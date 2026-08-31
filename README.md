# PlayMe v2

> Servidor HTTP de streaming de audio desde YouTube.
> Busca, reproduce, encola y descarga canciones en mp3 via API REST.
> Frontend web single-page con tabs (Reproducir | Cola | Descargas), polling a `/api/state` y streaming proxy.

## Estructura del repositorio

```
Playme/
├── backend/
│   ├── server.py              # HTTP Server + API REST (PlayMeHandler) + Conversor mp3
│   ├── player.py              # Lógica de negocio: cola, reproducción, next/prev
│   ├── resolver.py            # yt-dlp wrapper (búsqueda, stream URL, metadata)
│   ├── transcoder.py          # mpv wrapper (caché webm en background)
│   ├── token_manager.py       # SAPISID token → cookies.txt builder + refresh
│   ├── firefox_cookies.py     # Extracción de cookies de Firefox → cookies.txt
│   ├── cookie_parser.py       # Parser de cookies (Netscape format)
│   ├── normalize_cookies.py   # Normalización y dedupe de cookies
│   ├── runner.py              # Carga del servidor + configuración de cookies
│   ├── verify_firefox_cookies.py  # Verificación de cookies extraídas
│   └── test_*.py              # Suite de tests (52 casos)
├── frontend/
│   └── index.html             # Frontend SPA (vanilla JS, polling)
├── README.md                  # Este archivo
├── INSTALL.md                 # Instalación para producción
├── COOKIES_SETUP.md           # Guía de configuración de cookies
└── .gitignore
```

> Nota: el servidor sirve el frontend desde `frontend/` (`STATIC = dirname(dirname(__file__))/frontend`). No hay carpeta `static/`.

## Arquitectura

```
┌─────────────────────────────────────────────────────┐
│                    Navegador Web                      │
│  (frontend/index.html) polling 2s a /api/state       │
│  Audio via <audio src="/api/stream">                 │
└────────────┬────────────────────────────────────────┘
             │ HTTP (GET/POST)
             ▼
┌─────────────────────────────────────────────────────┐
│  PlayMeHandler (ThreadedHTTPServer — puerto 8090)    │
│                                                      │
│  GET  /api/state            → estado actual (polling)│
│  POST /api/search           → busca en YouTube       │
│  POST /api/play             → reproduce por video_id │
│  POST /api/pause|resume     → pausar / reanudar      │
│  POST /api/next|prev|stop   → navegación en cola     │
│  POST /api/queue/add        → agregar a cola         │
│  POST /api/queue/remove     → quitar de cola         │
│  GET  /api/stream           → proxy del audio        │
│  POST /api/convert          → conversión a mp3       │
│  GET  /api/download/mp3/{id}→ descarga mp3           │
│  POST /api/conversions      → estado de descargas    │
│  POST /api/clean/dl         → vaciar lista descargas │
│  GET  /api/cookies|token    → configuración cookies  │
└────────────┬────────────────────────────────────────┘
             │
        ┌────┴────────────┬──────────────┐
        ▼                 ▼              ▼
   ┌─────────┐    ┌────────────┐   ┌───────────┐
   │ Resolver │    │  Player   │   │Transcoder │
   │(yt-dlp)  │◄───┤(cola+     │   │(mpv cache)│
   │ search() │    │ estado)   │   │           │
   │get_stream│    │           │   │ start_file│
   │_url()    │    │ play()    │   │ is_cached │
   │get_info()│    │ stop()    │   │           │
   └────┬─────┘    └───────────┘   └───────────┘
        │
        ▼
   ┌────────────┐   ┌────────────┐
   │ yt-dlp CLI │   │ ffmpeg     │
   │ ──get-url  │   │ webm→mp3   │
   │ ──flat-pl. │   │ 320kbps    │
   └────────────┘   └────────────┘
```

## Flujo de Reproducción

### 1. Buscar
```
POST /api/search {"query": "musica hebrea"}
  → Resolver.search()
    → yt-dlp --flat-playlist -J ytsearch10:musica hebrea
    → parsea JSON con id, title, duration, uploader, thumbnail
  ← 10 resultados
```

### 2. Reproducir
```
POST /api/play {"video_id": "7xIhGS6cIJM"}
  → Player.play(video_id)
    → Resolver.get_info() — metadata del video
    → ¿Ya está en cache? Sí → modo "file" (archivo local)
    → No → Resolver.get_stream_url()
      → 3 estrategias en orden:
        1. default (ANDROID_VR client)
        2. android_creativecommons
        3. web_creativecommons
      → yt-dlp --get-url --format bestaudio <url>
      → Extrae última línea http...
    → ¿stream_url? Sí → modo "proxy"
      → thread background: transcoder.start_file()
        → mpv --stream-record /tmp/playme_cache/<id>.webm
    → ¿stream_url? No → modo "file" (mpv bloqueante)
    → Actualiza cola y estado
  ← estado actualizado
```

### 3. Escuchar (Streaming Proxy)
```
GET /api/stream
  → Modo "proxy":
    → urllib.request al stream_url de Google
    → Forward de Range headers (soporte seek)
    → Forward de Content-Type, Content-Length
    → Bucle: read(65536) → wfile.write() → flush()
    → Timeout 5s por lectura (no cuelga si Google tarda)
    → Client disconnect: sale limpiamente
  → Modo "file":
    → Sirve archivo /tmp/playme_cache/<id>.webm
    → Soporte Range (206 Partial Content)
  → HEAD /api/stream: responde 200 (necesario para navegador)
```

## Conversión y Descarga (mp3)

```
1. DW (por fila o en cola) → POST /api/convert {"video_id","title"}
     → thread en background _run_conv(video_id)
     → Fase 1: yt-dlp descarga bestaudio → /tmp/playme_mp3/<id>.webm (timeout 600s)
     → Fase 2: ffmpeg convierte webm → mp3 320kbps + metadatos ID3
2. Frontend monitorea con pollDl() → POST /api/conversions (polling 1.5s)
3. GET /api/download/mp3/{id} → si mp3 listo, sirve el archivo con Content-Disposition
4. POST /api/clean/dl       → vacía SOLO la lista de descargas (UI).
                               NO borra los archivos mp3 del disco.

Estado de cada conversión: {"status": queued|converting|ready|error,
                            "progress": 0-100, "title": ...}
```

## Calidad de audio

| Modo | Formato | Bitrate |
|------|---------|---------|
| Streaming proxy | Opus (webm) | ~160kbps (bestaudio) |
| Descarga mp3 | MP3 (libmp3lame) | 320kbps |
| Caché | Opus (webm) | ~160kbps |

## Página de Descargas (tab)

- Muestra cada ítem convertido con progreso, estado y enlace de descarga cuando está `ready`.
- El botón **"🗑 Vaciar descargas"** (`#clearDlBtn`) envía `POST /api/clean/dl` que vacía la lista en memoria (y se reconstruye al arrancar desde los mp3 del disco). No toca los archivos.
- Límite de entradas: `_MAX_CONVERSIONS = 50` (eviction de terminadas más antiguas).

## API Endpoints (resumen)

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/` rep `/index.html` | Frontend web |
| POST | `/api/state` | Estado actual (polling) |
| POST | `/api/search` | Buscar en YouTube |
| POST | `/api/play` | Reproducir por video_id |
| POST | `/api/pause` / `/api/resume` | Pausar / reanudar |
| POST | `/api/next` / `/api/prev` / `/api/stop` | Navegación en cola |
| POST | `/api/queue/add` / `/api/queue/remove` | Gestionar cola |
| GET | `/api/stream` | Streaming de audio |
| POST | `/api/convert` | Iniciar conversión a mp3 |
| GET | `/api/download/mp3/{id}` | Descargar mp3 convertido |
| POST | `/api/conversions` | Listar conversiones (estado) |
| POST | `/api/clean/dl` | Vaciar lista de descargas (solo UI) |
| GET | `/api/ip` | IP pública del servidor |
| GET | `/api/cookies` / `/api/token` | Páginas de configuración |

## Estrategias de Resolución de Stream

| Orden | Estrategia | Timeout | Ideal para |
|-------|-----------|---------|------------|
| 1 | default (ANDROID_VR) | 10s | Videos normales, populares |
| 2 | android_creativecommons | 10s | Videos con restricción |
| 3 | web_creativecommons | 10s | Fallback general |

El SAPISID token permite que yt-dlp evite bloqueos CGNAT. El TokenManager refresca las cookies cada 6h.

## Sistema de Cache

- **Ubicación**: `/tmp/playme_cache/`
- **Formato**: `<video_id>.webm` (audio webm/opus nativo)
- **Descarga**: mpv --stream-record en background (no bloquea)
- **Limpieza**: Automática al iniciar (archivos >24h)
- **Prioridad**: Si existe en cache, sirve directo (modo "file") sin llamar a YouTube

## Dependencias

- Python 3.12+
- yt-dlp 2026+ (resolución de URLs y conversión)
- mpv (caché en background, fallback)
- ffmpeg (conversión webm → mp3)
- Deno (compatibilidad con EJS challenge solver)

## Ejecución

```bash
# Manual (desarrollo)
cd /root/proyectos/Playme/backend && PORT=8090 python3 server.py

# Producción (systemd) → ver INSTALL.md
systemctl start playme.service
# Puerto: 8090 (configurable via PORT env)
```

## Archivos Clave

```
backend/
├── server.py         # HTTP Server + API REST + Conversor mp3
├── player.py         # Lógica de negocio, cola, coordinación
├── resolver.py       # yt-dlp wrapper (buscar, resolver URL, info)
├── transcoder.py     # mpv wrapper (caché en background)
├── token_manager.py  # SAPISID token + cookies.txt builder
├── firefox_cookies.py# Extracción cookies desde Firefox
└── runner.py         # Carga principal
frontend/
└── index.html        # Frontend SPA (vanilla JS, polling, 3 tabs)
logs/                 # playme.log + errors.log
```

## Mejoras Recientes

1. **HEAD /api/stream**: Soporte para navegador (antes daba 501)
2. **ThreadingMixIn**: Stream no bloquea otras requests API
3. **SO_REUSEADDR**: Socket configurado antes del bind para reinicios limpios
4. **Timeout en proxy**: 5s de timeout en lectura para evitar cuelgues
5. **3 estrategias de resolución**: ANDROID_VR → creativecommons → fallback
6. **Cache en background**: Descarga paralela mientras reproduce en proxy
7. **3 tabs en UI**: Reproducir | Cola | Descargas
8. **Play/DW por fila**: botones en resultados y en la cola (usan la clave real `it.id`)
9. **Descarga mp3**: conversión a mp3 320kbps + endpoint `/api/convert` + lista de descargas
10. **Vaciar descargas**: `/api/clean/dl` vacía la lista sin borrar los archivos
11. **Cookies desde Firefox**: extracción automática de cookies del navegador

## Pruebas Realizadas

- [x] Buscar canciones (10 resultados)
- [x] Reproducir en modo proxy (con resaltado de fila en reproducción)
- [x] HEAD /api/stream (200 OK)
- [x] State polling durante reproducción
- [x] Agregar a cola (múltiples items) + remover por fila
- [x] Next / Prev / Stop
- [x] Pause / Resume
- [x] Play / DW desde filas de la cola (clave `it.id`)
- [x] Conversión y descarga mp3
- [x] Vaciar descargas (solo lista, archivos intactos)
- [x] Concurrencia (buscar mientras reproduce)
- [x] Caché en background
- [x] 52 tests backend en verde
