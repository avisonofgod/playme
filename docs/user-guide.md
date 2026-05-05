# PlayMe - Guía de Usuario

## 🎵 ¿Qué es PlayMe?

PlayMe es un reproductor de música **solo audio** que busca y reproduce música desde YouTube sin anuncios, usando **yt-dlp** y **mpv**.

### ✨ Características
- ✅ **Solo audio** - Sin video, solo sonido
- ✅ **Reproducción inmediata** - La primera canción empieza en ~2 segundos
- ✅ **Búsqueda inteligente** - Encuentra música relacionada automáticamente
- ✅ **Cero configuración** - No necesitas API keys de YouTube
- ✅ **Interfaz animada** - Disco giratorio que reacciona a la música
- ✅ **Cola dinámica** - Las canciones relacionadas se agregan automáticamente

---

## 🚀 Instalación

### Prerrequisitos
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3 python3-tk mpv yt-dlp

# Verificar instalación
mpv --version
yt-dlp --version
python3 --version
```

### Descargar PlayMe
```bash
cd /home/river
git clone <repo-url> playme  # O usar directorio existente
cd playme
```

---

## 🎮 Cómo Usar

### 1. Iniciar PlayMe
```bash
cd /home/river/playme
python3 main.py
```

O usa el script:
```bash
./run.sh
```

### 2. Buscar Música
1. Escribe el nombre de la canción o artista en la caja de búsqueda
2. Presiona **Enter** o haz clic en **Play**
3. ¡La música empieza en ~2 segundos!

**Ejemplo**: Escribe `salmos cantados` y presiona Enter

### 3. Controles
| Botón | Función |
|-------|----------|
| **Play** | Busca y reproduce música |
| **Stop** | Detiene la reproducción y limpia la cola |
| **Pause** | Pausa/Reanuda la música |
| **Next** | Salta a la siguiente canción en la cola |

### 4. Calidad de Audio
Selecciona la calidad en la parte inferior:
- **Best** - Mejor calidad disponible
- **High** - 192 kbps o menos
- **Med** - 128 kbps o menos
- **Low** - 96 kbps o menos

### 5. Cola de Reproducción
- La lista "Queue (similar music)" muestra las canciones pendientes
- Doble-clic en una canción para saltar directamente a ella
- La canción actual tiene el símbolo "▶"

---

## 🔧 Solución de Problemas

### PlayMe no inicia
```bash
# Verificar que mpv y yt-dlp están instalados
which mpv yt-dlp

# Ver logs
tail -f /home/river/playme/logs/playme.log
```

### No se escucha audio
```bash
# Verificar dispositivos de audio
aplay -l

# Probar mpv manualmente
mpv --no-video "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Código viejo se ejecuta (errores extraños)
```bash
# Limpiar cache de Python
find /home/river/playme -name "*.pyc" -delete
find /home/river/playme -name "__pycache__" -type d -exec rm -rf {} +
```

### Error "Failed to connect to mpv IPC socket"
- **Solución**: PlayMe ya no usa IPC socket. Asegúrate de ejecutar la versión nueva.

### Error 403 Forbidden
- **Causa**: URLs expiradas (ya no debería ocurrir en PlayMe)
- **Solución**: Reinicia PlayMe

---

## 📊 Monitoreo

### Ver logs en tiempo real
```bash
tail -f /home/river/playme/logs/playme.log
```

### Verificar que PlayMe está corriendo
```bash
ps aux | grep -E "(main.py|mpv)" | grep -v grep
```

### Reiniciar PlayMe
```bash
pkill -9 -f "main.py|mpv"
cd /home/river/playme && python3 main.py &
```

---

## 🎯 Casos de Uso Común

### Reproducir una canción específica
1. Escribe: `Imagine Dragons Believer`
2. Enter → Empieza en ~2 segundos

### Descubrir música similar
1. Busca una canción que te guste
2. PlayMe automáticamente agrega 5 canciones similares a la cola
3. Usa **Next** para explorar

### Crear sesión continua de música
1. Busca el primer tema
2. Deja que PlayMe llene la cola automáticamente
3. La música continúa sin parar

---

## ⌨️ Atajos de Teclado

| Tecla | Acción |
|-------|--------|
| **Enter** | Buscar/Reproducir |
| **Space** | Pausa/Reanuda |

---

## 📝 Notas Técnicas

- **URLs**: PlayMe usa URLs de YouTube (`watch?v=`) en lugar de media URLs directas para evitar expiración
- **Reproducción**: mpv maneja la transmisión de audio usando su soporte integrado de yt-dlp
- **Pausa**: Se implementa con `signal.SIGSTOP/SIGCONT` (no IPC)
- **Búsqueda**: yt-dlp busca automáticamente agregando "music" a la consulta

---

## 🆘 Soporte

Si encuentras problemas:
1. Revisa logs: `/home/river/playme/logs/playme.log`
2. Verifica que `mpv` y `yt-dlp` funcionan manualmente
3. Asegúrate de estar usando la versión correcta (sin IPC socket)

---

**¡Disfruta tu música con PlayMe!** 🎉
