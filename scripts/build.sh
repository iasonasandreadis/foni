#!/bin/bash
# Builds build/Foni.app and build/Foni-<version>.dmg.
# Needs: an Apple Silicon Mac and Xcode Command Line Tools (xcode-select --install).
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' foni/__init__.py)
# A self-contained Python (python-build-standalone), pinned and checked.
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.12.16%2B20261110-aarch64-apple-darwin-install_only_stripped.tar.gz"
PY_SHA256="2f11b8aa3726bdbd87dd9eb76568412faddf4f47e26913fa45d6a2e424972acd"

BUILD=build
APP="$BUILD/Foni.app"
RES="$APP/Contents/Resources"
PYV=3.12

echo "• Building Foni $VERSION"
rm -rf "$APP" "$BUILD/dmg"
mkdir -p "$APP/Contents/MacOS" "$RES/app" "$BUILD/cache"

echo "• Python"
TGZ="$BUILD/cache/python-$PYV.tar.gz"
[ -f "$TGZ" ] || curl -fsSL "$PY_URL" -o "$TGZ"
echo "$PY_SHA256  $TGZ" | shasum -a 256 -c - >/dev/null
tar -xzf "$TGZ" -C "$RES"            # -> $RES/python
PY="$RES/python/bin/python3"

echo "• Packages (a few minutes the first time)"
"$PY" -m pip install --quiet --no-cache-dir --no-warn-script-location -r requirements.txt

echo "• Foni's code"
cp -R foni "$RES/app/foni"
find "$RES/app" -name __pycache__ -prune -exec rm -rf {} +

echo "• Trimming"
SP="$RES/python/lib/python$PYV/site-packages"
rm -rf "$RES/python/lib/python$PYV/test" "$RES/python/lib/python$PYV/idlelib" \
       "$RES/python/lib/python$PYV/tkinter" "$RES/python/lib/python$PYV/turtledemo" \
       "$RES/python/lib/tcl"* "$RES/python/lib/tk"* "$RES/python/lib/itcl"* "$RES/python/lib/thread"* \
       "$SP/pip" "$SP/pip-"* "$RES/python/bin/pip"* "$RES/python/share"
find "$SP" -type d \( -name tests -o -name test \) -prune -exec rm -rf {} +
find "$RES/python" -name __pycache__ -prune -exec rm -rf {} +
# precompile everything once, so Foni starts fast and never writes into the app
"$PY" -m compileall -q -j 0 "$RES/app" "$RES/python/lib/python$PYV" >/dev/null || true

echo "• Launcher"
clang -arch arm64 -mmacosx-version-min=14.0 -O2 packaging/launcher.c \
    -I "$RES/python/include/python$PYV" -L "$RES/python/lib" -lpython$PYV \
    -Wl,-rpath,@executable_path/../Resources/python/lib -o "$APP/Contents/MacOS/Foni"

echo "• Icon and Info.plist"
"$PY" packaging/make_icon.py "$RES/Foni.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Foni</string>
  <key>CFBundleDisplayName</key><string>Foni</string>
  <key>CFBundleIdentifier</key><string>com.jayandreadis.foni</string>
  <key>CFBundleExecutable</key><string>Foni</string>
  <key>CFBundleIconFile</key><string>Foni</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>NSMicrophoneUsageDescription</key>
  <string>Foni listens only while you hold your dictation key, and turns your speech into text on this Mac.</string>
  <key>NSHumanReadableCopyright</key><string>MIT License. github.com/iasonasandreadis/foni</string>
</dict>
</plist>
PLIST

# Sign with a stable identity when this Mac has it ("Foni Developer", the release key), so
# macOS keeps the permissions you gave Foni across updates. Anyone else's build is ad-hoc signed
# (then macOS asks for the permissions again after each rebuild).
SIGN="-"
if security find-identity -p codesigning 2>/dev/null | grep -q '"Foni Developer"'; then SIGN="Foni Developer"; fi
echo "• Signing ($([ "$SIGN" = "-" ] && echo ad-hoc || echo "$SIGN"))"
find "$RES" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 | xargs -0 codesign --force --sign "$SIGN" 2>/dev/null
find "$RES/python/bin" -type f -perm +111 -print0 | xargs -0 codesign --force --sign "$SIGN" 2>/dev/null || true
codesign --force --sign "$SIGN" --identifier com.jayandreadis.foni "$APP"
codesign --verify --deep --strict "$APP"

echo "• Disk image"
mkdir -p "$BUILD/dmg"
cp -R "$APP" "$BUILD/dmg/"
ln -s /Applications "$BUILD/dmg/Applications"
rm -f "$BUILD/Foni-$VERSION.dmg"
hdiutil create -quiet -volname "Foni" -srcfolder "$BUILD/dmg" -fs HFS+ -format ULMO "$BUILD/Foni-$VERSION.dmg"
rm -rf "$BUILD/dmg"

echo "✓ $APP ($(du -sh "$APP" | cut -f1)) and $BUILD/Foni-$VERSION.dmg ($(du -sh "$BUILD/Foni-$VERSION.dmg" | cut -f1))"
