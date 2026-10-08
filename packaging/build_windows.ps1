$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
$version = if ($env:MOMO_VERSION) { $env:MOMO_VERSION } else { "1.0.0" }

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command exited with code $LASTEXITCODE" }
}

Invoke-Checked $python @("-m", "pip", "install", "pyinstaller")
Invoke-Checked $python @("packaging\fetch_model.py")
Invoke-Checked $python @("-m", "PyInstaller", "packaging\MoMo.spec", "--noconfirm")

$app = Join-Path $root "dist\MoMo"
$downloads = Join-Path $root "build\tools"
New-Item -ItemType Directory -Force -Path $downloads | Out-Null

$headers = @{ "User-Agent" = "momo-build" }
if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
$release = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"

function Get-Tool {
    param([string]$Pattern, [string]$Name)
    $asset = $release.assets | Where-Object { $_.name -match $Pattern } | Select-Object -First 1
    if (-not $asset) { throw "No $Name download matched $Pattern" }
    $archive = Join-Path $downloads $asset.name
    Invoke-WebRequest -Headers $headers -Uri $asset.browser_download_url -OutFile $archive
    $target = Join-Path $downloads $Name
    Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
    Invoke-Checked "7z" @("x", "-y", "-o$target", $archive)
    return $target
}

$mpv = Get-Tool '^mpv-x86_64-\d{8}-git-[0-9a-f]+\.7z$' "mpv"
$ffmpeg = Get-Tool '^ffmpeg-x86_64-git-[0-9a-f]+\.7z$' "ffmpeg"

Copy-Item (Join-Path $mpv "mpv.exe") $app
Get-ChildItem $mpv -Filter "*.dll" | Copy-Item -Destination $app
foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
    $found = Get-ChildItem $ffmpeg -Recurse -Filter $name | Select-Object -First 1
    if ($found) { Copy-Item $found.FullName $app }
}
if (-not (Test-Path (Join-Path $app "ffmpeg.exe"))) { throw "ffmpeg.exe was not found in the download" }

$iscc = (Get-Command "iscc" -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe" }
Invoke-Checked $iscc @("/Qp", "/DAppVersion=$version", "packaging\MoMo.iss")
Write-Host "Built $root\dist\MoMo-Setup.exe"
