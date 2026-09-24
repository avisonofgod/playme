# PlayMe - YouTube Audio Streaming Server

Servidor HTTP para buscar, reproducir en streaming y descargar en mp3 audio desde YouTube.

## Arquitectura

```
Browser ← HTTP → Handler (socketserver.ThreadingMixIn + HTTPServer :8191)
                    ├── Resolver (yt-dlp: búsqueda, stream URL, metadata)
                    ├── Player (cola, reproducción, next/prev)
                    ├── Transcoder (caché webm en /tmp/playme_cache)
                    ├── TokenManager (SAPISID → cookies.txt)
                    ├── FirefoxCookies (extracción cookies de Firefox)
                    └── Converter (yt-dlp + ffmpeg → mp3 en background)
```

## Estructura del repositorio

```
Playme/
├── backend/
│   ├── server.py              # HTTP Server + API REST + Conversor mp3
│   ├── player.py              # Lógica de negocio: cola, reproducción
│   ├── resolver.py            # yt-dlp wrapper (búsqueda, stream, metadata)
│   ├── transcoder.py          # Caché de audio webm (mpv)
│   ├── token_manager.py       # Token SAPISID → cookies.txt
│   ├── firefox_cookies.py     # Extracción de cookies de Firefox
│   ├── cookie_parser.py       # Parser formato Netscape
│   ├── normalize_cookies.py   # Normalización de cookies
│   ├── runner.py              # Carga principal del servidor
│   ├── verify_firefox_cookies.py  # Verificación de cookies
│   └── test_*.py              # Suite (52 tests)
├── frontend/
│   └── index.html             # Frontend SPA (tabs: Reproducir/Cola/Descargas)
├── README.md
├── INSTALL.md                 # Este archivo
├── COOKIES_SETUP.md           # Guía de configuración de cookies
└── .gitignore
```

> Importante: el frontend vive en `frontend/index.html`. El servidor lo localiza en `backend/server.py` mediante `STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")`
    → resuelve a `/root/proyectos/Playme/frontend` (2 niveles desde `backend/server.py`). **No** existe una carpeta `static/`.

## Requisitos

- **Python 3.12+**
- **yt-dlp** (resolución YouTube y conversión)
- **ffmpeg** (conversión a mp3)
- **mpv** (caché de audio en background)
- **Deno** (EJS challenge solver para yt-dlp 2026+)

### Instalación de dependencias

```bash
# Python
apt update && apt install -y python3 python3-pip

# yt-dlp
pip3 install yt-dlp

# ffmpeg + mpv
apt install -y ffmpeg mpv

# Deno (para EJS challenge de yt-dlp)
curl -fsSL https://deno.land/install.sh | sh
ln -sf /root/.deno/bin/deno /usr/local/bin/deno

# Verificar
yt-dlp --version
ffmpeg -version | head -1
deno --version | head -1
```

## Instalación

```bash
# Clonar repositorio
git clone git@github.com:avisonofgod/playme.git /root/proyectos/Playme
cd /root/proyectos/Playme

# Crear estructura de directorios
mkdir -p logs

# Configurar cookies (ver COOKIES_SETUP.md)
# - Opcional: generar automáticamente desde Firefox vía backend/firefox_cookies.py
# - Opcional: pegar manualmente en backend/cookies.txt
```

## Ejecución

### Producción (systemd)

```bash
# Copiar servicio (ajustar WorkingDirectory/ExecStart a tu ruta real)
cat > /etc/systemd/system/playme.service << 'EOF'
[Unit]
Description=PlayMe - YouTube Audio Streaming
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/proyectos/Playme/backend
Environment=PORT=8191
ExecStart=/usr/bin/python3 /root/proyectos/Playme/backend/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Activar e iniciar
systemctl daemon-reload
systemctl enable playme
systemctl start playme

# Verificar
systemctl status playme
curl http://localhost:8191/api/state
```

### Desarrollo (manual)

```bash
cd /root/proyectos/Playme/backend
python3 server.py
# Servidor en http://localhost:8191
```

## API Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/` rep `/index.html` | Frontend web |
| POST | `/api/state` | Estado actual (polling) |
| POST | `/api/search` | Buscar en YouTube. Body: `{"query": "..."}` |
| POST | `/api/play` | Reproducir. Body: `{"video_id": "..."}` |
| POST | `/api/pause` / `/api/resume` | Pausar / reanudar |
| POST | `/api/next` / `/api/prev` / `/api/stop` | Navegación en cola |
| POST | `/api/queue/add` | Agregar a cola. Body: `{"video_id":"...","title":"..."}` |
| POST | `/api/queue/remove` | Quitar de cola. Body: `{"index":0}` |
| GET | `/api/stream` | Streaming de audio (proxy o file) |
| POST | `/api/convert` | Iniciar conversión a mp3. Body: `{"video_id":"...","title":"..."}` |
| GET | `/api/download/mp3/{id}` | Descargar mp3 convertido |
| POST | `/api/conversions` | Listar conversiones (estado) |
| POST | `/api/clean/dl` | Vaciar lista descargas (solo UI, NO borra archivos) |
| GET | `/api/ip` | IP pública del servidor |

## Flujo de reproducción

1. Buscar: `POST /api/search` → 10 resultados (id, title, duration, uploader)
2. Reproducir: `POST /api/play` → resuelve stream URL (yt-dlp --get-url)
   - Si existe en caché → modo file (instantáneo)
   - Si no → modo proxy (streaming) + descarga a caché en background
3. Streaming: `GET /api/stream` → proxy de Google CDN con timeout 5s/lectura
4. Controles: pause, resume, next, prev, stop
5. Polling: frontend consulta `/api/state` cada 2s para UI

## Conversión y descarga mp3

1. DW (en fila o cola) → `POST /api/convert` → thread en background
2. Fase 1: yt-dlp descarga bestaudio a webm (timeout 600s)
3. Fase 2: ffmpeg convierte webm → mp3 320kbps con metadatos ID3
4. Frontend monitorea con `POST /api/conversions` (polling 1.5s)
5. Cuando status = "ready", muestra enlace de descarga (`/api/download/mp3/{id}`)
6. `POST /api/clean/dl` vacía SOLO la lista de descargas (archivos mp3 intactos)

## Directorios temporales

| Directorio | Propósito |
|------------|-----------|
| `/tmp/playme_cache/` | Caché de audio webm (reproducción) |
| `/tmp/playme_mp3/` | mp3 convertidos + titles.json |

## Cookies y tokens

- Ver `COOKIES_SETUP.md` para la guía completa.
- `backend/firefox_cookies.py` extrae cookies de Firefox y genera `cookies.txt`.
- `backend/token_manager.py` gestiona el SAPISID token y refresca cookies cada 6h.
- Los archivos `cookies.txt`, `token.txt`, `cookies_master.txt` están en `.gitignore` (no se versionan).

## Solución de problemas

```bash
# Verificar que el servicio corre
systemctl status playme

# Verificar puerto
ss -tlnp | grep 8191

# Logs
tail -f /root/proyectos/Playme/logs/playme.log

# Probar API
curl http://localhost:8191/api/state

# Cache de audio
ls /tmp/playme_cache/

# Descargas mp3
ls /tmp/playme_mp3/

# Reiniciar servicio
systemctl restart playme

# Si el puerto está ocupado
fuser -k 8191/tcp
systemctl start playme
```

## Pruebas (backend)

```bash
cd /root/proyectos/Playme/backend
python3 -m unittest discover -s . -p 'test_*.py'
# Ran 52 tests ... OK
```

## App Android autonoma (v1.3.0)

APK que corre PlayMe dentro del movil (Python 3.11 + yt-dlp con Chaquopy), sin PC:
`android-app/dist/PlayMe-Local-1.3.0.apk` (arm64-v8a + armeabi-v7a, ~20 MB).
Compilar: `cd android-app && ./gradlew assembleRelease` con
`JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64`, `ANDROID_HOME=/opt/android-sdk`,
`GRADLE_USER_HOME=/home/.gradle`. Suite: `bash android-app/tests/run-tests.sh [--device]`.
Si el build falla con "SDK location not found" (local.properties no se versiona):
`echo sdk.dir=/opt/android-sdk > android-app/local.properties`.
