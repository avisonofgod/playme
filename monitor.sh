#!/bin/bash
# PlayMe Log Monitor
# Monitors all logs in real-time for debugging

LOG_FILE="/home/river/playme/logs/playme.log"
ERROR_LOG="/home/river/playme/logs/errors.log"

echo "=== PlayMe Log Monitor ==="
echo "Log file: $LOG_FILE"
echo "Press Ctrl+C to stop"
echo ""

# Create logs dir if not exists
mkdir -p "$(dirname "$LOG_FILE")"

# Monitor with colors
tail -f "$LOG_FILE" | while read line; do
    if echo "$line" | grep -q "ERROR"; then
        echo -e "\033[31m$line\033[0m"  # Red for errors
    elif echo "$line" | grep -q "WARNING"; then
        echo -e "\033[33m$line\033[0m"  # Yellow for warnings
    elif echo "$line" | grep -q "INFO"; then
        echo -e "\033[32m$line\033[0m"  # Green for info
    elif echo "$line" | grep -q "DEBUG"; then
        echo -e "\033[36m$line\033[0m"  # Cyan for debug
    else
        echo "$line"
    fi
done
