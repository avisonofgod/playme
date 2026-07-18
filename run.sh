#!/bin/bash
# PlayMe v2 - Server Launcher
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
PORT="${PORT:-8090}"
mkdir -p "$LOG_DIR"

echo "=== PlayMe v2 ==="
echo "Puerto: $PORT"
echo "Cache: /tmp/playme_cache"
echo "Logs: $LOG_DIR"
echo ""

# Kill existing on our port
fuser -k "${PORT}/tcp" 2>/dev/null
sleep 0.5

# Start
cd "$APP_DIR"
PORT=$PORT python3 server.py >> "$LOG_DIR/stdout.log" 2>> "$LOG_DIR/errors.log" &

PID=$!
echo "PID: $PID"
echo ""
echo "Monitor: tail -f $LOG_DIR/playme.log"
echo "Errores: tail -f $LOG_DIR/errors.log"
echo "Web: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
echo ""

# Verificar que arrancó
sleep 1
if kill -0 $PID 2>/dev/null; then
    echo "✓ PlayMe corriendo (PID $PID)"
else
    echo "✗ PlayMe falló al arrancar"
    tail -5 "$LOG_DIR/errors.log"
    exit 1
fi
