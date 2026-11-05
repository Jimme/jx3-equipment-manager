param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference='Stop'
$sourceRoot=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$targetRoot=[IO.Path]::GetFullPath($Destination)
if ($targetRoot.TrimEnd('\') -eq $sourceRoot.TrimEnd('\')) { throw '部署目录不能与开发项目根目录相同。' }
$forbidden=@((Join-Path $sourceRoot 'data'),(Join-Path $sourceRoot '.venv'),(Join-Path $sourceRoot 'frontend\node_modules'))
foreach ($path in $forbidden) {
    if ($targetRoot.TrimEnd('\') -eq $path -or $targetRoot.StartsWith($path+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '部署目录不能位于数据或依赖目录内。' }
}
[IO.Directory]::CreateDirectory($targetRoot) | Out-Null
$files=[Collections.Generic.List[string]]::new()
foreach ($name in @('Dockerfile','compose.yaml','compose.hub.yaml','compose.build.yaml','.dockerignore','.env.example','alembic.ini','requirements.txt','requirements-dev.txt','requirements.lock','pytest.ini','README.md','JX3_Tank_Equipment_Manager_PRD.md','群晖部署.md')) { $files.Add($name) }
foreach ($item in Get-ChildItem (Join-Path $sourceRoot 'backend') -File -Recurse -Filter '*.py') { $files.Add($item.FullName.Substring($sourceRoot.Length+1)) }
foreach ($folder in @('frontend\src','frontend\tests','scripts','docs')) {
    foreach ($item in Get-ChildItem (Join-Path $sourceRoot $folder) -File -Recurse) {
        if ($item.Extension -in @('.ts','.tsx','.css','.ps1','.py','.md','.mjs','.html','.svg','.png')) { $files.Add($item.FullName.Substring($sourceRoot.Length+1)) }
    }
}
foreach ($item in Get-ChildItem (Join-Path $sourceRoot 'frontend') -File) {
    if ($item.Extension -in @('.json','.ts','.mjs','.html')) { $files.Add($item.FullName.Substring($sourceRoot.Length+1)) }
}
function Get-DeploymentHash([string]$FilePath) {
    $stream=[IO.File]::OpenRead($FilePath)
    $algorithm=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','') }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}
$manifest=@()
foreach ($relative in ($files | Sort-Object -Unique)) {
    if ($relative -match '(^|[\\/])(data|node_modules|\.venv|\.tools|__pycache__)([\\/]|$)' -or $relative -match '\.sqlite3') { throw ('禁止包含运行数据或依赖：'+$relative) }
    $source=Join-Path $sourceRoot $relative
    $target=Join-Path $targetRoot $relative
    [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
    $preserveCompose=($relative -eq 'compose.yaml' -and (Test-Path -LiteralPath $target))
    if (!$preserveCompose) { Copy-Item -LiteralPath $source -Destination $target -Force }
    $sourceHash=(Get-DeploymentHash $source)
    $targetHash=(Get-DeploymentHash $target)
    if (!$preserveCompose -and $sourceHash -ne $targetHash) { throw ('部署文件校验失败：'+$relative) }
    $manifest+=@{path=$relative.Replace('\','/');sha256=$targetHash;source_sha256=$sourceHash}
    if ($preserveCompose) { Write-Output '保留目标现有 compose.yaml；新版参考配置见 compose.reference.yaml。' }
}
$reference=Join-Path $targetRoot 'compose.reference.yaml'
Copy-Item -LiteralPath (Join-Path $sourceRoot 'compose.yaml') -Destination $reference -Force
$referenceHash=Get-DeploymentHash $reference
if ($referenceHash -ne (Get-DeploymentHash (Join-Path $sourceRoot 'compose.yaml'))) { throw '参考 Compose 配置校验失败。' }
$manifest+=@{path='compose.reference.yaml';sha256=$referenceHash;source_sha256=$referenceHash}
$version=(Get-Content -LiteralPath (Join-Path $sourceRoot 'frontend\package.json') -Raw | ConvertFrom-Json).version
$environment=Join-Path $targetRoot '.env'
if (!(Test-Path -LiteralPath $environment)) { Copy-Item -LiteralPath (Join-Path $sourceRoot '.env.example') -Destination $environment }
[IO.Directory]::CreateDirectory((Join-Path $targetRoot 'data')) | Out-Null
@{version=$version;prepared_at=[DateTime]::UtcNow.ToString('o');files=$manifest;note='Application source only. Existing compose.yaml, .env and data are preserved; no development database is copied.'} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $targetRoot 'DEPLOYMENT-MANIFEST.json') -Encoding UTF8
Write-Output ('部署文件已复制并逐文件校验：'+$manifest.Count+' 个文件。')
Write-Output ('目标：'+$targetRoot)
Write-Output '使用 compose.yaml 建立群晖项目。已有 compose.yaml、.env 和 data 内容均保留，不复制本机试用数据库。'
