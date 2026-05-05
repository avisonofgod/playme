# PlayMe - Documentación de Arquitectura

## 📋 Índice
1. [Visión General](#visión-general)
2. [Flujo de Funcionamiento](#flujo-de-funcionamiento)
3. [Componentes](#componentes)
4. [Decisiones de Diseño](#decisiones-de-diseño)
5. [Comparación con playbin original](#comparación-con-playbin-original)
6. [API Reference](#api-reference)

---

## 🎯 Visión General

**PlayMe** es un reproductor de música de solo audio que utiliza **yt-dlp** para buscar y reproducir música desde YouTube sin anuncios, usando **mpv** como motor de reproducción.

### Características principales:
- ✅ Solo audio (`--no-video`)
- ✅ Primera canción se reproduce **inmediatamente** (~2 segundos)
- ✅ Búsqueda de música relacionada en **background**
- ✅ Cero configuración (sin YouTube API)
- ✅ Sin IPC socket (usa `signal.SIGSTOP/SIGCONT` para pausa)
- ✅ URLs de YouTube (no media URLs que expiran)

---

## 🔄 Flujo de Funcionamiento

### Diagrama de flujo:
```
Usuario escribe "salmos cantados"
         ↓
    [UI] Entry → Button
         ↓
    [Player.play(query)]
         ↓
    ┌──────────────────────┐
    │                      │
    ↓                      ↓
[Resolver.resolve()]   [Background Thread]
    ↓                      │
[yt-dlp --get-id]         │
    ↓                      │
[YouTube URL]             │
    ↓                      │
[Engine.play(url)]        │
    ↓                      │
[mpv --no-video          │
 --ytdl-format=bestaudio]│
    ↓                      │
[Reproduce instantáneamente] │
                           │
                           ↓
                  [Resolver.search_related()]
                           ↓
                  [Queue se llena con 5 items]
                           ↓
                  [Al terminar canción → play_next()]
```

### Paso a paso:

#### 1. Entrada del usuario (`ui.py`)
```python
# ui.py:112-118
def play(self):
    query = self.entry.get().strip()
    if query:
        self.apply_quality()
        self.status.config(text="Searching...", fg="orange")
        self.player.play(query)
```

#### 2. Player inicia reproducción (`player.py`)
```python
# player.py:53-71
def play(self, query: str):
    self.queue = [query]
    self.current_index = 0
    
    # 1. Reproduce PRIMERA canción INMEDIATAMENTE
    logger.info(f"Playing first result immediately: {query}")
    self.engine.set_on_finish(self.play_next)
    self._resolve_and_play(query)
    
    # 2. Busca música relacionada en BACKGROUND (no bloqueante)
    def search_background():
        similar = self.resolver.search_related(query, limit=5)
        if similar:
            self.queue.extend([q for q in similar if q not in self.queue])
            if self.on_queue_update:
                self.on_queue_update()
    
    threading.Thread(target=search_background, daemon=True).start()
```

#### 3. Resolver obtiene URL (`resolver.py`)
```python
# resolver.py:17-34
def resolve(self, query: str):
    """Get YouTube watch URL (not media URL to avoid expiration)"""
    output = subprocess.check_output([
        self.ytdlp,
        "--get-id",
        f"ytsearch1:{query} music"  # Agrega "music" para mejores resultados
    ]).decode().strip()
    
    video_id = output.split("\n")[0]
    url = f"https://www.youtube.com/watch?v={video_id}"
    return url
```

#### 4. Engine reproduce con mpv (`engine.py`)
```python
# engine.py:28-48
def play(self, youtube_url):
    """Play YouTube URL (mpv will use built-in ytdl to stream audio)"""
    args = [
        "mpv",
        "--no-video",                    # Solo audio
        "--ytdl-format=bestaudio",       # Formato de audio
        youtube_url                      # URL de YouTube (no media URL)
    ]
    
    self.process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,      # Evita buffer overflow
        stderr=subprocess.DEVNULL
    )
    
    # Monitor para detectar cuando termina
    monitor = threading.Thread(target=self._monitor_playback, daemon=True)
    monitor.start()
```

#### 5. Monitoreo de reproducción (`engine.py`)
```python
# engine.py:18-25
def _monitor_playback(self):
    """Monitor when mpv finishes playing"""
    if not self.process:
        return
    self.process.wait()  # Espera a que mpv termine
    if self.on_finish:
        self.on_finish()  # Trigger next song
```

---

## 🧩 Componentes

### 1. `main.py` - Punto de entrada
**Responsabilidad**: Inicializar logging y lanzar la aplicación.

```python
# main.py:1-33
def main():
    # Configura logging dual: archivo + consola
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[
            logging.FileHandler('logs/playme.log'),
            logging.StreamHandler()
        ]
    )
    
    player = Player()
    ui = UI(player)
    ui.run()
```

**Logging**:
- `/home/river/playme/logs/playme.log` - DEBUG level
- Formato: `timestamp - logger_name - LEVEL - message`

---

### 2. `resolver.py` - Resolución de URLs y búsqueda
**Responsabilidad**: Obtener URLs de YouTube y buscar música relacionada usando **solo yt-dlp**.

#### Métodos:
- **`resolve(query)`**: Obtiene URL de YouTube para una búsqueda
  - Usa `yt-dlp --get-id ytsearch1:{query} music`
  - Retorna: `https://www.youtube.com/watch?v={video_id}`
  - **Por qué no usar `-g`**: Las media URLs expiran rápido (403 Forbidden)

- **`search_related(query, limit=5)`**: Busca música relacionada
  - Usa `yt-dlp --flat-playlist -J ytsearch{limit+1}:{query} music similar`
  - Retorna: Lista de títulos de canciones
  - **Por qué `--flat-playlist`**: Solo necesitamos metadatos, no descargar

- **`set_quality(audio_fmt)`**: Cambia calidad de audio
  - Opciones: `bestaudio`, `bestaudio[abr<=192]`, `bestaudio[abr<=128]`, `bestaudio[abr<=96]`

---

### 3. `engine.py` - Motor de reproducción
**Responsabilidad**: Controlar el proceso de mpv usando `subprocess.Popen`.

#### Características:
- **Sin IPC socket**: El playbin original usaba `--input-ipc-server` que causaba errores de conexión
- **Pausa con signals**: `signal.SIGSTOP` (pausa) y `signal.SIGCONT` (reanuda)
- **Monitoreo en background**: Thread que detecta cuando termina la canción

#### Métodos:
- **`play(youtube_url)`**: Inicia reproducción
  - Ejecuta: `mpv --no-video --ytdl-format=bestaudio {url}`
  - **Por qué `subprocess.DEVNULL`**: Evita buffer overflow que causaba salida inmediata

- **`stop()`**: Termina proceso mpv
  - Usa `process.terminate()` y `process.wait(timeout=5)`

- **`set_pause(pause: bool)`**: Pausa/reanuda
  - **Por qué signals**: Simple, confiable, sin dependencias de IPC

- **`set_on_finish(callback)`**: Registra callback para cuando termina canción

---

### 4. `player.py` - Lógica de negocio
**Responsabilidad**: Gestionar cola de reproducción, estados y coordinar resolver/engine.

#### Estados:
- `self.playing`: Bool - Si está reproduciendo
- `self.paused`: Bool - Si está pausado
- `self.queue`: List[str] - Cola de canciones
- `self.current_index`: int - Índice actual en la cola

#### Métodos clave:
- **`play(query)`**: 
  1. Limpia cola y agrega query actual
  2. Reproduce INMEDIATAMENTE primera canción
  3. Lanza búsqueda de relacionados en **background thread**

- **`play_next()`**: 
  - Avanza al siguiente item en cola
  - Si no hay más, `playing = False`

- **`_resolve_and_play(query)`**:
  1. Llama a `resolver.resolve(query)` para obtener URL
  2. Llama a `engine.play(url)` para reproducir
  3. Actualiza estado y UI callbacks

#### Callbacks (UI updates):
- `on_play`: Se ejecuta cuando empieza reproducción
- `on_error`: Se ejecuta en caso de error
- `on_queue_update`: Se ejecuta cuando cambia la cola

---

### 5. `ui.py` - Interfaz gráfica
**Responsabilidad**: Mostrar interfaz de usuario con Tkinter.

#### Componentes visuales:
1. **Disco animado**: 
   - Canvas con círculos concéntricos y rayos
   - Animación basada en `self.player.playing` y `self.player.paused`
   - Efecto de "energía" que varía entre 0.0 y 1.0

2. **Entrada de búsqueda**:
   - Entry box con binding `<Return>` para play y `<space>` para pausa

3. **Cola de reproducción** (Listbox):
   - Muestra canciones en cola
   - Doble-click para saltar a canción
   - Prefijo "▶" para canción actual

4. **Controles**:
   - Play, Stop, Pause, Next buttons
   - Radio buttons para calidad de audio

#### Métodos importantes:
- **`update_queue_display()`**: Actualiza Listbox con `self.player.get_queue()`
- **`animate_disc()`**: Animación recursiva cada 30ms
- **`on_queue_select(event)`**: Salta a canción seleccionada

---

## 🤔 Decisiones de Diseño

### 1. ¿Por qué URLs de YouTube en lugar de media URLs?
**Problema**: `yt-dlp -g` devuelve media URLs que expiran en segundos → 403 Forbidden

**Solución**: Usar `yt-dlp --get-id` para obtener video ID, luego formar `https://www.youtube.com/watch?v={id}`

**Beneficio**: mpv tiene soporte ytdl integrado que maneja expiración automáticamente

---

### 2. ¿Por qué `signal.SIGSTOP/SIGCONT` en lugar de IPC socket?
**Problema playbin original**: 
```python
# playbin original engine.py
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(self.socket_path)  # Fallaba después de 30 intentos
```

**Solución PlayMe**:
```python
# playme engine.py
if pause:
    self.process.send_signal(signal.SIGSTOP)  # Para proceso
else:
    self.process.send_signal(signal.SIGCONT)  # Reanuda proceso
```

**Beneficio**: 
- Sin errores de conexión
- Sin complejidad de JSON IPC commands
- Simple y confiable

---

### 3. ¿Por qué primera canción inmediata + background search?
**Experiencia de usuario**:
- Usuario escucha música en ~2 segundos
- Búsqueda de relacionados no bloquea la interfaz
- Cola se llena automáticamente mientras escucha

**Implementación**:
```python
# player.py
def play(self, query):
    # 1. Inmediato (bloqueante, pero rápido ~2s)
    self._resolve_and_play(query)
    
    # 2. Background (no bloqueante)
    threading.Thread(target=search_background, daemon=True).start()
```

---

### 4. ¿Por qué solo yt-dlp (sin ytmusicapi)?
**Requerimiento del usuario**: "que youtube ni se de cuenta" (cero configuración)

**Ventajas**:
- No requiere API key de YouTube
- No requiere configuración OAuth
- yt-dlp es más resiliente a cambios de YouTube

**Búsqueda de relacionados**:
```python
# Usa yt-dlp --flat-playlist en lugar de ytmusicapi
output = subprocess.check_output([
    self.ytdlp,
    "--flat-playlist",
    "-J",  # JSON output
    f"ytsearch{limit+1}:{query} music similar"
])
```

---

### 5. ¿Por qué `subprocess.DEVNULL` en mpv?
**Problema**: Usar `subprocess.PIPE` causaba buffer overflow → mpv salía inmediatamente

**Solución**: Redirigir stdout/stderr a DEVNULL

**Alternativa considerada**: Usar `timeout` y `--ao=null` para testing, pero el usuario necesita audio real

---

## 📊 Comparación con playbin original

| Aspecto | playbin original | PlayMe | Razón del cambio |
|---------|------------------|--------|------------------|
| **URLs** | Media URLs (`-g` flag) | YouTube watch URLs | Las media URLs expiran (403) |
| **Control** | IPC socket (`--input-ipc-server`) | Signals (SIGSTOP/SIGCONT) | IPC daba "30 attempts failed" |
| **Video** | Soporte video+audio | Solo audio (`--no-video`) | Usuario pidió "music-only" |
| **Búsqueda** | Solo query actual | Primera canción inmediata + related en background | Mejor UX |
| **API** | Solo yt-dlp | Solo yt-dlp | Usuario rechazó ytmusicapi |
| **Pausa** | JSON IPC commands | `signal.SIGSTOP/SIGCONT` | Más simple y confiable |
| **Cola** | History (20 items) | Queue dinámica con related | Paradigma de playlist |
| **Logging** | No configurado | DEBUG a archivo + consola | Debugging y monitoreo |

---

## 🔧 API Reference

### Resolver
```python
class Resolver:
    def __init__(self):
        self.ytdlp = "yt-dlp"
        self.audio_fmt = "bestaudio"
    
    def resolve(self, query: str) -> str:
        """Retorna URL de YouTube para la primera búsqueda"""
        
    def search_related(self, query: str, limit: int = 5) -> List[str]:
        """Retorna lista de títulos de música relacionada"""
        
    def set_quality(self, audio_fmt=None):
        """Cambia formato de audio"""
```

### Engine
```python
class MPVEngine:
    def __init__(self):
        self.process = None
        self.on_finish = None
    
    def play(self, youtube_url: str):
        """Reproduce URL de YouTube con mpv"""
        
    def stop(self):
        """Detiene reproducción"""
        
    def set_pause(self, pause: bool):
        """Pausa/reanuda con signals"""
        
    def set_on_finish(self, callback):
        """Registra callback para fin de reproducción"""
```

### Player
```python
class Player:
    def __init__(self):
        self.resolver = Resolver()
        self.engine = MPVEngine()
        self.queue = []
        self.current_index = -1
        self.playing = False
        self.paused = False
        
        # Callbacks
        self.on_play = None
        self.on_error = None
        self.on_queue_update = None
    
    def play(self, query: str):
        """Reproduce canción y busca relacionados en background"""
        
    def play_next(self):
        """Avanza a siguiente canción en cola"""
        
    def stop(self):
        """Detiene reproducción y limpia cola"""
        
    def toggle_pause(self):
        """Alterna pausa/reproducción"""
        
    def get_queue(self) -> List[str]:
        """Retorna cola actual"""
```

---

## 🚀 Comandos Útiles

```bash
# Lanzar PlayMe
cd /home/river/playme && python3 main.py

# O usar script
./run.sh

# Monitorear logs en vivo
tail -f /home/river/playme/logs/playme.log

# Verificar que está corriendo
ps aux | grep -E "(main.py|mpv)" | grep -v grep

# Reiniciar si es necesario
pkill -9 -f "main.py|mpv"
cd /home/river/playme && python3 main.py &

# Limpiar cache de Python (si ejecuta código viejo)
find /home/river/playme -name "*.pyc" -delete
find /home/river/playme -name "__pycache__" -type d -exec rm -rf {} +
```

---

## 📝 Notas de Desarrollo

### Problemas encontrados y soluciones:
1. **mpv sale inmediatamente**: Causado por buffer overflow con PIPE → Usar DEVNULL
2. **403 Forbidden**: Media URLs expiran → Usar YouTube watch URLs
3. **IPC socket errors**: Complejidad innecesaria → Usar signals
4. **Código viejo se ejecuta**: Cache de Python → Limpiar `__pycache__`
5. **Audio no funciona**: Problemas con pulseaudio → Dejar que mpv use default

### Testing:
```bash
# Test resolver
python3 -c "from resolver import Resolver; r = Resolver(); print(r.resolve('test'))"

# Test engine
python3 -c "from engine import MPVEngine; e = MPVEngine(); e.play('https://www.youtube.com/watch?v=dQw4w9WgXcQ')"

# Test player (sin GUI)
python3 -c "from player import Player; p = Player(); p.play('salmos cantados')"
```

---

**Fin de documentación** 🎉
