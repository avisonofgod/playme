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

    def _run(self, args, timeout=None):
        out = args[args.index("--output") + 1]
        with open(out, "wb") as f:
            f.write(HEAD)
            f.flush()
            left = TOTAL - len(HEAD)
            while left > 0:
                n = min(CHUNK, left)
                f.write(b"a" * n)
                f.flush()
                left -= n
                time.sleep(CHUNK_DELAY)
        class R:
            returncode = 0
            stdout = b""
            stderr = b""
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

# 5) /api/clean/dl (boton "Vaciar descargas")
post("/api/convert", {"video_id": VID, "title": "Prueba progresiva"})
c = post("/api/conversions")["conversions"]
chk("hay 1 descarga en la lista", len(c) == 1, list(c.keys()))
r = post("/api/clean/dl")
c = post("/api/conversions")["conversions"]
chk("POST /api/clean/dl vacia la lista", r.get("ok") is True and c == {}, c)

print("== resultado: %s ==" % ("TODO-OK" if not FAIL else "%d fallos" % len(FAIL)))
sys.exit(1 if FAIL else 0)
