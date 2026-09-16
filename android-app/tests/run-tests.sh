#!/usr/bin/env bash
# Suite de pruebas de PlayMe (app Android autonoma + backend portado).
#
#   bash android-app/tests/run-tests.sh            # solo host (simula el arranque Android)
#   bash android-app/tests/run-tests.sh --device   # anade pruebas en el movil por adb
#
# Host: arranca el MISMO backend portado que usa la app (playme_boot) en un puerto
# de prueba, con rutas temporales, y verifica: sincronizacion/reparacion de cookies,
# busqueda, reproduccion y stream. No toca el servidor de produccion (puerto 8191).
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PYDIR="$REPO/android-app/app/src/main/python"
APK=$(ls -t "$REPO"/android-app/dist/PlayMe-Local-*.apk 2>/dev/null | head -1)
PORT="${TEST_PORT:-8299}"
FAIL=0
ok(){ echo "PASS $1"; }
bad(){ echo "FAIL $1"; FAIL=$((FAIL+1)); }
chk(){ if [ "$2" = "1" ]; then ok "$1"; else bad "$1"; fi; }

echo "== PlayMe tests =="

# 1) APK: firma, package, ABIs, dex con playme_boot
BT=${ANDROID_HOME:-/opt/android-sdk}/build-tools/35.0.0
if [ -f "$APK" ]; then
  $BT/apksigner verify "$APK" >/dev/null 2>&1 && chk "apk firmado" 1 || chk "apk firmado" 0
  b=$($BT/aapt2 dump badging "$APK" 2>/dev/null)
  [ "$(echo "$b" | grep -c 'package: name=.com.riveros.playme.')" -ge 1 ] && chk "package com.riveros.playme" 1 || chk "package com.riveros.playme" 0
  for abi in arm64-v8a armeabi-v7a; do
    [ "$(echo "$b" | grep -c "$abi")" -ge 1 ] && chk "abi $abi" 1 || chk "abi $abi" 0
  done
  # v1.2.0: x86_64 fuera del release (solo servia para el emulador)
  [ "$(echo "$b" | grep -c 'x86_64')" -eq 0 ] && chk "sin x86_64 (APK mas pequeno)" 1 || chk "sin x86_64 (APK mas pequeno)" 0
  [ "$($BT/aapt2 dump xmltree --file AndroidManifest.xml "$APK" 2>/dev/null | grep -c 'minSdkVersion.*24')" -ge 1 ] && chk "minSdk 24" 1 || chk "minSdk 24" 0
else
  bad "apk presente"
fi

# 2) Codigo portado: sintaxis
cd "$PYDIR" || exit 1
python3 -m compileall -q . >/dev/null 2>&1 && chk "sintaxis python portado" 1 || chk "sintaxis python portado" 0

# 3) Host: arranque + busqueda + play + stream (mismo codigo que corre en el movil)
SIM=$(mktemp -d /tmp/playme-tests.XXXXXX)
mkdir -p "$SIM"
# cookie REAL sin cabecera Netscape: prueba la reparacion Y una sesion valida
REAL=/home/proyectos/Playme/cookies.txt
if [ -f "$REAL" ]; then sed '1,2d' "$REAL" > "$SIM/cookies.txt"; else printf '.youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tfake\n' > "$SIM/cookies.txt"; fi

PORT=$PORT PLAYME_TEST_DIR="$SIM" PLAYME_NO_CONVERT=1 PLAYME_NO_BG_DOWNLOAD=1 python3 - <<'PY' > "$SIM/boot.log" 2>&1 &
import os, time, playme_boot
os.environ["PLAYME_TEST_DIR"] = os.environ["PLAYME_TEST_DIR"]
print("start:", playme_boot.start(os.environ["PLAYME_TEST_DIR"]))
print("ytdlp:", playme_boot.ytdlp_version())
time.sleep(420)
PY
BOOTPID=$!
for i in $(seq 1 40); do
  curl -s -m 2 "http://127.0.0.1:$PORT/api/state" >/dev/null 2>&1 && break
  sleep 1
done
curl -s -m 10 "http://127.0.0.1:$PORT/api/state" | grep -q '"ok": true' && chk "servidor local arranca (puerto $PORT)" 1 || chk "servidor local arranca (puerto $PORT)" 0

# la cookie sin cabecera debe repararse al copiarse a la ruta de trabajo
TMPCK="$SIM/cache/playme_cookies.txt"
if [ -f "$TMPCK" ]; then
  head -1 "$TMPCK" | grep -q '# Netscape' && chk "cookie reparada (cabecera anadida)" 1 || chk "cookie reparada (cabecera anadida)" 0
else
  bad "cookie copiada a la ruta de trabajo"
fi

R=$(curl -s -m 120 -X POST "http://127.0.0.1:$PORT/api/search" -H 'Content-Type: application/json' -d '{"query":"Genesis Bereshit"}')
echo "$R" | grep -q '"title"' && chk "busqueda 'Genesis Bereshit' devuelve resultados" 1 || bad "busqueda 'Genesis Bereshit' devuelve resultados"
R=$(curl -s -m 120 -X POST "http://127.0.0.1:$PORT/api/search" -H 'Content-Type: application/json' -d '{"query":"lofi girl"}')
echo "$R" | grep -q '"title"' && chk "busqueda 'lofi girl' devuelve resultados" 1 || bad "busqueda 'lofi girl' devuelve resultados"

curl -s -m 60 -X POST "http://127.0.0.1:$PORT/api/play" -H 'Content-Type: application/json' -d '{"video_id":"dQw4w9WgXcQ"}' >/dev/null
# en Android el play descarga el audio (SABR): puede tardar; se espera hasta 180s
for i in $(seq 1 30); do
  sleep 6
  S=$(curl -s -m 15 "http://127.0.0.1:$PORT/api/state")
  echo "$S" | grep -q '"playing": true' && break
done
echo "$S" | grep -q '"playing": true' && chk "play activo" 1 || bad "play activo"
# en modo archivo el audio se esta descargando: esperar a que el cache tenga datos
# (antes se pedia el stream durante la descarga y daba 000)
for i in $(seq 1 20); do
  BY=$(curl -s -m 10 "http://127.0.0.1:$PORT/api/state" | grep -o '"cache_bytes": [0-9]*' | head -1 | tr -dc 0-9)
  [ "${BY:-0}" -gt 100000 ] && break
  sleep 5
done
C=000
for i in $(seq 1 6); do
  C=$(curl -s -m 30 -o /dev/null -w '%{http_code}' -r 0-65535 "http://127.0.0.1:$PORT/api/stream")
  [ "$C" = "206" ] && break
  sleep 5
done
[ "$C" = "206" ] && chk "stream HTTP 206" 1 || bad "stream HTTP 206 (got $C)"
# descarga de audio original (Android sin ffmpeg)
curl -s -m 30 -X POST "http://127.0.0.1:$PORT/api/convert" -H 'Content-Type: application/json' -d '{"video_id":"dQw4w9WgXcQ","title":"t"}' >/dev/null
for i in $(seq 1 40); do
  curl -s -m 10 -X POST "http://127.0.0.1:$PORT/api/conversions" -H 'Content-Type: application/json' -d '{}' | grep -q '"ready"' && break
  sleep 3
done
CODE=$(curl -s -m 180 -o "$SIM/dl.bin" -w '%{http_code}' "http://127.0.0.1:$PORT/api/download/audio/dQw4w9WgXcQ")
SZ=$(stat -c%s "$SIM/dl.bin" 2>/dev/null || echo 0)
if [ "$CODE" = "200" ] && [ "$SZ" -gt 100000 ]; then chk "descarga audio original ($SZ bytes)" 1; else bad "descarga audio original (http=$CODE sz=$SZ)"; tail -4 "$SIM/boot.log"; fi

kill $BOOTPID 2>/dev/null; pkill -f "playme_boot" 2>/dev/null

# 3b) v1.2.0 (sin red: solo codigo)
grep -q 'player_client=ios' transcoder.py && chk "transcoder usa el cliente ios" 1 || bad "transcoder usa el cliente ios"
grep -q '_strip_cookies' transcoder.py && chk "descarga sin cookies (ios no las soporta)" 1 || bad "descarga sin cookies"
grep -q 'cache_dir=None' playme_boot.py && chk "arranque con cache del sistema" 1 || bad "arranque con cache del sistema"
! grep -q 'print(' dns_java.py && chk "sin print en dns_java (no corrompe el JSON)" 1 || bad "sin print en dns_java"

# 4) Dispositivo (opcional)
if [ "${1:-}" = "--device" ]; then
  ADB=${ANDROID_HOME:-/opt/android-sdk}/platform-tools/adb
  [ -x "$ADB" ] || ADB=$(command -v adb || echo "$HOME/android-sdk/platform-tools/adb")
  if timeout 15 "$ADB" shell pidof com.riveros.playme >/dev/null 2>&1; then
    "$ADB" forward tcp:8291 tcp:8191 >/dev/null 2>&1
    curl -s -m 10 http://127.0.0.1:8291/api/state | grep -q '"ok": true' && chk "device: servidor local" 1 || bad "device: servidor local"
    curl -s -m 120 -X POST http://127.0.0.1:8291/api/search -H 'Content-Type: application/json' -d '{"query":"Genesis Bereshit"}' | grep -q '"title"' && chk "device: busqueda con resultados" 1 || bad "device: busqueda con resultados"
    [ "$(curl -s -m 30 -o /dev/null -w '%{http_code}' -r 0-65535 http://127.0.0.1:8291/api/stream)" = "206" ] && chk "device: stream 206" 1 || bad "device: stream 206"
  else
    echo "SKIP device (no conectado)"
  fi
fi

[ $FAIL -gt 0 ] && { echo "--- boot.log ($SIM) ---"; tail -20 "$SIM/boot.log"; }
echo "== resultado: $([ $FAIL -eq 0 ] && echo TODO-OK || echo "$FAIL fallos") =="
exit $FAIL
