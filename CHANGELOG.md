# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.1.0/). Versionado: SemVer.

## [1.3.0] - 2026-09-24

### Añadido
- **Reproducción en vivo (stream progresivo)**: el audio empieza a sonar con los
  primeros ~256 KB (~5 s) en vez de esperar al 100% de la descarga. `download_bg()`
  ya no bloquea (arranca en background y es idempotente), `wait_partial()` espera los
  primeros bytes y `GET /api/stream` sirve el `.part` mientras crece (sin
  `Content-Length`); al completarse la descarga queda el archivo en caché y el stream
  pasa a responder 206 con Range (seek). Variables: `PLAYME_PARTIAL_WAIT` (25 s),
  `PLAYME_PARTIAL_MIN_BYTES` (262144), `PLAYME_STREAM_STALE` (90 s).
- Estado `streaming` en `/api/state`; la UI muestra la insignia `file · en vivo`,
  los MB descargados y usa la duración conocida para la barra de progreso.
- **Cambiar de tema corta el actual de inmediato**: pedir otro tema (`play`) limpia
  el estado de reproducción en el acto (`playing=false`, `current=null`) en vez de
  seguir sonando "encima" mientras se resuelve el nuevo; la UI corta el `<audio>`
  (`stopAudio`) y el nuevo tema arranca con sus primeros bytes. Repetir el MISMO
  tema no corta nada. El anterior sigue en la cola (botón ⏮ vuelve a él).

### Corregido
- **Botón "Vaciar descargas"**: no hacía nada porque `window.confirm()` en el WebView
  no está implementado (sin `WebChromeClient` devuelve `false`) y `cleanDl()` salía
  antes de llamar al API. Ahora usa un modal propio (`askConfirm`) y, además, la app
  implementa `onJsConfirm`/`onJsAlert` con `AlertDialog`.
- `_audio_convert()` (cola de Descargas) esperaba el fin de `download_bg()` de forma
  implícita; ahora usa `wait_done()` explícito tras hacerlo asíncrono.

## [1.2.1] - 2026-09-15

### Corregido
- **Reproducción/descarga en el móvil**: la app forzaba `player_client=ios`/`android_vr` y
  nunca probaba el cliente **por defecto** de yt-dlp, que es el que funciona (probado con
  el mismo yt-dlp y la misma salida a Internet que el PC). Ahora el orden de intentos es:
  por defecto → por defecto sin cookies → `ios` sin cookies → `android_vr`.
  Resultado en dispositivo: reproducción `mode=file` con stream 206 y descarga real.

## [1.2.0] - 2026-09-15

### Añadido
- Servicio en primer plano (`PlaymeService`) con `MediaSession`: la app conserva la red con
  la pantalla apagada (MagicOS/Doze) — verificado en dispositivo.
- `netdiag.py` + `GET /api/netdiag`: diagnóstico de red/DNS dentro de la app.
- Modo archivo preferido en Android: descarga el audio y lo reproduce local (`mode=file`).
- Si el proxy recibe 403, cae automáticamente al modo archivo.

### Corregido
- El permiso de notificaciones se pide **después** de arrancar: pedirlo en `onCreate` impedía
  abrir la app en MagicOS.
- Android 14: el tipo `mediaPlayback` exige una `MediaSession` activa; sin ella
  `startForeground` mataba el proceso.
- Audio con el cliente `ios` de yt-dlp (descarga sin runtime JS). `android_vr` da 403 al
  bajar y `web_embedded`/`mweb`/`tv` fallan con "page needs to be reloaded" sin solver EJS.
- La descarga reintenta sin cookies cuando corresponde (el cliente `ios` no las soporta).
- `print()` en `dns_java.py` corrompía el JSON de yt-dlp (búsquedas vacías en algunos móviles).
- Cola: sin duplicados al reproducir el mismo vídeo; una pista pedida durante la resolución
  ya no se pierde; borrar la pista en curso conserva el resto.
- `onReceivedError` solo actúa en el frame principal (antes recargaba la UI por un subrecurso).
- Log con rotación (1 MB × 2) y sin registrar cada `GET /api/state`; caché en `getCacheDir()`
  con tope de 500 MB (LRU).

### Cambiado
- APK solo `arm64-v8a` + `armeabi-v7a` (fuera `x86_64`, +11 MB): ~20 MB.
- `yt-dlp` fijado a `2026.8.19` para que una release nueva no cambie el APK sin tocar el repo.
- UI unificada: `frontend/index.html` es la misma que usa la app.

## [1.1.0] - 2026-09-11

- App Android autónoma (Chaquopy 16.1.0 + yt-dlp embebido), servidor en `127.0.0.1:8191`.
- Cookie de YouTube por login en el WebView; cookie Netscape reparada automáticamente.
- DNS vía `java.net.InetAddress` (`dns_java.py`), temporales explícitos, descarga de audio
  original (webm/m4a) con `DownloadManager` (sin ffmpeg no hay mp3).

## [1.0.0] - 2026-09-11

- Primera versión: servidor Linux (búsqueda, cola, streaming, conversión mp3), UI web de
  tres pestañas y APK cliente (WebView).
