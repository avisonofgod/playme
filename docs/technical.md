# PlayMe - Documentación Técnica

## 🏗️ Estructura del Proyecto

```
/home/river/playme/
├── main.py              # Punto de entrada
├── player.py            # Lógica de negocio y cola
├── resolver.py          # Resolución de URLs con yt-dlp
├── engine.py            # Motor de reproducción (mpv)
├── ui.py                # Interfaz gráfica (Tkinter)
├── run.sh               # Script de lanzamiento
├── monitor.sh           # Monitoreo de logs
├── logs/                # Directorio de logs
│   ├── playme.log       # Log principal (DEBUG)
│   ├── errors.log       # Errores
│   └── stdout.log       # Salida estándar
├── docs/                # Documentación
│   ├── architecture.md  # Arquitectura del sistema
│   ├── user-guide.md    # Guía de usuario
│   └── technical.md     # Este archivo
└── README.md            # Descripción general
```

---

## 🔍 Funcionamiento Interno

### Flujo de Datos

```
┌─────────────┐
│   UI (Tk)  │  ← Usuario escribe "salmos cantados"
└──────┬──────┘
       │
       ↓
┌─────────────┐
│   Player    │  ← Coordina lógica de reproducción
└──────┬──────┘
       │
       ├───────────────┐
       ↓               ↓
┌─────────────┐  ┌─────────────┐
│  Resolver   │  │ Background  │
│  (yt-dlp)   │  │  Thread    │
└──────┬──────┘  └──────┬──────┘
       ↓               │
┌─────────────┐  ┌──────┴──────┐
│ YouTube URL │  │ Related    │
│ watch?v=... │  │ Music List │
└──────┬──────┘  └─────────────┘
       ↓
┌─────────────┐
│   Engine    │
│ (mpv proc) │  ← subprocess.Popen
└─────────────┘
```

---

## 🧩 Módulos Detallados

### 1. main.py
**Propósito**: Inicialización y configuración

```python
# Configuración de logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/playme.log'),
        logging.StreamHandler()
    ]
)
```

**Flujo**:
1. Configura logging dual (archivo + consola)
2. Crea instancia de `Player`
3. Crea instancia de `UI` pasando el player
4. Lanza el mainloop de Tkinter

---

### 2. resolver.py
**Propósito**: Obtener URLs de YouTube y buscar música relacionada

#### Método `resolve(query)`
```python
def resolve(self, query: str):
    # Obtiene video ID usando yt-dlp
    output = subprocess.check_output([
        "yt-dlp",
        "--get-id",
        f"ytsearch1:{query} music"  # Agrega "music" para mejores resultados
    ]).decode().strip()
    
    video_id = output.split("\n")[0]
    return f"https://www.youtube.com/watch?v={video_id}"
```

**¿Por qué `--get-id` y no `-g`?**
- `-g` da media URLs que expiran rápido → 403 Forbidden
- `--get-id` da video ID → URL estable que mpv puede manejar

#### Método `search_related(query, limit)`
```python
def search_related(self, query: str, limit: int = 5):
    output = subprocess.check_output([
        "yt-dlp",
        "--flat-playlist",  # Solo metadatos, no descarga
        "-J",               # JSON output
        f"ytsearch{limit+1}:{query} music similar"
    ]).decode().strip()
    
    data = json.loads(output)
    results = []
    if "entries" in data:
        for entry in data["entries"][1:]:  # Skip first (original)
            if "title" in entry:
                results.append(entry["title"])
    return results
```

**¿Por qué `--flat-playlist`?**
- No necesitamos descargar, solo obtener títulos
- Es más rápido que búsqueda normal

---

### 3. engine.py
**Propósito**: Controlar proceso de mpv

#### Método `play(youtube_url)`
```python
def play(self, youtube_url):
    args = [
        "mpv",
        "--no-video",                    # Solo audio
        "--ytdl-format=bestaudio",       # Formato de audio
        youtube_url                      # URL de YouTube
    ]
    
    self.process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,       # Evita buffer overflow
        stderr=subprocess.DEVNULL
    )
```

**¿Por qué `subprocess.DEVNULL`?**
- `subprocess.PIPE` causaba buffer overflow → mpv salía inmediatamente
- `subprocess.DEVNULL` descarta salida, evita el problema

#### Método `set_pause(pause)`
```python
def set_pause(self, pause: bool):
    if pause:
        self.process.send_signal(signal.SIGSTOP)   # Pausa proceso
    else:
        self.process.send_signal(signal.SIGCONT)   # Reanuda proceso
```

**¿Por qué signals y no IPC?**
- IPC socket (`--input-ipc-server`) causaba errores de conexión
- Signals son simples y confiables para pausar procesos

#### Monitoreo de reproducción
```python
def _monitor_playback(self):
    """Hilo que espera a que mpv termine"""
    if not self.process:
        return
    self.process.wait()  # Bloquea hasta que mpv termine
    if self.on_finish:
        self.on_finish()  # Trigger next song
```

---

### 4. player.py
**Propósito**: Gestión de cola y coordinación

#### Estructura de datos
```python
self.queue = []           # Lista de consultas (strings)
self.current_index = -1   # Índice actual en la cola
self.playing = False      # Estado de reproducción
self.paused = False       # Estado de pausa
```

#### Método `play(query)` - Flujo principal
```python
def play(self, query: str):
    # 1. Configura cola
    self.queue = [query]
    self.current_index = 0
    
    # 2. Reproduce INMEDIATAMENTE primera canción
    self.engine.set_on_finish(self.play_next)
    self._resolve_and_play(query)  # ~2 segundos
    
    # 3. Busca relacionados en BACKGROUND (no bloqueante)
    def search_background():
        similar = self.resolver.search_related(query, limit=5)
        if similar:
            self.queue.extend([q for q in similar if q not in self.queue])
            if self.on_queue_update:
                self.on_queue_update()
    
    threading.Thread(target=search_background, daemon=True).start()
```

**¿Por qué background thread?**
- Usuario escucha música inmediatamente (~2s)
- Búsqueda de relacionados (~5s) no bloquea la interfaz

#### Método `play_next()`
```python
def play_next(self):
    if self.current_index < len(self.queue) - 1:
        self.current_index += 1
        next_query = self.queue[self.current_index]
        self._resolve_and_play(next_query)
    else:
        self.playing = False  # End of queue
```

---

### 5. ui.py
**Propósito**: Interfaz gráfica con Tkinter

#### Componentes principales
1. **Canvas (Disco animado)**
   - 16 rayos que reaccionan a `self.player.playing` y `self.player.paused`
   - Animación cada 30ms vía `root.after(30, self.animate_disc)`

2. **Entry (Búsqueda)**
   - Binding: `<Return>` → `self.play()`, `<space>` → `self.toggle_pause()`

3. **Listbox (Cola)**
   - Doble-click para saltar canción
   - Prefijo "▶" para canción actual

4. **Controles (Botones)**
   - Play, Stop, Pause, Next
   - Radio buttons para calidad

#### Animación del disco
```python
def animate_disc(self):
    # Energía: 1.0 si playing y no paused, 0.0 si no
    target = 1.0 if (self.player.playing and not self.player.paused) else 0.0
    self.energy = 0.85 * self.energy + 0.15 * target  # Suavizado
    
    # Rotación basada en energía
    if self.energy > 0.01:
        self.angle = (self.angle + 2 + 6 * self.energy) % 360
    
    # Actualizar coordenadas de rayos...
    self.root.after(30, self.animate_disc)  # 33 FPS
```

---

## 🔄 Diferencias con playbin original

| Característica | playbin | PlayMe | Razón |
|---------------|---------|--------|-------|
| **URLs** | Media URLs (`-g`) | YouTube watch URLs | Evitar expiración (403) |
| **Control** | IPC socket | Signals (SIGSTOP/SIGCONT) | Evitar errores de conexión |
| **Video** | Sí (opcional) | No (solo audio) | Requerimiento usuario |
| **Búsqueda** | Solo query actual | Primera + related en background | Mejor UX |
| **Cola** | History (20 items) | Queue dinámica | Paradigma playlist |
| **API** | yt-dlp | yt-dlp (sin ytmusicapi) | Cero configuración |

---

## 🐛 Debugging

### Logs
```bash
# Ver todos los logs
tail -f /home/river/playme/logs/playme.log

# Buscar errores
grep ERROR /home/river/playme/logs/playme.log

# Verificar flujo
grep -E "(INFO|DEBUG)" /home/river/playme/logs/playme.log | tail -50
```

### Testing de componentes
```bash
# Test Resolver
python3 -c "
from resolver import Resolver
r = Resolver()
print(r.resolve('test song'))
"

# Test Engine
python3 -c "
from engine import MPVEngine
e = MPVEngine()
e.play('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
import time; time.sleep(5); e.stop()
"

# Test Player (sin GUI)
python3 -c "
from player import Player
p = Player()
p.play('salmos cantados')
import time; time.sleep(10); p.stop()
"
```

---

## ⚙️ Configuración

### Logging
Archivo: `main.py`
```python
logging.basicConfig(
    level=logging.DEBUG,  # Cambiar a INFO en producción
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/playme.log'),
        logging.StreamHandler()
    ]
)
```

### Calidad de audio
Ajustar en `resolver.py`:
```python
def set_quality(self, audio_fmt=None):
    if audio_fmt == "best":
        self.audio_fmt = "bestaudio"
    elif audio_fmt == "high":
        self.audio_fmt = "bestaudio[abr<=192]"
    # etc.
```

---

## 🚧 Limitaciones Conocidas

1. **Audio devices**: Puede requerir configuración manual de `ao` en mpv
2. **X display**: Requiere `DISPLAY=:0` y autorización X correcta
3. **yt-dlp rate limits**: YouTube puede limitar búsquedas muy frecuentes
4. **No video**: Por diseño, PlayMe es solo audio

---

## 📈 Mejoras Futuras

- [ ] Soporte para playlists de YouTube
- [ ] Descarga local de canciones
- [ ] Equalizador de audio
- [ ] Lyrics scraping
- [ ] Modo radio (reproducción infinita)
- [ ] Integración con Last.fm

---

**Fin de documentación técnica** 🔧
