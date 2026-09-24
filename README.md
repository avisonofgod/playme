# PlayMe v2

> Servidor HTTP de streaming de audio desde YouTube.
> Busca, reproduce, encola y descarga canciones en mp3 via API REST.
> Frontend web single-page con tabs (Reproducir | Cola | Descargas), polling a `/api/state` y streaming proxy.
>
> **App Android autónoma (v1.3.0)**: APK que corre todo dentro del móvil (Python + yt-dlp),
> sin PC ni servidor — ver [App Android autónoma](#app-android-autónoma-android-app--v120) y
> [Releases](https://github.com/avisonofgod/playme/releases). Cambios por versión: `CHANGELOG.md`.

## Estructura del repositorio

```
Playme/
├── backend/
│   ├── server.py              # HTTP Server + API REST (Handler) + Conversor mp3
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
│  Handler (ThreadingHTTPServer — puerto 8191)    │
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
│  GET  /api/ip              → IP pública (detección local)   │
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

> **En la app Android (`android-app/`)**: sin ffmpeg no hay mp3. La pestaña Descargas
> baja el AUDIO ORIGINAL del cache (`GET /api/download/audio/{id}`, webm/m4a) y el
> movil lo guarda en Descargas con DownloadManager. El mp3 sigue en el servidor Linux.


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
| GET | `/api/state` | Estado actual (polling) |
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
cd /root/proyectos/Playme/backend && PORT=8191 python3 server.py

# Producción (systemd) → ver INSTALL.md
systemctl start playme.service
# Puerto: 8191 (configurable via PORT env)
```

## Android (APK WebView) — cliente ligero (legacy)

> La vía recomendada es la **app autónoma** (sección siguiente). Este APK es un cliente
> WebView que solo abre la UI del servidor: sigue sirviendo si ya tienes PlayMe en un PC
> o VPS, pero depende de él.

APK precompilado: **GitHub Releases** → `PlayMe-1.0.apk` (appId `playme.webview`, minSdk 24).
Firma: `CN=Riveros` (keystore `android/playme-release.keystore`, NO versionado).

Instalación:
1. Instala el APK en el móvil (adb install -r o abrir el archivo).
2. Al abrir por primera vez, escribe la dirección del servidor (`IP_DEL_SERVER:8191`).
   Orden de autodetección: última URL guardada → `127.0.0.1:8191` → IP pública:8191 → diálogo manual.
3. El servidor debe estar arriba (`systemctl status playme`) y el móvil alcanzarlo
   (misma red WiFi/LAN o IP pública con el puerto 8191 accesible; se permite HTTP en claro).

**El móvil NO necesita cookies.** Quien resuelve YouTube es el servidor, con la cookie de
`/root/proyectos/Playme/cookies.txt` (extraída del Firefox del servidor). La sesión de la
app YouTube de Android no se usa (no es posible extraer cookies de una app Android sin root).

Recompilar:
```bash
bash android/build-apk.sh        # aapt2 + javac + d8 + zipalign + apksigner (SDK en /opt/android-sdk)
```

## App Android autónoma (`android-app/`) — v1.3.0

> Nota yt-dlp 2026-09: YouTube exige a veces un runtime JS (solver EJS) que Android no
> lleva. En esos casos el cliente `ios` sigue descargando bien (verificado); el resto
> (web_embedded/mweb/tv) falla con "page needs to be reloaded" y android_vr da 403 al
> bajar. Por eso la app usa `player_client=ios` para el audio (modo archivo) y cae a
> modo archivo si el proxy recibe 403.

APK **independiente**: PlayMe corre DENTRO del móvil (Python 3.11 + yt-dlp embebidos con
Chaquopy), escucha en `127.0.0.1:8191` y se muestra en un WebView. No necesita PC ni servidor.
- `android-app/app/src/main/python/` → backend portado (mismas clases, rutas por variables de entorno).
- `playme_boot.py` → arranca el backend en el dispositivo; `ytdlp_inproc.py` → ejecuta yt-dlp en proceso
  (en Android no hay binario ni subprocess util).
- Cookie de YouTube: la app abre el login de YouTube en su WebView y captura SID/HSID
  (`CookieManager`) escribiendo `cookies.txt` en `filesDir`; botón "Guardar cookie de YouTube".
  Si ya existe cookie válida, arranca directo. Si la cookie caduca, se reintenta sin ella.
- Audio con el cliente `ios` de yt-dlp (descarga sin runtime JS); si el proxy recibe 403
  cae al modo archivo. `PlaymeService` (primer plano + MediaSession) mantiene la red con la
  pantalla apagada.
- Compilar: `cd android-app && ./gradlew assembleRelease` (JDK 21, Android SDK 35,
  Chaquopy descarga Python; en este PC: `JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64`,
  `ANDROID_HOME=/opt/android-sdk`, `GRADLE_USER_HOME=/home/.gradle`).
- Firmado con el keystore de Riveros (`android/playme-release.keystore`, no versionado).
- Requisitos: `minSdk 24`, `targetSdk 35`, ABIs `arm64-v8a` + `armeabi-v7a` (APK ~20 MB).

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
android/              # APK WebView: MainActivity.java (puerto 8191), build-apk.sh, dist/
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

```bash
# tests del backend (Linux): 52 en verde
cd backend && python3 -m unittest discover -s . -p 'test_*.py'
# suite de la app Android (host; añadir --device con el móvil conectado)
bash android-app/tests/run-tests.sh [--device]
```

### Android (app autónoma v1.2.0, HONOR BRP-NX3 / Android 16)

- [x] Arranque en MagicOS (el permiso de notificaciones se pide después de arrancar)
- [x] Servidor local 127.0.0.1:8191 en el móvil (acceso desde el PC con `adb forward`)
- [x] Buscar y reproducir en el dispositivo (modo archivo, `mode=file`, stream 206)
- [x] Pausa / reanudar en el dispositivo (`paused:true` / `false`)
- [x] Cola: agregar + `next` cambia de pista
- [x] Descarga real a `/sdcard/Download` (Adele 5.846.859 B, Ana Becoa 5.594.197 B ×2)
- [x] Red con la pantalla apagada (servicio en primer plano + MediaSession)
- [x] Suite del repo: 14/14 TODO-OK (host) con el modo archivo y la descarga
- [x] La suite marca SKIP (no FAIL) cuando el fallo es el límite de YouTube (PO token/SABR),
      para no confundir un problema externo con un fallo del código
- [ ] Reproducción/descarga cuando YouTube exige PO token/EJS en la red del móvil
      (límite externo, issue yt-dlp 12482; requiere runtime JS embebido)
