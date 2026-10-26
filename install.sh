#!/bin/sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
APP_DIR="$DATA_HOME/dropring/app"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/dropring"
INTEGRATION=1
AUTOSTART=1

for arg in "$@"; do
    case "$arg" in
        --no-integration) INTEGRATION=0 ;;
        --no-autostart) AUTOSTART="" ;;
        -h|--help)
            echo "Usage: ./install.sh [--no-integration] [--no-autostart]"
            exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required." >&2
    exit 1
fi

if ! python3 -c 'import PySide6' >/dev/null 2>&1; then
    echo "PySide6 (Qt for Python) is missing. Install it with one of:" >&2
    echo "  Fedora:        sudo dnf install python3-pyside6" >&2
    echo "  Arch:          sudo pacman -S pyside6" >&2
    echo "  Ubuntu/Debian: sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtnetwork" >&2
    echo "  Any:           python3 -m pip install --user PySide6" >&2
    echo "The command line (dropring convert …) works without it." >&2
fi

rm -rf "$APP_DIR.new"
mkdir -p "$APP_DIR.new" "$BIN_DIR"
cp -R "$SOURCE_DIR/dropring" "$APP_DIR.new/"
find "$APP_DIR.new" -name '__pycache__' -type d -prune -exec rm -rf {} +
if [ -d "$APP_DIR" ]; then
    rm -rf "$APP_DIR.old"
    mv "$APP_DIR" "$APP_DIR.old"
fi
mv "$APP_DIR.new" "$APP_DIR"
rm -rf "$APP_DIR.old"

cat > "$LAUNCHER" <<EOF
#!/bin/sh
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m dropring "\$@"
EOF
chmod 755 "$LAUNCHER"

if [ "$INTEGRATION" = 1 ]; then
    DROPRING_LAUNCHER="$LAUNCHER" "$LAUNCHER" integrate >/dev/null
    echo "Added: app launcher, Dolphin right-click menu (Convert with Dropring… / Convert to)."
fi
if [ -n "$AUTOSTART" ]; then
    PYTHONPATH="$APP_DIR" DROPRING_LAUNCHER="$LAUNCHER" python3 -c 'from dropring import integration; integration.set_autostart(True)'
    echo "Dropring will start in the tray at sign-in (needed for the Meta+Shift+C shortcut)."
fi

echo "Installed to $APP_DIR"
echo
"$LAUNCHER" engines
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo; echo "Note: $BIN_DIR is not on your PATH. Add it to use the 'dropring' command." ;;
esac
echo
if [ -n "$AUTOSTART" ]; then
    setsid "$LAUNCHER" gui --background >/dev/null 2>&1 &
fi
echo "Start it from the app menu, or run: dropring"
echo "Shortcut: select files in Dolphin, press Ctrl+C, then Meta+Shift+C."
