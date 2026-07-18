# PlayMe v2

> Servidor HTTP de streaming de audio desde YouTube.
> Busca, reproduce y encola canciones via API REST.
> Frontend web single-page con soporte de cola, next/prev, y streaming proxy.

## Arquitectura

```
┌─────────────────────────────────────────────────────┐
│                    Navegador Web                      │
│  (index.html) polling cada 2s a /api/state           │
│  Audio via <audio src="/api/stream">                 │
└────────────┬────────────────────────────────────────┘
             │ HTTP (GET/POST)
             ▼
┌─────────────────────────────────────────────────────┐
│  PlayMeHandler (ThreadedHTTPServer — puerto 8090)    │
│                                                      │
│  GET  /api/state    → estado actual (polling)        │
│  POST /api/search   → busca en YouTube               │
│  POST /api/play     → reproduce por video_id         │
│  POST /api/pause    → toggle pausa                   │
│  POST /api/next     → siguiente en cola              │
│  POST /api/prev     → anterior en cola               │
│  POST /api/stop     → detener y limpiar cola         │
│  POST /api/queue/add    → agregar a cola             │
│  POST /api/queue/remove → quitar de cola             │
│  GET  /api/stream   → proxy del audio de YouTube     │
│  GET  /api/cookies  → página para subir cookies      │
│  GET  /api/token    → página para configurar SAPISID │
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
   ┌────────────┐
   │ yt-dlp CLI │
   │ ──get-url  │
   │ ──flat-pl. │
   │ ──cookies  │
   └────────────┘
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

### 4. Sincronización Frontend
```
1. DOMContentLoaded → startPolling()
2. Cada 2s: GET /api/state
3. Si current.id cambió → audioElement.src = '/api/stream'
4. Si playing=false → stopAudio()
5. Botones: POST /api/{play,pause,next,prev,stop}
6. Enter en search: POST /api/search → renderResults()
7. Click ▶ en resultado: POST /api/play
```

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
- yt-dlp 2026+ (resolución de URLs)
- mpv (caché en background, fallback)
- Deno (compatibilidad con EJS challenge solver)

## Ejecución

```bash
# systemd (producción)
systemctl start playme.service
# Puerto: 8090 (configurable via PORT env)

# Manual (desarrollo)
cd /root/playme && PORT=8090 python3 server.py
```

## Archivos Clave

```
/root/playme/
├── server.py         # HTTP Server + API REST (PlayMeHandler)
├── player.py         # Lógica de negocio, cola, coordinación
├── resolver.py       # yt-dlp wrapper (buscar, resolver URL, info)
├── transcoder.py     # mpv wrapper (caché en background)
├── token_manager.py  # SAPISID token + cookies.txt builder
├── static/
│   └── index.html    # Frontend SPA (vanilla JS, polling)
├── logs/
│   ├── playme.log    # Log principal
│   └── errors.log    # Stderr del servicio
├── cookies.txt       # Cookies de YouTube (generado automáticamente)
├── token.txt         # SAPISID token (configuración única)
└── README.md         # Este archivo
```

## Mejoras Recientes (Julio 2026)

1. **HEAD /api/stream**: Soporte para navegador (antes daba 501)
2. **ThreadingMixIn**: Stream no bloquea otras requests API
3. **SO_REUSEADDR**: Socket configurado antes del bind para reinicios limpios
4. **Timeout en proxy**: 5s de timeout en lectura para evitar cuelgues
5. **3 estrategias de resolución**: ANDROID_VR → creativecommons → fallback
6. **Cache en background**: Descarga paralela mientras reproduce en proxy

## Pruebas Realizadas (Julio 2026)

- [x] Buscar canciones (10 resultados)
- [x] Reproducir en modo proxy
- [x] HEAD /api/stream (200 OK)
- [x] State polling durante reproducción
- [x] Agregar a cola (múltiples items)
- [x] Next / Prev
- [x] Pause / Resume
- [x] Remover de cola
- [x] Stop
- [x] Concurrencia (buscar mientras reproduce)
- [x] Caché en background
