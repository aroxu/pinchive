#!/usr/bin/env bash
# Fetch vendored front-end assets (htmx + self-hosted webfonts).
# Usage: bash scripts/fetch_assets.sh [DEST_DIR]
# DEST_DIR defaults to ./static (repo layout). In Docker it is /assets.
#
# htmx is required (build fails without it). Fonts are best-effort:
# the app falls back to system fonts (Segoe UI / Malgun Gothic, -apple-system)
# if a font file is missing, so a font download failure only degrades polish.
set -u

DEST="${1:-static}"
JS_OUT="$DEST"
FONT_OUT="$DEST/fonts"

# When called with the repo ./static layout, js lives under js/.
if [ "$DEST" = "static" ]; then
  JS_OUT="$DEST/js"
fi

mkdir -p "$JS_OUT" "$FONT_OUT"

echo ">> htmx (required)"
curl -fsSL "https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js" -o "$JS_OUT/htmx.min.js" || {
  echo "!! FATAL: could not download htmx" >&2
  exit 1
}

# idiomorph: DOM-morphing swap ('hx-swap="morph"'). Lets the live download poll
# update only what changed instead of replacing the whole grid — so videos don't
# reload/flicker and keep playing. Ships the htmx extension registration too.
echo ">> idiomorph (required)"
curl -fsSL "https://unpkg.com/idiomorph@0.3.0/dist/idiomorph-ext.min.js" -o "$JS_OUT/idiomorph-ext.min.js" || {
  echo "!! FATAL: could not download idiomorph" >&2
  exit 1
}

fetch_font() {
  local name="$1" url="$2"
  echo ">> font: $name (optional)"
  if ! curl -fsSL "$url" -o "$FONT_OUT/$name"; then
    echo "   (skipped $name — system fallback will be used)" >&2
    rm -f "$FONT_OUT/$name"
  fi
}

# Inter — variable font, single file covers all weights.
fetch_font "Inter.var.woff2" \
  "https://rsms.me/inter/font-files/InterVariable.woff2"

# JetBrains Mono (regular) for code windows.
fetch_font "JetBrainsMono-Regular.woff2" \
  "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/webfonts/JetBrainsMono-Regular.woff2"

# Pretendard — Korean fallback (dynamic subset regular + bold).
fetch_font "Pretendard-Regular.woff2" \
  "https://github.com/orioncactus/pretendard/raw/main/packages/pretendard/dist/web/static/woff2/Pretendard-Regular.woff2"
fetch_font "Pretendard-Bold.woff2" \
  "https://github.com/orioncactus/pretendard/raw/main/packages/pretendard/dist/web/static/woff2/Pretendard-Bold.woff2"

# Noto Sans KR (본고딕) — Korean primary. Google's static hinted woff2.
# Fragile mirror; if it fails, Pretendard/system Korean font takes over.
fetch_font "NotoSansKR-Regular.woff2" \
  "https://cdn.jsdelivr.net/gh/fontsource/font-files/fonts/google/noto-sans-kr/files/noto-sans-kr-korean-400-normal.woff2"
fetch_font "NotoSansKR-Bold.woff2" \
  "https://cdn.jsdelivr.net/gh/fontsource/font-files/fonts/google/noto-sans-kr/files/noto-sans-kr-korean-700-normal.woff2"

echo ">> done. assets in: $DEST"
