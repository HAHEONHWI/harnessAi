#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: scripts/build.sh [--no-install] [--zip]
  (default)     build "HarnessLoop.app", install it to INSTALL_DIR (/Applications) and the engine to ~/.ai-harness/bin
  --no-install  build only (build/HarnessLoop.app)
  --zip         also create dist/HarnessLoop-macOS.zip for GitHub Releases
Env: VERSION (default: git tag or 0.1.0), INSTALL_DIR, ARCHS (default: "arm64 x86_64")
EOF
  exit 2
}

install=1
zip=0
for arg in "$@"; do
  case "$arg" in
    --no-install) install=0 ;;
    --zip) zip=1 ;;
    *) usage ;;
  esac
done

root="$(cd "$(dirname "$0")/.." && pwd)"
app="$root/build/HarnessLoop.app"
version="${VERSION:-$(git -C "$root" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || true)}"
version="${version:-0.1.0}"
archs=(${ARCHS:-arm64 x86_64})

rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" "$root/build/obj"

binaries=()
for arch in "${archs[@]}"; do
  out="$root/build/obj/HarnessLoop-$arch"
  swiftc -O -swift-version 5 -parse-as-library -target "$arch-apple-macos14.0" \
    "$root/app/HarnessMonitor.swift" -o "$out"
  binaries+=("$out")
done
lipo -create "${binaries[@]}" -output "$app/Contents/MacOS/HarnessLoop"
install -m 755 "$root/engine/harness.py" "$app/Contents/Resources/harness.py"

# App icon: app/AppIcon.png (1024x1024 master) -> AppIcon.icns
iconset="$root/build/obj/AppIcon.iconset"
rm -rf "$iconset" && mkdir -p "$iconset"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$root/app/AppIcon.png" --out "$iconset/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) "$root/app/AppIcon.png" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/AppIcon.icns"

cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>HarnessLoop</string>
  <key>CFBundleIdentifier</key><string>io.github.haheonhwi.aiharness</string>
  <key>CFBundleName</key><string>HarnessLoop</string>
  <key>CFBundleDisplayName</key><string>HarnessLoop</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

codesign --force --sign - "$app"
printf 'Built %s (v%s, %s)\n' "$app" "$version" "${archs[*]}"

if [[ "$zip" -eq 1 ]]; then
  mkdir -p "$root/dist"
  rm -f "$root/dist/HarnessLoop-macOS.zip"
  ditto -c -k --keepParent "$app" "$root/dist/HarnessLoop-macOS.zip"
  printf 'Packaged %s\n' "$root/dist/HarnessLoop-macOS.zip"
fi

if [[ "$install" -eq 1 ]]; then
  engine_dir="$HOME/.ai-harness/bin"
  mkdir -p "$engine_dir"
  install -m 755 "$root/engine/harness.py" "$engine_dir/harness.py"
  "$engine_dir/harness.py" init
  install_dir="${INSTALL_DIR:-/Applications}"
  rm -rf "$install_dir/HarnessLoop.app" "$install_dir/AI Harness.app"  # also drop the pre-rename app
  cp -R "$app" "$install_dir/"
  printf 'Installed %s/HarnessLoop.app\n' "$install_dir"
fi
