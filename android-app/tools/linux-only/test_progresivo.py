"""v1.3.0 — reproduccion progresiva (arranque sin esperar el 100%) y limpieza de Descargas.

Sin red ni yt-dlp: un FakeResolver escribe el audio "descargandolo" por trozos con
retardo, como yt-dlp. Verifica:
  1. download_bg() retorna YA (no bloquea hasta el 100%)
  2. wait_partial() devuelve en cuanto hay los primeros KB
  3. POST /api/play -> playing=true con mode=file ANTES de tener el archivo completo
  4. GET /api/stream sirve bytes MIENTRAS se descarga y termina con el archivo completo
  5. POST /api/clean/dl vacia la lista de Descargas (boton "Vaciar descargas")
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "..", "app", "src", "main", "python")))

TMP = tempfile.mkdtemp(prefix="playme-prog.")
PORT = int(os.environ.get("PROG_TEST_PORT", "8391"))
VID = "progtest01"
TOTAL = 3 * 1024 * 1024          # 3 MB
CHUNK = 128 * 1024
CHUNK_DELAY = 0.15               # ~3.5 s de "descarga"
HEAD = b"\x1a\x45\xdf\xa3" + b"\x00" * 8   # EBML/webm

os.environ["PLAYME_CACHE_DIR"] = os.path.join(TMP, "cache")
os.environ["PLAYME_MP3_DIR"] = os.path.join(TMP, "mp3")
os.environ["PLAYME_LOG_DIR"] = os.path.join(TMP, "logs")
os.environ["PLAYME_HOST"] = "127.0.0.1"
os.environ["PORT"] = str(PORT)
os.environ["PLAYME_NO_FIREFOX"] = "1"
os.environ["PLAYME_NO_CONVERT"] = "1"
os.environ["PLAYME_PREFER_FILE"] = "1"
# v1.3.0: ritmo de descarga (posicion + 5 s) con valores chicos para el test
os.environ.setdefault("PLAYME_AHEAD_MIN_BYTES", "20480")

import server           # noqa: E402
from transcoder import Transcoder  # noqa: E402
from player import Player          # noqa: E402

FAIL = []


def chk(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(name)


class Res:
    """Resolver falso: 'descarga' el audio por trozos con retardo."""
    last_error = None

    def get_info(self, vid):
        return {"id": vid, "title": "Prueba progresiva", "duration": 240, "uploader": "Test"}

    def get_stream_url(self, vid):
        return None

    def _args(self):
        return []

    def _run(self, args, timeout=None, cancel=None, pace=None):
        out = args[args.index("--output") + 1]
        with open(out, "wb") as f:
            f.write(HEAD)
            f.flush()
            written = len(HEAD)
            left = TOTAL - len(HEAD)
            while left > 0:
                if cancel is not None and cancel.is_set():
                    break                      # simula el aborto de yt-dlp
                n = min(CHUNK, left)
                f.write(b"a" * n)
                f.flush()
                written += n
                left -= n
                if pace is not None:
                    # como yt-dlp: el hook avisa "llevo N bytes" y puede BLOQUEAR
                    # (pausa de la descarga al ritmo de reproduccion)
                    pace(written, TOTAL, 240.0)
                time.sleep(CHUNK_DELAY)

        class R:
            returncode = 0
            stdout = b""
            stderr = b""
            cancelled = False
        R.cancelled = bool(cancel is not None and cancel.is_set())
        if R.cancelled:
            R.returncode = 1
        return R()


# el servidor usa los globales resolver/transcoder/player: se sustituyen
server.resolver = Res()
server.transcoder = Transcoder()
server.player = Player(server.resolver, server.transcoder)

threading.Thread(target=server.main, daemon=True).start()
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.5).close()
        break
    except OSError:
        time.sleep(0.2)

BASE = "http://127.0.0.1:%d" % PORT


def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def state():
    with urllib.request.urlopen(BASE + "/api/state", timeout=15) as r:
        return json.loads(r.read().decode())


# 1) download_bg NO bloquea
t0 = time.time()
server.transcoder.download_bg(VID, server.resolver)
dt = time.time() - t0
chk("download_bg retorna ya (%.2fs)" % dt, dt < 1.0, dt)
chk("is_downloading() true", server.transcoder.is_downloading(VID))

# 2) wait_partial con los primeros KB
t0 = time.time()
p = server.transcoder.wait_partial(VID, timeout=10, min_bytes=262144)
dt = time.time() - t0
chk("wait_partial devuelve con primeros KB (%.2fs)" % dt, bool(p) and dt < 2.5, dt)
chk("aun NO esta completo (partial < total)", not server.transcoder.is_cached(VID),
    server.transcoder.size(VID))

# 3) play arranca antes del 100%
r = post("/api/play", {"video_id": VID})
chk("POST /api/play responde ok", r.get("ok") is True)
t0 = time.time()
st = state()
while not st.get("playing") and time.time() - t0 < 10:
    time.sleep(0.2)
    st = state()
dt = time.time() - t0
chk("playing=true con mode=file antes del 100%% (%.2fs)" % dt,
    st.get("playing") is True and st.get("mode") == "file" and dt < 3.0, st.get("mode"))
chk("state.streaming=true (en vivo)", st.get("streaming") is True, st.get("streaming"))
chk("cache_bytes parcial < total", 0 < st.get("cache_bytes", 0) < TOTAL, st.get("cache_bytes"))

# 4) stream progresivo: bytes ANTES de que la descarga termine
s = socket.create_connection(("127.0.0.1", PORT), timeout=20)
s.sendall(b"GET /api/stream HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
s.settimeout(20)
t0 = time.time()
first = s.recv(4096)
t_first = time.time() - t0
chk("primeros bytes del stream en <2s (%.2fs)" % t_first, len(first) > 0 and t_first < 2.0, t_first)
chk("headers sin Content-Length (flujo)", b"Content-Length" not in first, first[:60])
body = len(first.split(b"\r\n\r\n", 1)[-1])
while True:
    try:
        c = s.recv(65536)
    except (socket.timeout, OSError):
        break
    if not c:
        break
    body += len(c)
t_all = time.time() - t0
s.close()
chk("stream total = archivo completo (%d bytes)" % body, body == TOTAL, body)
chk("sirvio mientras bajaba (%.1fs de descarga)" % t_all, t_all > CHUNK_DELAY, t_all)
chk("al final queda cacheado", server.transcoder.is_cached(VID))
st = state()
chk("streaming=false al completar", st.get("streaming") is False, st.get("streaming"))

# 4b) v1.3.0: cambiar de tema CORTA el anterior y el nuevo arranca al ~1%
VID2 = "progtest02"
r = post("/api/play", {"video_id": VID2})
chk("POST de otro tema corta el actual (playing=false)", r.get("playing") is False, r.get("playing"))
chk("POST de otro tema limpia current/mode", r.get("current") is None and r.get("mode") is None,
    "%s/%s" % (r.get("current"), r.get("mode")))
t0 = time.time()
st2 = state()
while (st2.get("current") or {}).get("id") != VID2 and time.time() - t0 < 20:
    time.sleep(0.1)
    st2 = state()
dt = time.time() - t0
cur2 = (st2.get("current") or {}).get("id")
chk("el nuevo tema arranca antes del 100%% (%.1fs)" % dt, cur2 == VID2 and st2.get("streaming") is True,
    "%s streaming=%s" % (cur2, st2.get("streaming")))
chk("arranca con bytes parciales (%s de %s)" % (st2.get("cache_bytes"), TOTAL),
    0 < st2.get("cache_bytes", 0) < TOTAL, st2.get("cache_bytes"))
for _ in range(200):
    if state().get("streaming") is False:
        break
    time.sleep(0.2)
chk("el nuevo tema sigue solo hasta completar", server.transcoder.is_cached(VID2))

# 4c) v1.3.0: cambiar de tema CORTA tambien la DESCARGA anterior (libera yt-dlp
#     para que el nuevo arranque ya, no cuando termine la descarga vieja)
VID3 = "progtest03"
VID4 = "progtest04"
post("/api/play", {"video_id": VID3})
t0 = time.time()
while time.time() - t0 < 10:
    if server.transcoder.is_downloading(VID3) and server.transcoder.size(VID3) > 0:
        break
    time.sleep(0.1)
chk("VID3 descargando en curso", server.transcoder.is_downloading(VID3), server.transcoder.size(VID3))
t0 = time.time()
post("/api/play", {"video_id": VID4})
while server.transcoder.is_downloading(VID3) and time.time() - t0 < 5:
    time.sleep(0.05)
dt = time.time() - t0
chk("play de otro tema corta la descarga anterior (%.2fs)" % dt,
    not server.transcoder.is_downloading(VID3), dt)
chk("no queda .part a medias del tema cortado", server.transcoder.available_path(VID3) is None,
    server.transcoder.available_path(VID3))
t0 = time.time()
st4 = state()
while (st4.get("current") or {}).get("id") != VID4 and time.time() - t0 < 20:
    time.sleep(0.1)
    st4 = state()
chk("el tema nuevo arranca con ~5 s de audio (%s B)" % st4.get("cache_bytes"),
    (st4.get("current") or {}).get("id") == VID4 and st4.get("streaming") is True
    and 0 < st4.get("cache_bytes", 0) < TOTAL, st4.get("cache_bytes"))
for _ in range(200):
    if state().get("streaming") is False:
        break
    time.sleep(0.2)
chk("el tema nuevo sigue solo hasta completar", server.transcoder.is_cached(VID4))

# 5) /api/clean/dl (boton "Vaciar descargas")
post("/api/convert", {"video_id": VID, "title": "Prueba progresiva"})
c = post("/api/conversions")["conversions"]
chk("hay 1 descarga en la lista", len(c) == 1, list(c.keys()))
r = post("/api/clean/dl")
c = post("/api/conversions")["conversions"]
chk("POST /api/clean/dl vacia la lista", r.get("ok") is True and c == {}, c)

# 6) v1.3.0: al cambiar de tema solo queda el cache del ACTUAL
VID5 = "progtest05"
server.transcoder.download_bg(VID5, server.player.res)
for _ in range(200):
    if server.transcoder.size(VID5) > 0:
        break
    time.sleep(0.1)
antes = server.transcoder.size(VID5)
chk("VID5 tiene bytes en cache antes de cambiar (%s)" % antes, antes > 0, antes)
n = server.transcoder.forget(VID5)
chk("forget(VID5) borra el audio del tema abandonado (%s B)" % n, n > 0, n)
chk("VID5 ya no esta en cache", not server.transcoder.is_cached(VID5))
chk("no queda .part de VID5", server.transcoder.available_path(VID5) is None)
chk("el cache conserva el tema ACTUAL (VID4)",
    server.transcoder.is_cached(VID4), server.transcoder.size(VID4))
_extra = os.path.join(os.environ["PLAYME_CACHE_DIR"], "otrovideo01.webm")
with open(_extra, "wb") as f:
    f.write(b"\x1a\x45\xdf\xa3" + b"\x00" * 2048)
borrados = server.transcoder.forget_except(VID4)
chk("forget_except deja solo el tema actual (%s B borrados)" % borrados, borrados > 0, borrados)
chk("el otro audio ya no esta en disco", not os.path.isfile(_extra))
chk("el tema actual sigue intacto tras forget_except", server.transcoder.is_cached(VID4))

# 7) v1.3.0: la descarga SIGUE a la reproduccion (no baja el archivo completo)
VID7 = "progtest07"
VID8 = "progtest08"
_t = server.transcoder
_t.enable_pace(VID7, duration=240, filesize=TOTAL)
_t.position(VID7, 0.0)
stop_adv = threading.Event()


def _advance():
    p = 0.0
    while not stop_adv.is_set() and p < 600:
        _t.position(VID7, p)
        time.sleep(0.5)
        p += 5.0                       # 5 s de musica cada 0,5 s (playback acelerado)


threading.Thread(target=_advance, daemon=True).start()
_t.download_bg(VID7, server.player.res)
time.sleep(6)
s7 = _t.size(VID7)
_t.cancel(VID7)
stop_adv.set()
for _ in range(50):
    if not _t.is_downloading(VID7):
        break
    time.sleep(0.1)
chk("con ritmo de reproduccion NO baja el archivo completo (%d de %d bytes)" % (s7, TOTAL),
    s7 < TOTAL * 0.9, s7)
chk("con ritmo de reproduccion si carga poco a poco (%d bytes)" % s7, s7 > 0, s7)
chk("POST /api/position mueve la posicion del backend",
    (post("/api/position", {"video_id": VID7, "t": 42}) or {}).get("ok") is True, "")
chk("la posicion queda registrada", abs(_t._pos.get(VID7, -1) - 42) < 0.5, _t._pos.get(VID7))
_t.forget(VID7)
# sin ritmo (boton Descargar audio) la descarga SI es completa
_t.download_bg(VID8, server.player.res)
for _ in range(200):
    if _t.is_cached(VID8):
        break
    time.sleep(0.1)
chk("sin ritmo (Descargar audio) la descarga es completa", _t.is_cached(VID8), _t.size(VID8))

print("== resultado: %s ==" % ("TODO-OK" if not FAIL else "%d fallos" % len(FAIL)))
sys.exit(1 if FAIL else 0)
