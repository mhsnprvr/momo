#!/bin/bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

"$root/.venv/bin/pip" install pyinstaller
"$root/.venv/bin/python" packaging/fetch_model.py
"$root/.venv/bin/pyinstaller" packaging/MoMo.spec --noconfirm
rm -rf "$root/dist/MoMo"

app="$root/dist/MoMo.app"
macos="$app/Contents/MacOS"
frameworks="$app/Contents/Frameworks"
mkdir -p "$frameworks"

"$root/.venv/bin/python" packaging/bundle_macho.py "$(command -v ffmpeg)" "$macos" "$frameworks"
"$root/.venv/bin/python" packaging/bundle_macho.py "$(command -v mpv)" "$macos" "$frameworks"
codesign --force --deep --sign - "$app"

stage="$root/dist/dmg-root"
rm -rf "$stage"
mkdir -p "$stage"
ditto "$app" "$stage/MoMo.app"
ln -s /Applications "$stage/Applications"

rm -f "$root/dist/MoMo.dmg"
hdiutil create -volname "MoMo" -srcfolder "$stage" -ov -format UDZO "$root/dist/MoMo.dmg"
rm -rf "$stage"
echo "Built $root/dist/MoMo.dmg"
