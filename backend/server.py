"""
PlayMe v3 - HTTP Server modular.
Bugfix: lock real en _handle_stream; CL correcto en proxy con Range; lock en _run_conv.
v4-fix403: proxy envia headers de navegador + cookies (googlevideo -> 403 Forbidden).
"""
import json, logging, os, re, subprocess, threading, time, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver

from resolver import Resolver
from token_manager import TokenManager
from transcoder import Transcoder
from player import Player
from cookie_parser import build_cookie_header

PORT = int(os.environ.get("PORT", "8090"))
STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(os.path.join(log_dir, "playme.log")), logging.StreamHandler()],
)
logger = logging.getLogger("playme")

# cookies.txt = raiz del proyecto (fuente viva mantenida por TokenManager)
COOKIES_PATH = os.path.join(PROJECT_ROOT, "cookies.txt")

# Headers de navegador para que googlevideo no rechace con 403 los proxys.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
    "Referer": "https://www.youtube.com/",
    "Origin": "https://www.youtube.com",
    "Accept": "*/*",
}

resolver = Resolver()
token_mgr = TokenManager()
transcoder = Transcoder()
player = Player(resolver, transcoder)

MP3_DIR = "/tmp/playme_mp3"
os.makedirs(MP3_DIR, exist_ok=True)

# Tamano del chunk de streaming cuando el cliente pide un rango abierto
# (bytes=0-): googlevideo rechaza rangos abiertos (403) y tambien rangos
# mayores a ~1MB (verificado: 4MB->403, 1MB->206).
MAX_CHUNK = 1024 * 1024  # 1MB

# ── Converter: descargas mp3 en background ──
_conv_lock = threading.Lock()
_conversions = {}  # video_id -> {"progress": 0-100, "path": str or None, "error": str or None}

def _scan_existing_mp3():
    """Escanea MP3_DIR al arrancar y reconstruye conversions."""
    titles = {}
    tpath = os.path.join(MP3_DIR, "titles.json")
    if os.path.isfile(tpath):
        try:
            with open(tpath) as f: titles = json.load(f)
        except: pass
    if os.path.isdir(MP3_DIR):
        for f in os.listdir(MP3_DIR):
            if f.endswith(".mp3"):
                vid = f[:-4]
                path = os.path.join(MP3_DIR, f)
                sz = os.path.getsize(path)
                tit = titles.get(vid, vid)
                _conversions[vid] = {"status": "ready", "title": tit}

_scan_existing_mp3()

def build_stream_request(url, range_h):
    """Construye un urllib.Request con headers de navegador + cookies para
    googlevideo. Anade Range si viene de un request parcial."""
    req = urllib.request.Request(url, headers=dict(BROWSER_HEADERS))
    cookie_hdr = build_cookie_header(COOKIES_PATH)
    if cookie_hdr:
        req.add_header("Cookie", cookie_hdr)
    if range_h:
        req.add_header("Range", range_h)
    return req

def _run_conv(video_id):
    logger.info(f"Conv running: {video_id}")
    webm_path = os.path.join(MP3_DIR, f"{video_id}.webm")
    mp3_path = os.path.join(MP3_DIR, f"{video_id}.mp3")

    # snapshot seguro bajo lock: titulo para metadatos
    with _conv_lock:
        entry = _conversions.get(video_id, {})
    titulo = entry.get("title", video_id)[:30]
    artista = video_id

    dl_args = resolver._args() + [
        "--format", "bestaudio",
        "--output", webm_path,
        "--no-part", "--no-mtime",
        f"https://www.youtube.com/watch?v={video_id}"
    ]
    try:
        subprocess.run(dl_args, capture_output=True, timeout=600)

        if not os.path.isfile(webm_path) or os.path.getsize(webm_path) == 0:
            raise Exception("Download failed or empty")
        # FASE 2: Convertir a mp3 con ffmpeg (incluye metadatos)
        logger.info(f"Conv ffmpeg: {video_id}")
        ff_args = ["ffmpeg", "-i", webm_path, "-vn", "-acodec", "libmp3lame", "-ab", "320k",
                    "-metadata", f"title={titulo}", "-metadata", f"artist={artista}",
                    "-y", mp3_path]
        subprocess.run(ff_args, capture_output=True, timeout=600)

        if os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 0:
            sz = os.path.getsize(mp3_path)
            with _conv_lock:
                entry = _conversions.get(video_id, {})
                entry["status"] = "ready"
                _conversions[video_id] = entry
            logger.info(f"Conv done: {video_id} ({sz} bytes)")
            try: os.unlink(webm_path)
            except: pass
            # Guardar titulo
            try:
                tpath = os.path.join(MP3_DIR, "titles.json")
                titles = {}
                if os.path.isfile(tpath):
                    with open(tpath) as f: titles = json.load(f)
                titles[video_id] = titulo
                with open(tpath, "w") as f: json.dump(titles, f)
            except: pass
        else:
            raise Exception("FFmpeg output not found")
    except Exception as e:
        logger.error(f"Conv fail: {video_id}: {e}")
        # Limpiar webm huerfano si lo hay
        try:
            if os.path.isfile(webm_path): os.unlink(webm_path)
        except: pass
        with _conv_lock:
            if video_id in _conversions:
                _conversions[video_id]["status"] = "error"

def json_res(data, status=200):
    body = json.dumps(data).encode()
    return status, {"Content-Type": "application/json", "Content-Length": str(len(body))}, body

def err_res(msg, status=400):
    return json_res({"ok": False, "error": msg}, status)

def read_body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    return handler.rfile.read(length) if length else b""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(f"{self.client_address[0]} - {fmt % args}")

    def _send(self, status, headers, body):
        try:
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if isinstance(body, bytes):
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "audio/webm")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
        except: pass

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._static("index.html", "text/html")
            elif path == "/api/state":
                state = player.get_state()
                # agregar estado de conversiones activas
                with _conv_lock:
                    convs = dict(_conversions)
                state["conversions"] = {k: {"status": v["status"]} for k, v in convs.items()}
                self._send(*json_res({"ok": True, **state}))
            elif path == "/api/stream":
                self._handle_stream()
            elif path.startswith("/api/download/mp3/"):
                vid = path[len("/api/download/mp3/"):].split("/")[0].split("?")[0]
                self._serve_mp3(vid)
            else:
                self._send(*err_res("Not found", 404))
        except Exception as e:
            logger.exception(f"GET {path}: {e}")
            try: self._send(*err_res(str(e), 500))
            except: pass

    def _serve_mp3(self, video_id):
        """Sirve archivo mp3 si ya existe."""
        # Extraer title del query string si existe
        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        title = qs.get("title", [None])[0]

        mp3 = os.path.join(MP3_DIR, f"{video_id}.mp3")
        if os.path.isfile(mp3):
            sz = os.path.getsize(mp3)
            fname = title if title else video_id
            # Limpiar nombre: 30 chars, solo caracteres seguros (escapar comillas/punto-coma)
            fname = fname[:30].replace("/", "-").replace(" ", "-")
            fname = "".join(c for c in fname if c.isalnum() or c in "._- ") or video_id
            fname = fname.strip().replace(" ", "-") + ".mp3"
            # sanear: quitar comillas, punto-coma y backslash que romperian el header
            fname = fname.replace('"', "").replace(";", "").replace(chr(92), "-")
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(sz))
            self.send_header('Content-Disposition', f'attachment; filename="{fname}"')
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with open(mp3, "rb") as f:
                while True:
                    c = f.read(65536)
                    if not c: break
                    self.wfile.write(c)
            return
        self._send(*err_res("Not ready", 404))

    def _static(self, name, mime=None):
        path = os.path.join(STATIC, name)
        if not os.path.isfile(path):
            self._send(*err_res("Not found", 404)); return
        mt = mime or {"html": "text/html", "js": "application/javascript", "css": "text/css"}.get(name.split(".")[-1], "application/octet-stream")
        sz = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", mt)
        self.send_header("Content-Length", str(sz))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                c = f.read(65536)
                if not c: break
                self.wfile.write(c)

    def _handle_stream(self):
        # Bugfix: usar el lock real del player, no un nuevo threading.Lock() efimero
        with player._lock:
            mode = player.mode
            surl = player.stream_url
            cpath = player.cache_path
        if mode == "file" and cpath and os.path.exists(cpath):
            self._serve_file(cpath)
        elif mode == "proxy" and surl:
            self._proxy(surl)
        else:
            self._send(*err_res("No stream", 404))

    def _serve_file(self, path):
        sz = os.path.getsize(path)
        range_h = self.headers.get("Range", "")
        start, end = 0, sz - 1
        if range_h.startswith("bytes="):
            parts = range_h[6:].split("-")
            try:
                start = int(parts[0]) if parts[0] else 0
                if len(parts) > 1 and parts[1]: end = int(parts[1])
            except: pass
        ct = {"webm": "audio/webm", "mp3": "audio/mpeg"}.get(path.split(".")[-1], "audio/webm")
        if start > 0:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{sz}")
            cl = end - start + 1
        else:
            self.send_response(200)
            cl = sz
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(cl))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start); left = cl
                while left > 0:
                    c = f.read(min(65536, left))
                    if not c: break
                    self.wfile.write(c); left -= len(c)
        except (BrokenPipeError, ConnectionResetError): pass

    def _proxy(self, url):
        """Proxy de streaming. Envia headers de navegador + cookies para que
        googlevideo no rechace con 403. Mantiene Range/CL/Content-Range.
        googlevideo EXIGE Range CON FIN: rechaza rangos abiertos 'bytes=0-'
        (403). El navegador manda 'bytes=0-' al iniciar -> se convierte a un
        chunk con fin (bytes=0-<MAX_CHUNK-1>)."""
        range_h = self.headers.get("Range", "")
        upstream_range = range_h if range_h else "bytes=0-"
        if upstream_range.endswith("-"):
            start_str = upstream_range[len("bytes="):-1]
            try:
                start = int(start_str) if start_str else 0
            except ValueError:
                start = 0
            upstream_range = f"bytes={start}-{start + MAX_CHUNK - 1}"
        try:
            req = build_stream_request(url, upstream_range)
            resp = urllib.request.urlopen(req, timeout=10)
            ct = resp.headers.get("Content-Type", "audio/webm")
            cl = resp.headers.get("Content-Length")
            cr = resp.headers.get("Content-Range", "")
            st = 206 if resp.status == 206 else 200
            self.send_response(st)
            self.send_header("Content-Type", ct)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Accept-Ranges", "bytes")
            # Bugfix: enviar Content-Length SIEMPRE que el upstream la de (incluso con Range)
            if cl:
                self.send_header("Content-Length", cl)
            if cr:
                self.send_header("Content-Range", cr)
            self.end_headers()
            while True:
                # Lectura directa: select.select([resp.fp]) falla en Python 3.13
                # (resp.fp de urllib no tiene fileno()) y cortaba el stream tras
                # el primer chunk. El read bloqueante termina solo: b'' si el
                # upstream corta, BrokenPipe si el cliente cierra.
                try:
                    c = resp.read(65536)
                except Exception:
                    break
                if not c:
                    break
                try:
                    self.wfile.write(c); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
            resp.close()
        except Exception as e:
            logger.error(f"proxy error: {e}")
            try: self._send(*err_res("Stream error", 502))
            except: pass

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            data = json.loads(read_body(self)) if self.headers.get("Content-Length", "0") != "0" else {}
        except:
            self._send(*err_res("Invalid JSON", 400)); return
        try:
            if path == "/api/search":
                q = data.get("query", "").strip()
                if not q: self._send(*err_res("query required")); return
                tipo = data.get("tipo", "video")
                results = resolver.search(q, tipo=tipo)
                self._send(*json_res({"ok": True, "results": results}))
            elif path == "/api/play":
                vid = data.get("video_id", "")
                if not vid: self._send(*err_res("video_id required")); return
                if player.play(vid):
                    self._send(*json_res({"ok": True, **player.get_state()}))
                else:
                    self._send(*err_res(player.last_error or "Play failed", 500))
            elif path == "/api/convert":
                vid = data.get("video_id", "")
                if not vid: self._send(*err_res("video_id required")); return
                title = data.get("title", vid)
                mp3_path = os.path.join(MP3_DIR, f"{vid}.mp3")
                # Si ya existe listo
                if os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 0:
                    self._send(*json_res({"ok": True, "status": "ready", "url": f"/api/download/mp3/{vid}"}))
                    return
                # Si ya esta en cola, devolver estado
                with _conv_lock:
                    if vid in _conversions:
                        c = _conversions[vid]
                        self._send(*json_res({"ok": True, "status": c.get("status","converting")}))
                        return
                # Nueva conversion
                logger.info(f"Conv queue: {vid} - {title}")
                with _conv_lock:
                    _conversions[vid] = {"status": "converting", "title": title}
                t = threading.Thread(target=_run_conv, args=(vid,), daemon=True)
                t.start()
                self._send(*json_res({"ok": True, "status": "queued"}))
            elif path == "/api/conversions":
                with _conv_lock:
                    c = dict(_conversions)
                self._send(*json_res({"ok": True, "conversions": c}))
            elif path == "/api/clean/dl":
                import shutil
                try:
                    # Matar procesos yt-dlp y ffmpeg activos
                    for f in os.listdir(MP3_DIR):
                        if f.endswith(".webm"):
                            vid = f[:-5]
                            subprocess.run(["pkill", "-9", "-f", vid], capture_output=True, timeout=5)
                    if os.path.isdir(MP3_DIR):
                        shutil.rmtree(MP3_DIR)
                        os.makedirs(MP3_DIR, exist_ok=True)
                    with _conv_lock:
                        _conversions.clear()
                    logger.info("Downloads cleaned")
                    self._send(*json_res({"ok": True}))
                except Exception as e:
                    logger.error(f"Clean error: {e}")
                    self._send(*err_res("Clean failed", 500))
            elif path == "/api/pause":
                player.toggle_pause()
                self._send(*json_res({"ok": True, **player.get_state()}))
            elif path == "/api/resume":
                if player.paused: player.toggle_pause()
                self._send(*json_res({"ok": True, **player.get_state()}))
            elif path == "/api/stop":
                player.stop()
                self._send(*json_res({"ok": True, **player.get_state()}))
            elif path == "/api/next":
                player.next()
                self._send(*json_res({"ok": True, **player.get_state()}))
            elif path == "/api/prev":
                player.prev()
                self._send(*json_res({"ok": True, **player.get_state()}))
            elif path == "/api/queue/add":
                vid = data.get("video_id", "")
                if not vid: self._send(*err_res("video_id required")); return
                player.add_queue(vid, data.get("title"), data.get("duration", 0), data.get("uploader", ""))
                self._send(*json_res({"ok": True, **player.get_state()}))
            elif path == "/api/queue/remove":
                idx = data.get("index", -1)
                if idx < 0: self._send(*err_res("index required")); return
                player.remove_queue(idx)
                self._send(*json_res({"ok": True, **player.get_state()}))
            else:
                self._send(*err_res("Not found", 404))
        except Exception as e:
            logger.exception(f"POST {path}: {e}")
            try: self._send(*err_res(str(e), 500))
            except: pass

def main():
    os.makedirs(STATIC, exist_ok=True)
    transcoder.cleanup()
    threading.Thread(target=token_mgr.refresh_loop, daemon=True).start()

    class Threaded(socketserver.ThreadingMixIn, HTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Threaded(("0.0.0.0", PORT), Handler)
    logger.info(f"PlayMe v4-fix403 en http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

if __name__ == "__main__":
    main()
