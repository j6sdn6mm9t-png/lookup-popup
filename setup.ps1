<#
安装侧键查词：创建 Python 虚拟环境、安装依赖、下载并构建词典数据，然后启动。

    powershell -ExecutionPolicy Bypass -File .\setup.ps1             安装并启动
    powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Startup    同时设为开机自启
    加 -KeepDownloads 保留下载的原始数据（约 700MB，默认构建完删除）
#>
param([switch]$Startup, [switch]$KeepDownloads)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

# 找 Python 3.10–3.13（pywebview 依赖的 pythonnet 暂不支持更新的版本）
function Find-Python {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') { $candidates += , @('py', "-$v") }
    }
    $candidates += , @('python')
    $check = "import sys; print(sys.executable if (3, 10) <= sys.version_info[:2] <= (3, 13) else '')"
    foreach ($c in $candidates) {
        try {
            $exe = & $c[0] @($c | Select-Object -Skip 1) -c $check 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return "$exe".Trim() }
        } catch {}
    }
    return $null
}

function Find-AutoHotkey {
    $paths = @(
        "$env:LOCALAPPDATA\Programs\AutoHotkey\v2\AutoHotkey64.exe",
        "$env:ProgramFiles\AutoHotkey\v2\AutoHotkey64.exe"
    )
    return $paths | Where-Object { Test-Path $_ } | Select-Object -First 1
}

Step '检查 Python'
$python = Find-Python
if (-not $python) {
    Write-Host '没有找到 Python 3.10–3.13。可以用下面的命令安装后重新运行本脚本：' -ForegroundColor Yellow
    Write-Host '    winget install Python.Python.3.13'
    exit 1
}
Write-Host "使用 $python"

Step '检查 AutoHotkey v2'
$ahk = Find-AutoHotkey
if (-not $ahk) {
    Write-Host '没有找到 AutoHotkey v2。可以用下面的命令安装（安装完再运行本脚本即可启动）：' -ForegroundColor Yellow
    Write-Host '    winget install AutoHotkey.AutoHotkey'
} else {
    Write-Host "使用 $ahk"
}

Step '创建虚拟环境并安装依赖'
if (-not (Test-Path .venv\Scripts\python.exe)) { & $python -m venv .venv }
& .venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }

if (Test-Path data\dict.db) {
    Step '词典数据已存在，跳过构建（删除 data\dict.db 可重新构建）'
} else {
    Step '下载并构建词典数据（约 700MB 下载，10–20 分钟）'
    & .venv\Scripts\python.exe -X utf8 build_db.py
    if ($LASTEXITCODE -ne 0) { throw '构建词典数据失败，重新运行本脚本会从中断处继续' }
    if (-not $KeepDownloads) { Remove-Item download -Recurse -Force }
}

if (-not $ahk) { exit 0 }

if ($Startup) {
    Step '设为开机自启'
    $link = Join-Path ([Environment]::GetFolderPath('Startup')) 'LookUp Popup.lnk'
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
    $shortcut.TargetPath = $ahk
    $shortcut.Arguments = "`"$PSScriptRoot\lookup.ahk`""
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Save()
    Write-Host "已创建 $link"
}

Step '启动'
Start-Process $ahk "`"$PSScriptRoot\lookup.ahk`""
Write-Host '完成。把鼠标停在英文单词上，按鼠标前进侧键即可查词。' -ForegroundColor Green
Write-Host '想换按键：右键托盘里的 AutoHotkey 图标 → 更改查词按键…'
