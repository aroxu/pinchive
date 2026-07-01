# Fetch vendored front-end assets on Windows (local dev).
# Usage: powershell -File scripts/fetch_assets.ps1
# Mirrors scripts/fetch_assets.sh. htmx required; fonts best-effort.

$ErrorActionPreference = "Stop"
$jsOut = "static/js"
$fontOut = "static/fonts"
New-Item -ItemType Directory -Force -Path $jsOut, $fontOut | Out-Null

function Get-Asset($url, $out, $required) {
    try {
        Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
        Write-Host ">> ok: $out"
    } catch {
        if ($required) {
            Write-Error "FATAL: could not download $url"
            exit 1
        }
        Write-Host "   (skipped $out - system fallback used)" -ForegroundColor Yellow
        if (Test-Path $out) { Remove-Item $out }
    }
}

Get-Asset "https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js" "$jsOut/htmx.min.js" $true
Get-Asset "https://rsms.me/inter/font-files/InterVariable.woff2" "$fontOut/Inter.var.woff2" $false
Get-Asset "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/webfonts/JetBrainsMono-Regular.woff2" "$fontOut/JetBrainsMono-Regular.woff2" $false
Get-Asset "https://github.com/orioncactus/pretendard/raw/main/packages/pretendard/dist/web/static/woff2/Pretendard-Regular.woff2" "$fontOut/Pretendard-Regular.woff2" $false
Get-Asset "https://github.com/orioncactus/pretendard/raw/main/packages/pretendard/dist/web/static/woff2/Pretendard-Bold.woff2" "$fontOut/Pretendard-Bold.woff2" $false
Get-Asset "https://cdn.jsdelivr.net/gh/fontsource/font-files/fonts/google/noto-sans-kr/files/noto-sans-kr-korean-400-normal.woff2" "$fontOut/NotoSansKR-Regular.woff2" $false
Get-Asset "https://cdn.jsdelivr.net/gh/fontsource/font-files/fonts/google/noto-sans-kr/files/noto-sans-kr-korean-700-normal.woff2" "$fontOut/NotoSansKR-Bold.woff2" $false

Write-Host ">> done."
