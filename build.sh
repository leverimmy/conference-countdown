#!/bin/zsh

set -euo pipefail

COUNTDOWN_ROOT="${0:A:h}"
COUNTDOWN_BUILD_DIR="$COUNTDOWN_ROOT/build"
COUNTDOWN_APP_DIR="$COUNTDOWN_BUILD_DIR/Conference-Countdown.app"
COUNTDOWN_CONTENTS_DIR="$COUNTDOWN_APP_DIR/Contents"
COUNTDOWN_MACOS_DIR="$COUNTDOWN_CONTENTS_DIR/MacOS"
COUNTDOWN_RESOURCES_DIR="$COUNTDOWN_CONTENTS_DIR/Resources"
COUNTDOWN_DATA_DIR="$COUNTDOWN_RESOURCES_DIR/ConferenceData"
COUNTDOWN_ARCH_DIR="$COUNTDOWN_BUILD_DIR/Architectures"
COUNTDOWN_ARCH_LIST="${COUNTDOWN_ARCHS:-arm64 x86_64}"

python3 "$COUNTDOWN_ROOT/scripts/validate_data.py" --data-dir "$COUNTDOWN_ROOT/data"

rm -rf "$COUNTDOWN_APP_DIR" "$COUNTDOWN_ARCH_DIR"
mkdir -p "$COUNTDOWN_MACOS_DIR" "$COUNTDOWN_DATA_DIR" "$COUNTDOWN_ARCH_DIR"

COUNTDOWN_BINARIES=()
for COUNTDOWN_ARCH in ${=COUNTDOWN_ARCH_LIST}; do
    COUNTDOWN_ARCH_OUTPUT="$COUNTDOWN_ARCH_DIR/$COUNTDOWN_ARCH"
    COUNTDOWN_MODULE_CACHE="$COUNTDOWN_ARCH_DIR/ModuleCache-$COUNTDOWN_ARCH"
    mkdir -p "$COUNTDOWN_MODULE_CACHE"

    swiftc \
        -parse-as-library \
        -swift-version 5 \
        -module-name ConferenceCountdown \
        -target "$COUNTDOWN_ARCH-apple-macosx13.0" \
        -module-cache-path "$COUNTDOWN_MODULE_CACHE" \
        -O \
        -framework AppKit \
        -framework Combine \
        -framework CryptoKit \
        -framework Foundation \
        -framework ServiceManagement \
        -framework SwiftUI \
        -framework UserNotifications \
        "$COUNTDOWN_ROOT"/Sources/*.swift \
        -o "$COUNTDOWN_ARCH_OUTPUT"
    COUNTDOWN_BINARIES+=("$COUNTDOWN_ARCH_OUTPUT")
done

if (( ${#COUNTDOWN_BINARIES[@]} == 1 )); then
    cp "$COUNTDOWN_BINARIES[1]" "$COUNTDOWN_MACOS_DIR/ConferenceCountdown"
else
    lipo -create "${COUNTDOWN_BINARIES[@]}" -output "$COUNTDOWN_MACOS_DIR/ConferenceCountdown"
fi

cp "$COUNTDOWN_ROOT/Info.plist" "$COUNTDOWN_CONTENTS_DIR/Info.plist"
cp -R "$COUNTDOWN_ROOT/data/." "$COUNTDOWN_DATA_DIR/"
cp "$COUNTDOWN_ROOT/LICENSE" "$COUNTDOWN_RESOURCES_DIR/LICENSE.txt"

plutil -lint "$COUNTDOWN_CONTENTS_DIR/Info.plist"

codesign --force --deep --sign - "$COUNTDOWN_APP_DIR"
codesign --verify --deep --strict "$COUNTDOWN_APP_DIR"

echo "$COUNTDOWN_APP_DIR"
