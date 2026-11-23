#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
sdk="${ANDROID_SDK_ROOT:-$root/artifacts/android-sdk}"
tools="$sdk/build-tools/34.0.0"
platform="$sdk/platforms/android-34/android.jar"
out="$root/artifacts/android"
source_dir="$root/android"
mkdir -p "$out"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/classes" "$work/dex"
"$tools/aapt2" compile --dir "$source_dir/res" -o "$work/resources.zip"
"$tools/aapt2" link -I "$platform" --manifest "$source_dir/AndroidManifest.xml" \
  -o "$work/resources.apk" "$work/resources.zip"
javac -encoding UTF-8 --release 8 -classpath "$platform" \
  -d "$work/classes" "$source_dir"/src/edu/aiia/glasses/*.java
jar cf "$work/classes.jar" -C "$work/classes" .
"$tools/d8" --min-api 28 --lib "$platform" --output "$work/dex" "$work/classes.jar"
(cd "$work/dex" && zip -q "$work/resources.apk" classes.dex)
"$tools/zipalign" -f 4 "$work/resources.apk" "$work/aligned.apk"
key="$out/3800-debug.jks"
if [[ ! -f "$key" ]]; then
  (umask 077 && keytool -genkeypair -keystore "$key" -storepass android -keypass android \
    -alias androiddebugkey -dname 'CN=3800 Local Development' -keyalg RSA -keysize 2048 -validity 10000)
fi
"$tools/apksigner" sign --ks "$key" --ks-pass pass:android --out "$out/3800-mobile.apk" "$work/aligned.apk"
"$tools/apksigner" verify --verbose "$out/3800-mobile.apk"
