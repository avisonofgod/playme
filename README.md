# PlayMe

> Un reproductor de música solo audio que busca y reproduce música desde YouTube sin anuncios, usando **yt-dlp** y **mpv**.

## ✨ Características

- 🎵 **Solo audio** - Sin video, solo sonido
- ⚡ **Reproducción inmediata** - La primera canción empieza en ~2 segundos
- 🔍 **Búsqueda inteligente** - Encuentra música relacionada automáticamente en background
- 🚀 **Cero configuración** - No necesitas API keys de YouTube
- 🎨 **Interfaz animada** - Disco giratorio que reacciona a la música
- 📋 **Cola dinámica** - Las canciones relacionadas se agregan automáticamente

## 🚀 Instalación Rápida

```bash
# Prerrequisitos
sudo apt update
sudo apt install python3 python3-tk mpv yt-dlp

# Clonar (si no lo tienes ya)
cd /home/river
git clone <repo-url> playme  # O usa directorio existente

# Ejecutar
cd playme
python3 main.py
```

## 🎮 Uso Básico

1. Escribe el nombre de la canción o artista
2. Presiona **Enter**
3. ¡La música empieza en ~2 segundos!
4. PlayMe automáticamente agrega música similar a la cola

**Ejemplo**: Escribe `salmos cantados` y presiona Enter

## 📖 Documentación

- [Guía de Usuario](docs/user-guide.md) - Cómo usar PlayMe
- [Arquitectura](docs/architecture.md) - Diseño y funcionamiento interno
- [Documentación Técnica](docs/technical.md) - Detalles para desarrolladores

## 🔧 Solución de Problemas

### PlayMe no inicia
```bash
# Verificar instalación
which mpv yt-dlp python3

# Ver logs
tail -f logs/playme.log
```

### No se escucha audio
```bash
# Verificar dispositivos
aplay -l

# Probar mpv manualmente
mpv --no-video "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Reiniciar PlayMe
```bash
pkill -9 -f "main.py|mpv"
cd /home/river/playme && python3 main.py &
```

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
├── docs/                # Documentación completa
│   ├── architecture.md  # Arquitectura del sistema
│   ├── user-guide.md    # Guía de usuario
│   └── technical.md     # Documentación técnica
└── README.md            # Este archivo
```

## 🔍 Cómo Funciona

1. Usuario escribe consulta → `ui.py`
2. Player inicia reproducción → `player.py`
3. Resolver obtiene URL de YouTube → `resolver.py`
4. Engine lanza mpv con la URL → `engine.py`
5. Primera canción empieza (~2s)
6. Background thread busca música relacionada
7. Cola se llena automáticamente
8. Al terminar → `play_next()` reproduce siguiente

## 🆚 Diferencias con playbin original

| Característica | playbin | PlayMe |
|---------------|---------|--------|
| URLs | Media URLs (`-g`) | YouTube watch URLs |
| Control | IPC socket | Signals (SIGSTOP/SIGCONT) |
| Video | Sí (opcional) | No (solo audio) |
| Búsqueda | Solo query actual | Primera + related en background |
| API | yt-dlp | yt-dlp (sin ytmusicapi) |

## 📝 Notas Técnicas

- **URLs**: Usa URLs de YouTube (`watch?v=`) para evitar expiración
- **Reproducción**: mpv con soporte integrado de yt-dlp
- **Pausa**: Implementada con `signal.SIGSTOP/SIGCONT`
- **Búsqueda**: yt-dlp con "music" agregado a la consulta

## ⌨️ Atajos de Teclado

| Tecla | Acción |
|-------|--------|
| Enter | Buscar/Reproducir |
| Space | Pausa/Reanuda |

## 🐛 Reporte de Errores

Si encuentras problemas:
1. Revisa logs: `tail -f logs/playme.log`
2. Verifica que `mpv` y `yt-dlp` funcionan manualmente
3. Crea un issue en el repositorio

## 📄 Licencia

MIT License - Ver LICENSE para más detalles

---

**Desarrollado con ❤️ para amantes de la música** 🎵
