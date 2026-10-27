# 构建 UefiAnimWizard.exe（PyInstaller 单文件，带管理员清单）
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Clean      # 先清 dist/build
#
# 产物：dist\UefiAnimWizard.exe（双击 → UAC 提权 → 图形向导）
#
# 依赖：Python 3.10+、pip install pillow pyinstaller

param(
    [switch]$Clean,
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# ---- 找 Python ----
if (-not $Python) {
    $cand = @(@(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python312\python.exe"
    ) | Where-Object { Test-Path $_ })
    if ($cand.Count -gt 0) { $Python = [string]$cand[0] } else { $Python = 'python' }
}
Write-Host "[build] python = $Python"
& $Python -c "import sys; print('  ' + sys.version.split()[0])"

# ---- 依赖 ----
& $Python -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[build] 安装 Pillow…'
    & $Python -m pip install --disable-pip-version-check pillow
}
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[build] 安装 PyInstaller…'
    & $Python -m pip install --disable-pip-version-check pyinstaller
}

if ($Clean) {
    Write-Host '[build] 清理 build/ dist/'
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

# ---- 自检（构建前先保证逻辑没问题）----
Write-Host '[build] 运行自检…'
& $Python -X utf8 -m wizard --selftest
if ($LASTEXITCODE -ne 0) { Write-Host '[build] 自检未通过，终止构建。'; exit 1 }

# ---- PyInstaller ----
$excludes = @(
    'numpy', 'pandas', 'scipy', 'matplotlib', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
    'notebook', 'IPython', 'pytest', 'setuptools', 'pip', 'wheel'
) | ForEach-Object { "--exclude-module=$_" }

$pyArgs = @(
    '-m', 'PyInstaller',
    '--noconfirm', '--clean', '--onefile', '--windowed',
    '--uac-admin',
    '--name', 'UefiAnimWizard',
    '--icon', 'wizard\assets\wizard.ico',
    '--version-file', 'wizard\assets\version_info.txt',
    '--add-data', 'wizard\assets;assets',
    '--add-data', 'wizard\tests;tests',
    '--collect-submodules', 'PIL',
    '--hidden-import', 'PIL.ImageTk',
    '--hidden-import', 'PIL._tkinter_finder'
) + $excludes + @('run_wizard.py')

Write-Host '[build] PyInstaller…'
& $Python @pyArgs
if ($LASTEXITCODE -ne 0) { Write-Host '[build] PyInstaller 构建失败。'; exit 1 }

$exe = Join-Path $root 'dist\UefiAnimWizard.exe'
if (-not (Test-Path $exe)) { Write-Host "[build] 未生成 $exe"; exit 1 }
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ''
Write-Host "[build] 完成：$exe（$mb MB）"
Write-Host '[build] 下一步：'
Write-Host '        .\dist\UefiAnimWizard.exe --selftest      # 冻结后自检'
Write-Host '        .\dist\UefiAnimWizard.exe                 # 打开向导（会请求管理员）'
exit 0
