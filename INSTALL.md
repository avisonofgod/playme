# PlayMe - YouTube Audio Streaming Server

Servidor HTTP para buscar, reproducir en streaming y descargar en mp3 audio desde YouTube.

## Arquitectura

```
Browser ← HTTP → PlayMeHandler (ThreadedHTTPServer :8090)
                    ├── Resolver (yt-dlp: búsqueda, stream URL, metadata)
                    ├── Player (cola, reproducción, next/prev)
                    ├── Transcoder (caché webm en /tmp/playme_cache)
                    ├── TokenManager (SAPISID → cookies.txt)
                    └── Converter (yt-dlp + ffmpeg → mp3 en background)
```

## Requisitos

- **Python 3.12+**
- **yt-dlp** (resolución YouTube)
- **ffmpeg** (conversión a mp3)
- **Deno** (EJS challenge solver para yt-dlp 2026+)

### Instalación de dependencias

```bash
# Python
apt update && apt install -y python3 python3-pip

# yt-dlp
pip3 install yt-dlp

# ffmpeg
apt install -y ffmpeg

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
git clone git@github.com:avisonofgod/playme.git /root/playme
cd /root/playme

# Archivos del proyecto:
#   server.py          - HTTP Server + API + Conversor mp3
#   player.py          - Cola y reproducción
#   resolver.py        - Búsqueda y resolución YouTube
#   transcoder.py      - Caché de audio webm
#   token_manager.py   - Token SAPISID
#   static/index.html  - Frontend web

# Crear estructura de directorios
mkdir -p logs static

# Configurar token SAPISID (opcional, para videos bloqueados)
echo "fH88mDHf_dXdqmGj/Avq22eV_AV3IREbDb" > token.txt
# ^ Reemplazar con tu SAPISID real (de DevTools de Chrome en youtube.com)
```

## Ejecución

### Producción (systemd)

```bash
# Copiar servicio
cat > /etc/systemd/system/playme.service << 'EOF'
[Unit]
Description=PlayMe - YouTube Audio Streaming
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/playme
Environment=PORT=8090
ExecStart=/usr/bin/python3 /root/playme/server.py
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
curl http://localhost:8090/api/state
```

### Desarrollo (manual)

```bash
cd /root/playme
python3 server.py
# Servidor en http://localhost:8090
```

## API Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/` | Frontend web |
| GET | `/api/state` | Estado actual (cola, reproducción, conversiones) |
| POST | `/api/search` | Buscar en YouTube. Body: `{"query": "..."}` |
| POST | `/api/play` | Reproducir. Body: `{"video_id": "..."}` |
| POST | `/api/pause` | Pausar/reanudar |
| POST | `/api/stop` | Detener y limpiar cola |
| POST | `/api/next` | Siguiente en cola |
| POST | `/api/prev` | Anterior en cola |
| POST | `/api/queue/add` | Agregar a cola. Body: `{"video_id":"...","title":"..."}` |
| POST | `/api/queue/remove` | Quitar de cola. Body: `{"index":0}` |
| GET | `/api/stream` | Streaming de audio (proxy o file) |
| POST | `/api/convert` | Iniciar conversión a mp3. Body: `{"video_id":"...","title":"..."}` |
| GET | `/api/download/mp3/{id}` | Descargar mp3 convertido |
| POST | `/api/conversions` | Listar conversiones activas |
| POST | `/api/clean/dl` | Limpiar todas las descargas |

## Flujo de reproducción

1. Buscar: `POST /api/search` → lista de resultados (id, title, duration, uploader)
2. Reproducir: `POST /api/play` → resuelve stream URL (yt-dlp --get-url)
   - Si existe en caché → modo file (instantáneo)
   - Si no → modo proxy (streaming) + descarga a caché en background
3. Streaming: `GET /api/stream` → proxy de Google CDN con idle timeout 30s
4. Controles: pause, resume, next, prev, stop
5. Polling: frontend consulta `/api/state` cada 2s para UI

## Conversión a mp3

1. Usuario hace clic en DW → `POST /api/convert` → thread en background
2. Fase 1: yt-dlp descarga bestaudio a webm (timeout 600s)
3. Fase 2: ffmpeg convierte webm → mp3 320kbps con metadatos ID3
4. Frontend monitorea con polling cada 1.5s
5. Cuando status = "ready", muestra link de descarga

## Calidad de audio

| Modo | Formato | Bitrate |
|------|---------|---------|
| Streaming proxy | Opus (webm) | ~160kbps (bestaudio) |
| Descarga mp3 | MP3 (libmp3lame) | 320kbps |
| Caché | Opus (webm) | ~160kbps |

## Tokens y autenticación

Para videos bloqueados por región o CGNAT, se requiere token SAPISID:

1. Abrir youtube.com en Chrome con sesión iniciada
2. DevTools → Application → Cookies → youtube.com
3. Copiar valor de SAPISID
4. Pegar en `token.txt` (un token por línea)
5. El servidor construye cookies.txt automáticamente y lo refresca cada 6h

## Solución de problemas

```bash
# Verificar que el servicio corre
systemctl status playme

# Verificar puerto
ss -tlnp | grep 8090

# Logs
tail -f /root/playme/logs/playme.log

# Probar API
curl http://localhost:8090/api/state

# Cache de audio
ls /tmp/playme_cache/

# Descargas mp3
ls /tmp/playme_mp3/

# Reiniciar servicio
systemctl restart playme

# Si el puerto está ocupado
fuser -k 8090/tcp
systemctl start playme
```

## Archivos importantes

```
/root/playme/
├── server.py          # Servidor HTTP + API
├── player.py          # Lógica de reproducción y cola
├── resolver.py        # Integración con yt-dlp
├── transcoder.py      # Caché de audio
├── token_manager.py   # Gestión de tokens YouTube
├── static/
│   └── index.html     # Frontend (tabs: reproductor/descargas)
├── logs/
│   └── playme.log     # Log principal
├── token.txt          # SAPISID (configuración única)
├── cookies.txt        # Generado automáticamente desde token.txt
└── README.md          # Este archivo
```

## Directorios temporales

| Directorio | Propósito |
|------------|-----------|
| `/tmp/playme_cache/` | Caché de audio webm para reproducción |
| `/tmp/playme_mp3/` | Archivos mp3 convertidos + titles.json |
