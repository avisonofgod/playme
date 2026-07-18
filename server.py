"""
PlayMe v2 - HTTP Server
Sirve API REST y streaming de audio via mpv/yt-dlp.
"""
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO

# ── logging ──────────────────────────────────────────────
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "playme.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("playme")

# ── imports locales ──────────────────────────────────────
from resolver import Resolver     # noqa: E402
from token_manager import TokenManager # noqa: E402
from transcoder import Transcoder # noqa: E402
from player import Player         # noqa: E402

# ── instancias globales ──────────────────────────────────
resolver = Resolver()
token_mgr = TokenManager()
transcoder = Transcoder()
player = Player(resolver, transcoder)
player_lock = threading.Lock()
state_cache = {}
state_cache_lock = threading.Lock()
PORT = int(os.environ.get("PORT", "8080"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# ── helpers ──────────────────────────────────────────────

def json_response(data, status=200):
    body = json.dumps(data).encode("utf-8")
    return (status, {"Content-Type": "application/json", "Content-Length": str(len(body))}, body)

def error_response(msg, status=400):
    return json_response({"ok": False, "error": msg}, status)

def read_body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return b""
    return handler.rfile.read(length)

def update_state():
    global state_cache
    s = player.get_state()
    with state_cache_lock:
        state_cache = s
    return s

# ── proxy helpers ────────────────────────────────────────

PROXY_TIMEOUT = 30

# ── handler HTTP ─────────────────────────────────────────

class PlayMeHandler(BaseHTTPRequestHandler):
    quiet = False

    def log_message(self, fmt, *args):
        if not self.quiet:
            logger.info(f"{self.client_address[0]} - {fmt % args}")

    def _send(self, status, headers, body):
        """Envía respuesta con headers + body (bytes o generador)."""
        try:
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if isinstance(body, bytes):
                self.wfile.write(body)
            else:
                for chunk in body:
                    if chunk:
                        self.wfile.write(chunk)
                        self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ── HEAD ──────────────────────────────────────────────

    def do_HEAD(self):
        """HEAD requests (navegador/preload). Responde como GET sin body."""
        path = self.path.split("?")[0]
        try:
            if path == "/api/stream":
                # Para HEAD de stream, responder headers básicos
                self.send_response(200)
                self.send_header("Content-Type", "audio/webm")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as e:
            logger.exception(f"HEAD {path}: {e}")
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    # ── GET ──────────────────────────────────────────────

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/" or path == "/index.html":
                self._serve_static("index.html", "text/html")
            elif path.startswith("/static/"):
                self._serve_static(path[8:])
            elif path == "/api/state":
                self._handle_state()
            elif path == "/api/stream":
                self._handle_stream()
            elif path == "/api/cache":
                self._handle_cache_info()
            elif path == "/api/cookies":
                self._handle_cookies_get()
            elif path == "/api/cookies/status":
                self._handle_cookies_status()
            elif path == "/api/token":
                self._handle_token_page()
            elif path == "/api/token/status":
                self._handle_token_status()
            else:
                self._send(*error_response("Not found", 404))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as e:
            logger.exception(f"GET {path}: {e}")
            try:
                self._send(*error_response(str(e), 500))
            except Exception:
                pass

    def _serve_static(self, filename, force_mime=None):
        path = os.path.join(STATIC_DIR, filename)
        if not os.path.isfile(path):
            self._send(*error_response("File not found", 404))
            return
        mime_map = {
            ".html": "text/html",
            ".js": "application/javascript",
            ".css": "text/css",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        ext = os.path.splitext(filename)[1]
        mime = force_mime or mime_map.get(ext, "application/octet-stream")
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _handle_state(self):
        s = update_state()
        self._send(*json_response({"ok": True, **s}))

    def _handle_cache_info(self):
        """Info de archivos en cache."""
        cache_dir = "/tmp/playme_cache"
        files = []
        if os.path.isdir(cache_dir):
            for f in os.listdir(cache_dir):
                fpath = os.path.join(cache_dir, f)
                if os.path.isfile(fpath) and not f.startswith("pipe_"):
                    files.append({
                        "name": f,
                        "size": os.path.getsize(fpath),
                        "mtime": os.path.getmtime(fpath),
                    })
        self._send(*json_response({"ok": True, "files": files}))

    def _handle_cookies_status(self):
        """Estado de las cookies."""
        from resolver import COOKIES_PATH as CP
        has = os.path.isfile(CP) and os.path.getsize(CP) > 0
        size = 0
        domains = []
        if has:
            size = os.path.getsize(CP)
            with open(CP) as f:
                for line in f:
                    if line.strip() and not line.startswith("#"):
                        parts = line.strip().split("\t")
                        if len(parts) >= 1:
                            d = parts[0]
                            if d not in domains:
                                domains.append(d)
        self._send(*json_response({
            "ok": True,
            "has_cookies": has,
            "file_size": size,
            "domains": domains[:5],
        }))

    def _handle_cookies_get(self):
        """Endpoint GET para obtener instrucciones."""
        html = """<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8">
<title>PlayMe - Configurar Cookies</title>
<style>
body{font-family:sans-serif;background:#0d0d0d;color:#e0e0e0;max-width:600px;margin:40px auto;padding:20px}
h1{color:#e94560}
code{background:#1a1a2e;padding:2px 6px;border-radius:4px;font-size:0.9em}
pre{background:#1a1a2e;padding:12px;border-radius:8px;overflow-x:auto}
input[type=file]{padding:12px;background:#1a1a2e;border:1px solid #0f3460;border-radius:8px;color:#e0e0e0;width:100%}
button{padding:12px 24px;background:#e94560;color:white;border:none;border-radius:8px;cursor:pointer;margin-top:12px}
#status{margin-top:12px;padding:8px;border-radius:6px;display:none}
</style>
</head>
<body>
<h1>🍪 PlayMe - Cookies de YouTube</h1>
<p>Para reproducir videos bloqueados, necesitas subir tus cookies de YouTube.</p>

<h2>📖 Instrucciones</h2>
<ol>
<li>Instala la extensión <strong>"Get cookies.txt"</strong>:
   <br><a href="https://chromewebstore.google.com/detail/get-cookiestxt/bgaddhkoddajcdgocldbbfleckgcbcid" target="_blank">Chrome</a>
   | <a href="https://addons.mozilla.org/firefox/addon/cookies-txt/" target="_blank">Firefox</a></li>
<li>Ve a <strong>youtube.com</strong> y asegúrate de haber iniciado sesión</li>
<li>Haz clic en la extensión y exporta las cookies (formato Netscape)</li>
<li>Sube el archivo <code>cookies.txt</code> aquí abajo</li>
</ol>

<form id="uploadForm" enctype="multipart/form-data">
  <input type="file" id="fileInput" accept=".txt">
  <button type="submit">Subir Cookies</button>
</form>
<div id="status"></div>

<h2>🔄 ¿Cómo funciona?</h2>
<ul>
<li>Las cookies se guardan en <code>cookies.txt</code></li>
<li>yt-dlp las usa automáticamente en cada búsqueda</li>
<li>YouTube refresca los tokens automáticamente</li>
<li>No necesitas volver a subirlas a menos que cierres sesión</li>
</ul>

<h2>✔️ Verificar estado</h2>
<p id="cookiesStatus">Cargando...</p>

<script>
async function checkStatus() {
  const r = await fetch('/api/cookies/status');
  const d = await r.json();
  const el = document.getElementById('cookiesStatus');
  if (d.has_cookies) {
    el.innerHTML = '✅ Cookies configuradas (' + d.domains.join(", ") + ') - ' +
      (d.file_size / 1024).toFixed(1) + ' KB';
    document.getElementById('cookiesStatus').style.color = '#44bd32';
  } else {
    el.innerHTML = '❌ Sin cookies. Sigue las instrucciones para subirlas.';
    document.getElementById('cookiesStatus').style.color = '#e94560';
  }
}
checkStatus();

document.getElementById('uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const file = document.getElementById('fileInput').files[0];
  if (!file) return;
  const status = document.getElementById('status');
  status.style.display = 'block';
  status.textContent = 'Subiendo...';
  status.style.background = '#1a3a6b';
  status.style.color = '#88bbff';

  const formData = new FormData();
  formData.append('cookies', file);
  try {
    const r = await fetch('/api/cookies/upload', { method: 'POST', body: formData });
    const d = await r.json();
    if (d.ok) {
      status.textContent = '✅ Cookies guardadas! ' + d.count + ' cookies de ' +
        d.domains.join(", ");
      status.style.background = '#1a6b3c';
      status.style.color = '#88ff88';
      checkStatus();
    } else {
      status.textContent = '❌ Error: ' + (d.error || 'desconocido');
      status.style.background = '#6b1a1a';
      status.style.color = '#ff8888';
    }
  } catch (err) {
    status.textContent = '❌ Error de conexión: ' + err.message;
    status.style.background = '#6b1a1a';
    status.style.color = '#ff8888';
  }
});
</script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.end_headers()
        self.wfile.write(html.encode())

    def _handle_token_status(self):
        """Estado del token SAPISID."""
        info = token_mgr.get_info()
        self._send(*json_response({"ok": True, **info}))

    def _handle_token_page(self):
        """Página HTML para configurar token SAPISID."""
        info = token_mgr.get_info()
        has = info["has_token"]
        prefix = info["sapisid_prefix"] or "—"

        html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8">
<title>PlayMe - Token YouTube</title>
<style>
body{{font-family:sans-serif;background:#0d0d0d;color:#e0e0e0;max-width:600px;margin:40px auto;padding:20px}}
h1{{color:#e94560}}
code{{background:#1a1a2e;padding:2px 6px;border-radius:4px;font-size:0.9em}}
pre{{background:#1a1a2e;padding:12px;border-radius:8px;overflow-x:auto;font-size:0.85em}}
input[type=text]{{padding:12px;background:#1a1a2e;border:1px solid #0f3460;border-radius:8px;color:#e0e0e0;width:100%;font-family:monospace;font-size:0.95em}}
button{{padding:12px 24px;background:#e94560;color:white;border:none;border-radius:8px;cursor:pointer;margin-top:12px}}
#status{{margin-top:12px;padding:8px;border-radius:6px;display:none}}
.ok{{color:#44bd32}}
.ko{{color:#e94560}}
.step{{background:#1a1a2e;padding:16px;border-radius:8px;margin:12px 0;border-left:3px solid #0f3460}}
</style>
</head>
<body>
<h1>🔑 PlayMe - Token YouTube</h1>
<p>Configuración única. El token se refresca automáticamente.</p>

<div class="step">
<h3>📋 Estado actual</h3>
<p id="statusDisplay">{'✅ Token configurado: <code>' + prefix + '</code>' if has else '❌ Sin token'}</p>
</div>

<div class="step">
<h3>📖 Obtener tu SAPISID</h3>
<ol>
<li>Abre <strong>youtube.com</strong> en Chrome y asegúrate de haber iniciado sesión</li>
<li>Abre DevTools: <code>F12</code> o <code>Ctrl+Shift+I</code></li>
<li>Ve a <strong>Application → Storage → Cookies → youtube.com</strong></li>
<li>Busca la cookie <strong>SAPISID</strong></li>
<li>Copia el <strong>valor</strong> (es una cadena como <code>fH88mDHf_dXdqmGj/...</code>)</li>
</ol>
</div>

<div class="step">
<h3>🔑 Pegar SAPISID</h3>
<input type="text" id="tokenInput" placeholder="Pega aquí tu SAPISID..." spellcheck="false">
<button onclick="submitToken()">Guardar Token</button>
<div id="status"></div>
</div>

<div class="step">
<h3>🔄 ¿Cómo funciona?</h3>
<ul>
<li>El SAPISID se guarda en <code>/root/playme/token.txt</code></li>
<li>El servidor construye un <code>cookies.txt</code> válido automáticamente</li>
<li>yt-dlp renueva las cookies volátiles en cada request</li>
<li>El token NO expira mientras mantengas sesión en YouTube</li>
<li>No necesitas volver a configurar a menos que cierres sesión</li>
</ul>
</div>

<script>
async function checkStatus() {{
  const r = await fetch('/api/token/status');
  const d = await r.json();
  const el = document.getElementById('statusDisplay');
  if (d.has_token) {{
    el.innerHTML = '✅ Token configurado: <code>' + (d.sapisid_prefix || '') + '</code>';
    el.className = 'ok';
  }} else {{
    el.innerHTML = '❌ Sin token. Sigue las instrucciones.';
    el.className = 'ko';
  }}
}}

async function submitToken() {{
  const token = document.getElementById('tokenInput').value.trim();
  if (!token) return;
  const status = document.getElementById('status');
  status.style.display = 'block';
  status.textContent = 'Guardando...';
  status.style.background = '#1a3a6b';
  status.style.color = '#88bbff';
  try {{
    const r = await fetch('/api/token/set', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{sapisid: token}})
    }});
    const d = await r.json();
    if (d.ok) {{
      status.innerHTML = '✅ Token guardado! (<code>' + d.prefix + '...</code>)';
      status.style.background = '#1a6b3c';
      status.style.color = '#88ff88';
      checkStatus();
    }} else {{
      status.textContent = '❌ Error: ' + (d.error || 'desconocido');
      status.style.background = '#6b1a1a';
      status.style.color = '#ff8888';
    }}
  }} catch (err) {{
    status.textContent = '❌ Error: ' + err.message;
    status.style.background = '#6b1a1a';
    status.style.color = '#ff8888';
  }}
}}
</script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.end_headers()
        self.wfile.write(html.encode())

    def _handle_token_set(self, data):
        """Recibe SAPISID del usuario."""
        sapisid = data.get("sapisid", "").strip()
        if not sapisid or len(sapisid) < 20:
            self._send(*error_response("SAPISID inválido (muy corto)"))
            return
        ok = token_mgr.set_token(sapisid)
        if ok:
            info = token_mgr.get_info()
            self._send(*json_response({
                "ok": True,
                "prefix": info["sapisid_prefix"],
            }))
        else:
            self._send(*error_response("Error al guardar token"))

    def _handle_stream(self):
        """Sirve el audio actual."""
        with player_lock:
            mode = player.current_mode
            stream_url = player.stream_url
            cache_path = player.cache_path

        if mode == "proxy" and stream_url:
            # Proxy directo a Google con Range forwarding
            range_header = self.headers.get("Range", "")
            try:
                req = urllib.request.Request(stream_url)
                if range_header:
                    req.add_header("Range", range_header)
                resp = urllib.request.urlopen(req, timeout=10)
                content_type = resp.headers.get("Content-Type", "audio/webm")
                content_length = resp.headers.get("Content-Length")
                content_range = resp.headers.get("Content-Range", "")
                http_status = resp.status

                # Pasar headers al cliente
                if range_header and http_status == 206:
                    self.send_response(206)
                    if content_range:
                        self.send_header("Content-Range", content_range)
                else:
                    self.send_response(200)

                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Accept-Ranges", "bytes")
                if content_length and not range_header:
                    self.send_header("Content-Length", content_length)
                self.end_headers()

                # Proxy directo desde la respuesta abierta (sin cerrar)
                try:
                    resp.fp._sock.settimeout(5) if hasattr(resp.fp, '_sock') else None
                    while True:
                        try:
                            chunk = resp.read(65536)
                        except socket.timeout:
                            logger.debug("Stream read timeout, continuando")
                            continue
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            logger.info("Client disconnected from stream")
                            break
                except (BrokenPipeError, ConnectionResetError, OSError):
                    logger.info("Client disconnected from stream")
                finally:
                    resp.close()
            except Exception as e:
                logger.error(f"Proxy error: {e}")
                try:
                    self._send(*error_response("Stream failed", 502))
                except Exception:
                    pass

        elif mode == "file" and cache_path and os.path.exists(cache_path):
            # Servir archivo mp3 con Range support
            file_size = os.path.getsize(cache_path)
            range_header = self.headers.get("Range", "")
            start = 0
            end = file_size - 1

            if range_header.startswith("bytes="):
                ranges = range_header[6:].split("-")
                try:
                    start = int(ranges[0]) if ranges[0] else 0
                    if len(ranges) > 1 and ranges[1]:
                        end = int(ranges[1])
                except ValueError:
                    start = 0

            # Determinar Content-Type por extensión
            ext = os.path.splitext(cache_path)[1].lower()
            ct = {"webm": "audio/webm", "mp3": "audio/mpeg"}.get(ext, "audio/webm")

            if start > 0:
                self.send_response(206)
                self.send_header("Content-Range",
                                f"bytes {start}-{end}/{file_size}")
                content_length = end - start + 1
            else:
                self.send_response(200)
                content_length = file_size

            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(content_length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            # Leer y enviar el rango
            with open(cache_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(65536, remaining)
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    remaining -= len(chunk)
        else:
            self._send(*error_response("No active stream", 404))

    # ── POST ─────────────────────────────────────────────

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = read_body(self)
            data = json.loads(body) if body else {}
        except Exception:
            self._send(*error_response("Invalid JSON body", 400))
            return

        try:
            if path == "/api/search":
                self._handle_search(data)
            elif path == "/api/play":
                self._handle_play(data)
            elif path == "/api/pause":
                self._handle_pause()
            elif path == "/api/resume":
                self._handle_resume()
            elif path == "/api/stop":
                self._handle_stop()
            elif path == "/api/next":
                self._handle_next()
            elif path == "/api/prev":
                self._handle_prev()
            elif path == "/api/queue/add":
                self._handle_queue_add(data)
            elif path == "/api/queue/remove":
                self._handle_queue_remove(data)
            elif path == "/api/cookies/upload":
                self._handle_cookies_upload()
            elif path == "/api/token/set":
                self._handle_token_set(data)
            else:
                self._send(*error_response("Not found", 404))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as e:
            logger.exception(f"POST {path}: {e}")
            try:
                self._send(*error_response(str(e), 500))
            except Exception:
                pass

    def _handle_search(self, data):
        query = data.get("query", "").strip()
        if not query:
            self._send(*error_response("query required"))
            return
        results = resolver.search(query)
        self._send(*json_response({"ok": True, "results": results}))

    def _handle_play(self, data):
        video_id = data.get("video_id", "")
        if not video_id:
            self._send(*error_response("video_id required"))
            return
        with player_lock:
            ok = player.play(video_id)
        if ok:
            s = update_state()
            self._send(*json_response({"ok": True, **s}))
        else:
            self._send(*error_response("Play failed", 500))

    def _handle_pause(self):
        with player_lock:
            player.toggle_pause()
        s = update_state()
        self._send(*json_response({"ok": True, **s}))

    def _handle_resume(self):
        with player_lock:
            if player.paused:
                player.toggle_pause()
        s = update_state()
        self._send(*json_response({"ok": True, **s}))

    def _handle_cookies_upload(self):
        """Recibe archivo cookies.txt via multipart/form-data."""
        content_type = self.headers.get("Content-Type", "")
        boundary = None
        if "boundary=" in content_type:
            boundary = content_type.split("boundary=")[1].split(";")[0].strip()
            if boundary.startswith('"') and boundary.endswith('"'):
                boundary = boundary[1:-1]

        if not boundary:
            self._send(*error_response(
                "Content-Type must be multipart/form-data with boundary"))
            return

        # Leer body crudo
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            self._send(*error_response("No data received"))
            return
        body = self.rfile.read(length)

        # Parsear multipart manualmente
        boundary_bytes = f"--{boundary}".encode()
        parts = body.split(boundary_bytes)
        cookies_content = None

        for part in parts:
            if b"Content-Disposition" in part and b'name="cookies"' in part:
                # Encontrar el inicio del contenido (después de doble \\r\\n)
                idx = part.find(b"\r\n\r\n")
                if idx > 0:
                    cookies_content = part[idx + 4:]
                    # Quitar trailing \\r\\n-- (cierre del multipart)
                    if cookies_content.endswith(b"\r\n"):
                        cookies_content = cookies_content[:-2]
                    break

        if not cookies_content:
            self._send(*error_response(
                "No se encontró el archivo 'cookies' en el upload"))
            return

        # Validar que parece un archivo cookies.txt Netscape
        decoded = cookies_content.decode("utf-8", errors="replace")
        lines = [l.strip() for l in decoded.split("\n") if l.strip()]
        has_netscape = any(
            l.startswith("# Netscape HTTP Cookie File") or
            l.startswith("# HTTP Cookie File")
            for l in lines
        )
        has_youtube = any(".youtube.com" in l for l in lines)
        cookie_count = len([l for l in lines if not l.startswith("#") and "\t" in l])

        if not has_youtube:
            self._send(*error_response(
                "No se encontraron cookies de .youtube.com. "
                "Asegúrate de exportar desde youtube.com"))
            return

        # Guardar
        try:
            from resolver import COOKIES_PATH as CP
            with open(CP, "w") as f:
                f.write(decoded)
            domains = set()
            for l in lines:
                if not l.startswith("#") and "\t" in l:
                    parts = l.split("\t")
                    if len(parts) >= 1:
                        domains.add(parts[0])
            logger.info(f"Cookies guardadas: {cookie_count} cookies, "
                       f"{len(domains)} dominios")
            self._send(*json_response({
                "ok": True,
                "count": cookie_count,
                "domains": list(domains)[:5],
            }))
        except Exception as e:
            logger.error(f"Error guardando cookies: {e}")
            self._send(*error_response(f"Error guardando: {e}"))

    def _handle_stop(self):
        with player_lock:
            player.stop()
        s = update_state()
        self._send(*json_response({"ok": True, **s}))

    def _handle_next(self):
        with player_lock:
            player.play_next()
        s = update_state()
        self._send(*json_response({"ok": True, **s}))

    def _handle_prev(self):
        with player_lock:
            player.play_prev()
        s = update_state()
        self._send(*json_response({"ok": True, **s}))

    def _handle_queue_add(self, data):
        video_id = data.get("video_id", "")
        if not video_id:
            self._send(*error_response("video_id required"))
            return
        with player_lock:
            ok = player.add_to_queue(video_id)
        if ok:
            s = update_state()
            self._send(*json_response({"ok": True, **s}))
        else:
            self._send(*error_response("Failed to add to queue"))

    def _handle_queue_remove(self, data):
        index = data.get("index", -1)
        if index < 0:
            self._send(*error_response("index required"))
            return
        with player_lock:
            ok = player.remove_from_queue(index)
        if ok:
            s = update_state()
            self._send(*json_response({"ok": True, **s}))
        else:
            self._send(*error_response("Invalid index"))


# ── main ─────────────────────────────────────────────────

def main():
    # Crear directorio static si no existe
    os.makedirs(STATIC_DIR, exist_ok=True)

    # Limpiar cache viejo al arrancar
    transcoder.cleanup_old_cache()

    # Iniciar refresh loop de token
    token_mgr.start_refresh_loop()

    # Iniciar servidor con reuse address
    # Crear socket manualmente para setear SO_REUSEADDR antes del bind
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(("0.0.0.0", PORT))
    sock.listen(5)

    # ThreadPool: manejar requests en paralelo para que stream no bloquee API
    import socketserver

    class ThreadedPlayMe(socketserver.ThreadingMixIn, HTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    # Crear server con bind_and_activate=False para usar socket manual
    server = ThreadedPlayMe(("", PORT), PlayMeHandler, bind_and_activate=False)
    server.socket = sock
    server.server_address = sock.getsockname()
    logger.info(f"PlayMe v2 corriendo en http://0.0.0.0:{PORT}")
    logger.info(f"Cache: /tmp/playme_cache")
    logger.info(f"Logs: {log_dir}/playme.log")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        transcoder.stop()
        server.shutdown()

if __name__ == "__main__":
    main()
