param([switch]$Browser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
$originalTemp = $env:TEMP
$originalTmp = $env:TMP
try {
    $testRoot = Join-Path $projectRoot '.tools'
    $taskTemp = Join-Path $testRoot 'temp'
    New-Item -ItemType Directory -Force $taskTemp | Out-Null
    $env:TEMP = $taskTemp
    $env:TMP = $taskTemp
    $testPath = Join-Path $testRoot ('pytest-' + [Guid]::NewGuid().ToString('N'))
    & '.\.venv\Scripts\python.exe' -m pytest -q --basetemp $testPath
    if ($LASTEXITCODE -ne 0) { throw '后端测试未通过。' }
    Push-Location frontend
    try {
        function Invoke-ProjectNpm {
            param([string[]]$NpmArgs)
            if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { & npm.cmd @NpmArgs }
            elseif (Test-Path '../.tools/package/bin/npm-cli.js') { & node ../.tools/package/bin/npm-cli.js @NpmArgs }
            else { throw '找不到 npm，请安装 Node.js 22 LTS。' }
            if ($LASTEXITCODE -ne 0) { throw '前端检查未通过。' }
        }
        Invoke-ProjectNpm -NpmArgs @('run', 'build')
        Invoke-ProjectNpm -NpmArgs @('test')
        if ($Browser) { Invoke-ProjectNpm -NpmArgs @('run', 'test:e2e') }
    }
    finally { Pop-Location }
}
finally {
    $env:TEMP = $originalTemp
    $env:TMP = $originalTmp
    Pop-Location
}
