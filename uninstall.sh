#!/bin/sh
set -eu

DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
APP_DIR="$DATA_HOME/dropring/app"
LAUNCHER="$HOME/.local/bin/dropring"
PURGE=0

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -h|--help) echo "Usage: ./uninstall.sh [--purge]   (--purge also deletes settings and history)"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ -d "$APP_DIR" ]; then
    PYTHONPATH="$APP_DIR" python3 -c 'from dropring import integration; integration.remove()' || true
    PYTHONPATH="$APP_DIR" python3 -c 'from dropring import ipc; ipc.send({"cmd": "quit"})' || true
fi
rm -rf "$DATA_HOME/dropring"
rm -f "$LAUNCHER"
if [ "$PURGE" = 1 ]; then
    rm -rf "$CONFIG_HOME/dropring"
    echo "Settings and history deleted."
else
    echo "Settings kept in $CONFIG_HOME/dropring (use --purge to delete them)."
fi
echo "Dropring removed. Converted files are kept."
