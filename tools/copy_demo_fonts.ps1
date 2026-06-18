# Copy demo LVGL fonts from K230 TF card to resource/font/
# Usage:
#   .\tools\copy_demo_fonts.ps1
#   .\tools\copy_demo_fonts.ps1 -Source "E:\CanMV Sample\Fonts"

param(
    [string]$Source = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DestDir = Join-Path $ProjectRoot "resource\font"

$DemoFonts = @(
    "lv_font_normal_size20_bpp4.bin",
    "lv_font_normal_size25_bpp4.bin",
    "lv_font_normal_bold_size25_bpp4.bin"
)

$ProjectMap = @{
    "font_title_20.bin"   = "lv_font_normal_size25_bpp4.bin"
    "font_body_18.bin"    = "lv_font_normal_size20_bpp4.bin"
    "font_caption_14.bin" = "lv_font_normal_size20_bpp4.bin"
}

function Find-SourceDir {
    param([string]$UserSource)
    if ($UserSource -and (Test-Path $UserSource)) {
        return (Resolve-Path $UserSource).Path
    }
    $candidates = @(
        "CanMV Sample\Fonts",
        "sdcard\CanMV Sample\Fonts"
    )
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Name -match '^[A-Z]$' })) {
        $root = "$($drive.Name):\"
        foreach ($rel in $candidates) {
            $p = Join-Path $root $rel
            if (Test-Path $p) {
                return (Resolve-Path $p).Path
            }
        }
    }
    return $null
}

New-Item -ItemType Directory -Force -Path $DestDir | Out-Null

$src = Find-SourceDir -UserSource $Source
if (-not $src) {
    Write-Host "[WARN] Demo font folder not found on this PC."
    Write-Host "Fonts are NOT in repo demo/; they live on board TF card:"
    Write-Host "  /sdcard/CanMV Sample/Fonts/"
    Write-Host "Mount TF card or connect board, then run:"
    Write-Host '  .\tools\copy_demo_fonts.ps1 -Source "E:\CanMV Sample\Fonts"'
    exit 1
}

Write-Host "Source: $src"
Write-Host "Dest:   $DestDir"

$copied = 0
foreach ($name in $DemoFonts) {
    $from = Join-Path $src $name
    if (-not (Test-Path $from)) {
        Write-Host "[SKIP] missing: $name"
        continue
    }
    Copy-Item -Force $from (Join-Path $DestDir $name)
    Write-Host "[OK] $name"
    $copied++
}

foreach ($projName in $ProjectMap.Keys) {
    $demoName = $ProjectMap[$projName]
    $from = Join-Path $DestDir $demoName
    if (-not (Test-Path $from)) {
        $from = Join-Path $src $demoName
    }
    if (Test-Path $from) {
        Copy-Item -Force $from (Join-Path $DestDir $projName)
        Write-Host "[MAP] $demoName -> $projName"
        $copied++
    }
}

if ($copied -eq 0) {
    Write-Host "[ERROR] No font files copied."
    exit 1
}

Write-Host "[DONE] $copied file(s) copied."
