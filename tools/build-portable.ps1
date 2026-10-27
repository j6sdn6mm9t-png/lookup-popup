<#
打包免安装版：自带嵌入式 Python、依赖库、AutoHotkey 和词典数据，解压后双击 LookUp.exe 即用。

    powershell -ExecutionPolicy Bypass -File .\tools\build-portable.ps1 -Version 1.0

前提：已运行过 setup.ps1（需要其中构建好的 data\dict.db），并安装了 AutoHotkey v2 和
Python 3.13（嵌入式 Python 的版本与本机 Python 一致，以保证依赖库的二进制兼容）。
输出：dist\LookUp-Popup-v<版本>-win64.zip
#>
param([Parameter(Mandatory)][string]$Version)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

if (-not (Test-Path data\dict.db)) { throw '找不到 data\dict.db，请先运行 setup.ps1' }
$ahkDir = @("$env:LOCALAPPDATA\Programs\AutoHotkey", "$env:ProgramFiles\AutoHotkey") |
    Where-Object { Test-Path "$_\v2\AutoHotkey64.exe" } | Select-Object -First 1
if (-not $ahkDir) { throw '找不到 AutoHotkey v2' }
$python = (& py -3.13 -c "import sys; print(sys.executable)" 2>$null)
if (-not $python) { $python = 'python' }
$pyVersion = (& $python -c "import platform; print(platform.python_version())").Trim()

$dist = Join-Path $root 'dist'
$out = Join-Path $dist 'LookUp-Popup'
$cache = Join-Path $dist 'cache'
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Force $out, $cache | Out-Null

Step "嵌入式 Python $pyVersion"
$embedZip = Join-Path $cache "python-$pyVersion-embed-amd64.zip"
if (-not (Test-Path $embedZip)) {
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip" -OutFile $embedZip -UseBasicParsing
}
Expand-Archive $embedZip "$out\python"
# 嵌入式 Python 默认不加载 site-packages，在 ._pth 里打开
$pth = Get-ChildItem "$out\python\python*._pth" | Select-Object -First 1
Set-Content $pth.FullName -Encoding ascii -Value @(
    (Get-Content $pth.FullName | Where-Object { $_ -notmatch '^#?\s*import site' }) + 'Lib\site-packages' + 'import site')

Step '安装依赖库'
& $python -m pip install --disable-pip-version-check -q --no-compile --target "$out\python\Lib\site-packages" `
    (Get-Content requirements.txt | Where-Object { $_ -match '^(pywebview|uiautomation)' })
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
Remove-Item "$out\python\Lib\site-packages\bin" -Recurse -Force -ErrorAction SilentlyContinue

Step '复制程序和数据'
# AutoHotkey 改名为 LookUp.exe 后，双击会自动运行同目录的 LookUp.ahk
Copy-Item "$ahkDir\v2\AutoHotkey64.exe" "$out\LookUp.exe"
Copy-Item lookup.ahk "$out\LookUp.ahk"
Copy-Item lookup.pyw, LICENSE $out
Copy-Item ui "$out\ui" -Recurse
New-Item -ItemType Directory "$out\data" | Out-Null
Copy-Item data\dict.db "$out\data\"
Copy-Item portable\* $out
New-Item -ItemType Directory "$out\licenses" | Out-Null
Copy-Item "$ahkDir\license.txt" "$out\licenses\AutoHotkey-GPL-2.0.txt"
Copy-Item "$out\python\LICENSE.txt" "$out\licenses\Python-PSF.txt"
Get-ChildItem $out -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

Step '压缩'
$zip = Join-Path $dist "LookUp-Popup-v$Version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive $out $zip -CompressionLevel Optimal
Write-Host ("完成：{0}（{1:N1} MB）" -f $zip, ((Get-Item $zip).Length / 1MB)) -ForegroundColor Green
