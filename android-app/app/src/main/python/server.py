"""PlayMe - Servidor HTTP modular de streaming de audio.
Endpoints: estado, reproduccion, cola, busqueda, conversor mp3, deteccion de IP.
Arquitectura modular con rate-limiting por IP; streaming en proxy respeta HTTP Range.
"""
import json, logging, os, re, shutil, subprocess, threading, time, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver

from resolver import Resolver
from token_manager import TokenManager
from transcoder import Transcoder
from player import Player
from cookie_parser import build_cookie_header
from runner import run_command, _kill_group as _kill_group_runner

PORT = int(os.environ.get("PORT", "8191"))
HOST = os.environ.get("PLAYME_HOST", "0.0.0.0")
STATIC = os.environ.get("PLAYME_STATIC") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
log_dir = os.environ.get("PLAYME_LOG_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
os.makedirs(log_dir, exist_ok=True)
try:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(os.path.join(log_dir, "playme.log")), logging.StreamHandler()],
    )
except OSError:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("playme")

# cookies.txt = raiz del proyecto (fuente viva mantenida por TokenManager)
COOKIES_PATH = os.environ.get("PLAYME_COOKIES_FILE") or os.path.join(PROJECT_ROOT, "cookies.txt")

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

MP3_DIR = os.environ.get("PLAYME_MP3_DIR", "/tmp/playme_mp3")
os.makedirs(MP3_DIR, exist_ok=True)

# Tamano del chunk de streaming cuando el cliente pide un rango abierto
MAX_CHUNK = 1024 * 1024  # 1MB

# ── Rate limiting por IP (token bucket simple por endpoint) ──
_RATE_LOCK = threading.Lock()
_RATE = {
    "/api/convert": {"max": 10, "window_sec": 60, "hits": {}},     # 10/min
    "/api/search": {"max": 15, "window_sec": 60, "hits": {}},      # 15/min
}
_RATE_INACTIVE_SEC = 600  # limpiar ips inactivas > 10 min


def _rate_limited(path, ip):
    """Devuelve True si la ip excede el limite para el endpoint. Token bucket
    de ventana deslizante: cuenta hits en los ultimos `window_sec` segundos."""
    cfg = _RATE.get(path)
    if not cfg:
        return False
    now = time.time()
    with _RATE_LOCK:
        hits = cfg["hits"].setdefault(ip, [])
        # descartar hits fuera de la ventana
        cutoff = now - cfg["window_sec"]
        wins = [t for t in hits if t > cutoff]
        if len(wins) >= cfg["max"]:
            cfg["hits"][ip] = wins
            return True
        wins.append(now)
        cfg["hits"][ip] = wins
    return False


def _cleanup_rates():
    """Elimina ips inactivas (>10min) para que el dict no crezca sin limite."""
    now = time.time()
    with _RATE_LOCK:
        for path, cfg in _RATE.items():
            stale = [ip for ip, hits in cfg["hits"].items()
                     if not hits or (now - max(hits)) > _RATE_INACTIVE_SEC]
            for ip in stale:
                del cfg["hits"][ip]


# ── Deteccion de IP publica (con cache) para /api/ip y el cliente Android ──
_PUBLIC_IP = {"ip": None, "t": 0.0}
_PUBLIC_IP_CACHE_SEC = 600  # refrescar si > 10 min


def _detect_public_ip():
    """Devuelve la IP publica del servidor (api.ipify.org, timeout corto).
    Cachea en memoria; refresca tras _PUBLIC_IP_CACHE_SEC. Fallback: IP local
    LAN via socket, y si nada, '127.0.0.1'. Nunca bloquea el server."""
    now = time.time()
    if _PUBLIC_IP["ip"] and (now - _PUBLIC_IP["t"]) < _PUBLIC_IP_CACHE_SEC:
        return _PUBLIC_IP["ip"]
    ip = None
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=3) as r:
            txt = r.read().decode().strip()
            if txt and re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", txt):
                ip = txt
    except Exception:
        ip = None
    if not ip:
        ip = _local_lan_ip()
    if not ip:
        ip = "127.0.0.1"
    _PUBLIC_IP["ip"] = ip
    _PUBLIC_IP["t"] = now
    return ip


def _local_lan_ip():
    """IP local LAN via socket UDP connect (no envia trafico real)."""
    try:
        import socket as _s
        s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return None


# ── Converter: descargas mp3 en background ──
_conv_lock = threading.Lock()
_conversions = {}  # video_id -> {"progress": 0-100, "path": str or None, "error": str or None}
_MAX_CONVERSIONS = 50  # limite de entradas en _conversions


def _evict_conversions():
    """Evicta las TERMINADAS mas antiguas si _conversions supera el limite.
    NUNCA evicta una en curso (status converting/downloading)."""
    with _conv_lock:
        while len(_conversions) > _MAX_CONVERSIONS:
            oldest = None
            for k in list(_conversions.keys()):
                if _conversions[k].get("status") in ("ready", "error"):
                    oldest = k
                    break
            if oldest is None:
                break
            del _conversions[oldest]
            logger.info(f"conversions evict: {oldest}")


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


def _audio_convert(vid, title):
    """Android sin ffmpeg: descarga el audio original y sirve /api/download/audio/<vid>."""
    url = "/api/download/audio/" + vid
    if transcoder.is_cached(vid):
        with _conv_lock:
            _conversions[vid] = {"status": "ready", "title": title, "url": url}
        return json_res({"ok": True, "status": "ready", "url": url})
    with _conv_lock:
        st = (_conversions.get(vid) or {}).get("status")
        if st in ("queued", "converting"):
            return json_res({"ok": True, "status": st})
        _conversions[vid] = {"status": "converting", "title": title, "url": url}

    def _job():
        try:
            transcoder.download_bg(vid, resolver)
            r = "ready" if transcoder.is_cached(vid) else "error"
        except Exception as e:
            logger.warning("audio dl fail %s: %s" % (vid, e)); r = "error"
        with _conv_lock:
            _conversions[vid] = {"status": r, "title": title, "url": url}

    threading.Thread(target=_job, daemon=True).start()
    return json_res({"ok": True, "status": "queued"})

def sanitize_filename(title, video_id):
    """Regla UNICA de nombre de archivo descargado (fuente de verdad del backend).
    Usa la antigua regla de _serve_mp3, ahora expuesta y reutilizable.
    Devuelve siempre algo (fallback video_id)."""
    fname = (title or "").strip()
    if not fname:
        fname = video_id
    fname = re.sub(r"\.(mp3|webm|m4a|opus|ogg)$", "", fname, flags=re.I)
    fname = fname[:30].replace("/", "-").replace(" ", "-")
    fname = re.sub(r"-{2,}", "-", fname).strip("-")
    fname = "".join(c for c in fname if c.isalnum() or c in "._- ") or video_id
    fname = fname.strip().replace(" ", "-")
    # quitar comillas, punto-coma y backslash que romperian el header
    fname = fname.replace('"', "").replace(";", "").replace(chr(92), "-")
    return fname + ".mp3"


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
        run_command(dl_args, timeout=600)

        if not os.path.isfile(webm_path) or os.path.getsize(webm_path) == 0:
            raise Exception("Download failed or empty")
        # FASE 2: Convertir a mp3 con ffmpeg (incluye metadatos)
        logger.info(f"Conv ffmpeg: {video_id}")
        ff_args = ["ffmpeg", "-i", webm_path, "-vn", "-acodec", "libmp3lame", "-ab", "320k",
                    "-metadata", f"title={titulo}", "-metadata", f"artist={artista}",
                    "-y", mp3_path]
        run_command(ff_args, timeout=600)

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

    def _client_ip(self):
        """IP del cliente para rate limiting (sin confiar en headers)."""
        return self.client_address[0]

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
            elif path == "/api/netdiag":
                import netdiag
                self._send(*json_res(netdiag.run())); return
            elif path == "/api/state":
                state = player.get_state()
                # agregar estado de conversiones activas
                with _conv_lock:
                    convs = dict(_conversions)
                state["conversions"] = {k: {"status": v["status"], "url": v.get("url", "")} for k, v in convs.items()}
                state["mp3"] = os.environ.get("PLAYME_NO_CONVERT") != "1"
                self._send(*json_res({"ok": True, **state}))
            elif path == "/api/ip":
                self._send(*json_res({"ok": True, "ip": _detect_public_ip()}))
            elif path == "/api/stream":
                self._handle_stream()
            elif path.startswith("/api/download/audio/"):
                self._serve_audio(path[len("/api/download/audio/"):].split("/")[0].split("?")[0])
            elif path.startswith("/api/download/mp3/"):
                vid = path[len("/api/download/mp3/"):].split("/")[0].split("?")[0]
                self._serve_mp3(vid)
            else:
                self._send(*err_res("Not found", 404))
        except Exception as e:
            logger.exception(f"GET {path}: {e}")
            try: self._send(*err_res(str(e), 500))
            except: pass

    def _serve_audio(self, video_id):
        """Sirve el audio original cacheado (Android sin ffmpeg)."""
        path = transcoder.path(video_id)
        if not (os.path.isfile(path) and os.path.getsize(path) > 0):
            self._send(*err_res("Not ready", 404)); return
        with _conv_lock:
            title = (_conversions.get(video_id) or {}).get("title")
        with open(path, "rb") as f:
            head = f.read(12)
        if head[4:8] == b"ftyp":
            ctype, ext = "audio/mp4", ".m4a"
        else:
            ctype, ext = "audio/webm", ".webm"
        fname = sanitize_filename(title, video_id) + ext
        sz = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(sz))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % fname)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                c = f.read(65536)
                if not c: break
                self.wfile.write(c)

    def _serve_mp3(self, video_id):
        """Sirve archivo mp3 si ya existe. El nombre viene de la unica fuente
        de verdad: sanitize_filename() sobre el title de _conversions."""
        with _conv_lock:
            conv = _conversions.get(video_id, {})
        title = conv.get("title")

        mp3 = os.path.join(MP3_DIR, f"{video_id}.mp3")
        if os.path.isfile(mp3):
            sz = os.path.getsize(mp3)
            fname = sanitize_filename(title, video_id)
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
        elif mode == "proxy":
            vid = player.current.get("id") if player.current else ""
            # Si ya hay URL resuelta (googlevideo) usamos _proxy(): respeta el
            # Range del cliente (206 + Content-Range) -> el seek funciona y no
            # se descarga el video completo por cada peticion.
            if surl:
                self._proxy(surl)
            else:
                # Android: sin subprocess no hay respaldo yt-dlp; error claro.
                self._send(*err_res("Sin stream resuelto", 404))
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
        if range_h.startswith("bytes="):
            # cualquier Range (incluido bytes=0-) -> respuesta parcial 206
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
        googlevideo no rechace con 403. Mantiene Range/CL/Content-Range."""
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
            if cl:
                self.send_header("Content-Length", cl)
            if cr:
                self.send_header("Content-Range", cr)
            self.end_headers()
            while True:
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
        ip = self._client_ip()
        _cleanup_rates()  # mantiene el rate-dict acotado
        try:
            data = json.loads(read_body(self)) if self.headers.get("Content-Length", "0") != "0" else {}
        except:
            self._send(*err_res("Invalid JSON", 400)); return
        try:
            if path in ("/api/search", "/api/convert"):
                if _rate_limited(path, ip):
                    logger.warning(f"rate limit {ip} {path}")
                    self._send(*err_res("rate limit", 429))
                    return
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
                if os.environ.get("PLAYME_NO_CONVERT") == "1":
                    self._send(*_audio_convert(vid, data.get("title", vid))); return
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
                # Nueva conversion (acotar las entradas terminadas antes)
                _evict_conversions()
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
                # SOLO limpia la lista de descargas (UI). NO borra archivos mp3 en disco.
                try:
                    with _conv_lock:
                        _conversions.clear()
                    logger.info("Downloads list cleared (files kept on disk)")
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
                # Metadata: si el titulo llega vacio, es placeholder "YouTube <id>"
                # o no viene, resolver la metadata real con get_info.
                title = data.get("title") or ""
                duration = data.get("duration", 0)
                uploader = data.get("uploader", "")
                if (not title or title == f"YouTube {vid}"):
                    info = resolver.get_info(vid) or {}
                    title = info.get("title") or title or f"YouTube {vid}"
                    duration = info.get("duration", duration or 0)
                    uploader = info.get("uploader", uploader or "")
                player.add_queue(vid, title, duration, uploader)
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
    if os.environ.get("PLAYME_NO_FIREFOX") != "1":
        threading.Thread(target=token_mgr.refresh_loop, daemon=True).start()

    class Threaded(socketserver.ThreadingMixIn, HTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Threaded((HOST, PORT), Handler)
    logger.info(f"PlayMe v11-limpieza en http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

if __name__ == "__main__":
    main()
