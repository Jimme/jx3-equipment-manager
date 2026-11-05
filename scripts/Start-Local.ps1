param(
    [switch]$Install,
    [switch]$Build,
    [ValidateRange(1024, 65535)][int]$Port = 8000,
    [string]$PythonPath = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $venvPython)) {
        if (!$Install) { throw '首次运行请使用：.\scripts\Start-Local.ps1 -Install' }
        if ($PythonPath) { & $PythonPath -m venv .venv }
        elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3.12 -m venv .venv }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv .venv }
        else { throw '请先安装 Python 3.12，或通过 -PythonPath 指定 python.exe。' }
        if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败。请确认安装了完整的 Python 3.12。' }
    }
    function Invoke-ProjectNpm {
        param([string[]]$NpmArgs)
        if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { & npm.cmd @NpmArgs }
        elseif ((Test-Path '../.tools/package/bin/npm-cli.js') -and (Get-Command node -ErrorAction SilentlyContinue)) { & node ../.tools/package/bin/npm-cli.js @NpmArgs }
        else { throw '请安装 Node.js 22 LTS（包含 npm），然后重新打开 PowerShell。' }
        if ($LASTEXITCODE -ne 0) { throw '前端安装或构建失败，请查看上方错误。' }
    }
    if ($Install) {
        & $venvPython -m pip install -r requirements-dev.txt -c requirements.lock
        if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败。' }
        Push-Location frontend
        try { Invoke-ProjectNpm -NpmArgs @('ci', '--no-audit', '--no-fund') }
        finally { Pop-Location }
    }
    if ($Install -or $Build -or !(Test-Path 'frontend/dist/index.html')) {
        Push-Location frontend
        try { Invoke-ProjectNpm -NpmArgs @('run', 'build') }
        finally { Pop-Location }
    }
    Write-Host "打开浏览器访问 http://127.0.0.1:$Port （停止：Ctrl+C）"
    & $venvPython -m uvicorn backend.main:app --host 127.0.0.1 --port $Port --workers 1
    if ($LASTEXITCODE -ne 0) { throw '应用启动失败，请查看上方日志。' }
}
finally { Pop-Location }
