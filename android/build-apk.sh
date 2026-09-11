#!/usr/bin/env bash
# Compila el APK WebView de PlayMe sin Gradle (aapt2 + javac + d8 + zipalign + apksigner).
#
# Uso:  bash android/build-apk.sh
# Requisitos: JDK 17+ (javac), Android SDK en $ANDROID_SDK (por defecto /opt/android-sdk)
#             con build-tools 35.0.0 y platform android-35.
# Salida: android/dist/PlayMe-1.0.apk (firmado con android/playme-release.keystore)
#
# El keystore y keystore.properties NO se versionan: respárdalos; sin ellos no podrás
# firmar actualizaciones como la misma app.
set -e

SDK=${ANDROID_SDK:-/opt/android-sdk}
BT=$SDK/build-tools/35.0.0
AJAR=$SDK/platforms/android-35/android.jar
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC=$HERE
OUT=$(mktemp -d /tmp/playme_build.XXXXXX)
KEY=$SRC/playme-release.keystore
PROPS=$SRC/keystore.properties
VERSION_NAME=1.0
VERSION_CODE=1

[ -x "$BT/aapt2" ] || { echo "ERROR: aapt2 no encontrado en $BT"; exit 1; }
[ -f "$AJAR" ] || { echo "ERROR: android.jar no encontrado en $AJAR"; exit 1; }
command -v javac >/dev/null || { echo "ERROR: javac no está en PATH (instala un JDK)"; exit 1; }

mkdir -p "$OUT/gen" "$OUT/classes" "$OUT/dex"
cd "$SRC"

echo "== aapt2 compile/link =="
"$BT/aapt2" compile --dir res -o "$OUT/res.zip"
"$BT/aapt2" link -o "$OUT/base.apk" -I "$AJAR" --manifest AndroidManifest.xml \
  -R "$OUT/res.zip" --auto-add-overlay --java "$OUT/gen" \
  --min-sdk-version 24 --target-sdk-version 35 \
  --version-code $VERSION_CODE --version-name $VERSION_NAME

echo "== javac =="
javac -source 11 -target 11 -classpath "$AJAR" -d "$OUT/classes" \
  src/playme/webview/MainActivity.java "$OUT/gen/playme/webview/R.java" 2>&1 \
  | grep -viE 'warning|bootstrap' || true
ls "$OUT/classes/playme/webview/" >/dev/null

echo "== d8 =="
"$BT/d8" --release --lib "$AJAR" --min-api 24 --output "$OUT/dex" \
  $(find "$OUT/classes" -name '*.class')

echo "== empaquetar (classes.dex) =="
python3 - "$OUT" <<'PY'
import sys, zipfile, shutil, os
out = sys.argv[1]
shutil.copy(os.path.join(out, 'base.apk'), os.path.join(out, 'app.apk'))
with zipfile.ZipFile(os.path.join(out, 'app.apk'), 'a', zipfile.ZIP_DEFLATED) as z:
    z.write(os.path.join(out, 'dex', 'classes.dex'), 'classes.dex')
PY
"$BT/zipalign" -f 4 "$OUT/app.apk" "$OUT/app-aligned.apk"

echo "== firma =="
if [ ! -f "$KEY" ]; then
  P=$(openssl rand -base64 15 | tr -d '/+=' | cut -c1-20)
  keytool -genkeypair -keystore "$KEY" -alias playme -keyalg RSA -keysize 2048 \
    -validity 10000 -storepass "$P" -keypass "$P" \
    -dname "CN=Riveros, O=Riveros, L=San Luis Potosi, ST=San Luis Potosi, C=MX"
  printf 'storeFile=playme-release.keystore\nstorePassword=%s\nkeyPassword=%s\nkeyAlias=playme\n' "$P" "$P" > "$PROPS"
  echo "Keystore nuevo creado: $KEY"
  echo "CONTRASEÑA (guárdala): $P"
  echo "  respaldo en: $PROPS"
fi
P=$(grep '^storePassword=' "$PROPS" | cut -d= -f2)

mkdir -p "$SRC/dist"
"$BT/apksigner" sign --ks "$KEY" --ks-key-alias playme --ks-pass "pass:$P" --key-pass "pass:$P" \
  --out "$SRC/dist/PlayMe-$VERSION_NAME.apk" "$OUT/app-aligned.apk"
"$BT/apksigner" verify "$SRC/dist/PlayMe-$VERSION_NAME.apk" && echo "FIRMA_OK"
"$BT/aapt2" dump badging "$SRC/dist/PlayMe-$VERSION_NAME.apk" | head -3
ls -la "$SRC/dist/PlayMe-$VERSION_NAME.apk"
echo "BUILD_APK_OK"
