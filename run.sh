#!/bin/bash
# PlayMe Application Launcher with full error capture
# Captures all output to logs for debugging

APP_DIR="/home/river/playme"
LOG_DIR="$APP_DIR/logs"
ERROR_LOG="$LOG_DIR/errors.log"
STDOUT_LOG="$LOG_DIR/stdout.log"

mkdir -p "$LOG_DIR"

echo "$(date): Starting PlayMe..." >> "$LOG_DIR/playme.log"

cd "$APP_DIR" || exit 1

# Kill any existing mpv processes
pkill -f "mpv" 2>/dev/null
pkill -f "main.py" 2>/dev/null
sleep 1

# Run with all output captured
python3 main.py >> "$STDOUT_LOG" 2>> "$ERROR_LOG" &

PID=$!
echo "PlayMe started with PID: $PID"
echo "Monitor logs with: tail -f $LOG_DIR/playme.log"
echo "Check errors with: tail -f $ERROR_LOG"
