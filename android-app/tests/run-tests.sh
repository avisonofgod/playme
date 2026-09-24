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
if echo "$S" | grep -q '"playing": true'; then chk "play activo" 1
elif grep -qiE 'po_token|SABR|page needs to be reloaded' "$SIM/boot.log" 2>/dev/null; then
  echo "SKIP play activo (limite de YouTube: PO token/SABR, no es del codigo)"
else bad "play activo"; fi
# en modo archivo el audio se esta descargando: esperar a que el cache tenga datos
# (antes se pedia el stream durante la descarga y daba 000)
for i in $(seq 1 20); do
  BY=$(curl -s -m 10 "http://127.0.0.1:$PORT/api/state" | grep -o '"cache_bytes": [0-9]*' | head -1 | tr -dc 0-9)
  [ "${BY:-0}" -gt 100000 ] && break
  sleep 5
done
C=000
for i in $(seq 1 12); do
  # v1.3.0: con el archivo aun descargando el stream es progresivo (200, sin
  # Content-Length); ya completo responde 206 con Range.
  C=$(curl -s -m 60 -o /dev/null -w '%{http_code}' -r 0-65535 "http://127.0.0.1:$PORT/api/stream")
  { [ "$C" = "206" ] || [ "$C" = "200" ]; } && break
  sleep 6
done
if [ "$C" = "206" ]; then chk "stream HTTP 206 (completo/seek)" 1
elif [ "$C" = "200" ]; then chk "stream progresivo HTTP 200 (en vivo)" 1
elif grep -qiE 'po_token|SABR|page needs to be reloaded' "$SIM/boot.log" 2>/dev/null; then
  echo "SKIP stream (limite de YouTube: PO token/SABR, no es del codigo)"
else bad "stream HTTP 200/206 (got $C)"; fi
# descarga de audio original (Android sin ffmpeg)
curl -s -m 30 -X POST "http://127.0.0.1:$PORT/api/convert" -H 'Content-Type: application/json' -d '{"video_id":"dQw4w9WgXcQ","title":"t"}' >/dev/null
for i in $(seq 1 40); do
  curl -s -m 10 -X POST "http://127.0.0.1:$PORT/api/conversions" -H 'Content-Type: application/json' -d '{}' | grep -q '"ready"' && break
  sleep 3
done
CODE=$(curl -s -m 180 -o "$SIM/dl.bin" -w '%{http_code}' "http://127.0.0.1:$PORT/api/download/audio/dQw4w9WgXcQ")
SZ=$(stat -c%s "$SIM/dl.bin" 2>/dev/null || echo 0)
if [ "$CODE" = "200" ] && [ "$SZ" -gt 100000 ]; then chk "descarga audio original ($SZ bytes)" 1
elif grep -qiE 'po_token|SABR|page needs to be reloaded' "$SIM/boot.log" 2>/dev/null; then
  echo "SKIP descarga audio (limite de YouTube: PO token/SABR, no es del codigo)"
else bad "descarga audio original (http=$CODE sz=$SZ)"; tail -4 "$SIM/boot.log"; fi

kill $BOOTPID 2>/dev/null; pkill -f "playme_boot" 2>/dev/null

# 3b) v1.2.0 (sin red: solo codigo)
grep -q 'player_client=ios' transcoder.py && chk "transcoder usa el cliente ios" 1 || bad "transcoder usa el cliente ios"
grep -q '_strip_cookies' transcoder.py && chk "descarga sin cookies (ios no las soporta)" 1 || bad "descarga sin cookies"
grep -q 'cache_dir=None' playme_boot.py && chk "arranque con cache del sistema" 1 || bad "arranque con cache del sistema"
! grep -q 'print(' dns_java.py && chk "sin print en dns_java (no corrompe el JSON)" 1 || bad "sin print en dns_java"

# 3c) v1.3.0 (sin red)
grep -q 'def wait_partial' transcoder.py && chk "transcoder: arranque con los primeros KB" 1 || bad "transcoder: wait_partial"
grep -q 'def _serve_growing' server.py && chk "server: stream progresivo (sin esperar el 100%)" 1 || bad "server: _serve_growing"
grep -q 'def is_downloading' transcoder.py && chk "descarga en background no bloqueante" 1 || bad "is_downloading"
grep -q 'pedir OTRO tema corta el actual' player.py && chk "play de otro tema corta el actual" 1 || bad "corte al cambiar de tema"
# v1.3.0: al cambiar de tema solo queda en cache el ACTUAL (el anterior se borra)
grep -q 'def forget' transcoder.py && chk "transcoder: forget() borra el audio del tema abandonado" 1 || bad "forget() en transcoder"
grep -q 'def forget_except' transcoder.py && chk "transcoder: forget_except() deja solo el tema actual" 1 || bad "forget_except() en transcoder"
grep -q '_f(cur)' player.py && chk "player: abandonar un tema borra su cache (play/next/prev/stop)" 1 || bad "forget en player"
grep -q 'stopAudio' static/index.html && chk "UI: corta el audio al pedir otro tema" 1 || bad "UI stopAudio"
grep -q 'askConfirm' static/index.html && chk "UI: confirm propio (Vaciar descargas)" 1 || bad "UI: askConfirm"
! grep -q 'if (!confirm(' static/index.html && chk "UI: sin window.confirm (WebView)" 1 || bad "UI: sigue usando confirm()"
grep -q 'WebChromeClient' "$REPO/android-app/app/src/main/java/com/riveros/playme/MainActivity.java" && chk "app: dialogos JS (onJsConfirm) en el WebView" 1 || bad "app: WebChromeClient"
grep -q 'versionName = "1.3.0"' "$REPO/android-app/app/build.gradle.kts" && chk "version 1.3.0" 1 || bad "version 1.3.0"
# logica del player (sin red) y progresivo + limpieza de Descargas
( cd "$REPO/android-app/tools/linux-only" && PYTHONPATH="$PYDIR" python3 test_player.py > "$SIM/player.log" 2>&1 ) \
  && chk "player: cola/corte/progresivo (test_player)" 1 \
  || { bad "player: cola/corte/progresivo"; tail -8 "$SIM/player.log"; }
python3 "$REPO/android-app/tools/linux-only/test_progresivo.py" > "$SIM/prog.log" 2>&1 \
  && chk "reproduccion progresiva + clean/dl (test_progresivo)" 1 \
  || { bad "reproduccion progresiva + clean/dl"; tail -12 "$SIM/prog.log"; }

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
