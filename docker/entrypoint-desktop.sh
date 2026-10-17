#!/usr/bin/env bash
# ==============================================================================
# SCP Virtual Desktop & Computer-Use Container Entrypoint
# Supervises Xvfb, Openbox WM, x11vnc, websockify (noVNC), and Chromium GUI.
# Enforces clean signal trapping, socket readiness polling, and zombie reaping.
# ==============================================================================
set -eo pipefail

export DISPLAY="${DISPLAY:-:99}"
export SCREEN_WIDTH="${SCREEN_WIDTH:-1920}"
export SCREEN_HEIGHT="${SCREEN_HEIGHT:-1080}"
export SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
export CHROME_USER_DATA_DIR="${CHROME_USER_DATA_DIR:-/tmp/chromium-profile}"

DISP_NUM="${DISPLAY#:}"
XVFB_PID=""
OPENBOX_PID=""
X11VNC_PID=""
WEBSOCKIFY_PID=""
CHROMIUM_PID=""
CHILD_PID=""

echo "=================================================================="
echo " Starting SCP Virtual Desktop Container"
echo " Display: $DISPLAY (${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH})"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 1. Signal Handler & Clean Teardown
# ------------------------------------------------------------------------------
cleanup() {
    echo ""
    echo "[entrypoint-desktop] Received shutdown signal. Terminating desktop services..."

    # Terminate launched processes gracefully
    for pid in "$CHILD_PID" "$CHROMIUM_PID" "$WEBSOCKIFY_PID" "$X11VNC_PID" "$OPENBOX_PID" "$XVFB_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    # Give processes a moment to exit gracefully
    sleep 1

    # Force kill any lingering processes
    for pid in "$CHILD_PID" "$CHROMIUM_PID" "$WEBSOCKIFY_PID" "$X11VNC_PID" "$OPENBOX_PID" "$XVFB_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done

    # Clean up stale locks
    rm -f "/tmp/.X${DISP_NUM}-lock" "/tmp/.X11-unix/X${DISP_NUM}"
    echo "[entrypoint-desktop] Desktop services terminated cleanly."
}

trap cleanup SIGTERM SIGINT EXIT

# ------------------------------------------------------------------------------
# 2. Clean Up Stale X11 Locks
# ------------------------------------------------------------------------------
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix
rm -f "/tmp/.X${DISP_NUM}-lock" "/tmp/.X11-unix/X${DISP_NUM}"

# ------------------------------------------------------------------------------
# 3. Start Virtual Display: Xvfb (:99)
# ------------------------------------------------------------------------------
echo "[entrypoint-desktop] Starting Xvfb on $DISPLAY (${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH})..."
Xvfb "$DISPLAY" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Socket readiness polling for Xvfb
XVFB_READY=0
for i in $(seq 1 30); do
    if [ -S "/tmp/.X11-unix/X${DISP_NUM}" ]; then
        XVFB_READY=1
        break
    fi
    sleep 0.2
done

if [ "$XVFB_READY" -ne 1 ]; then
    echo "[entrypoint-desktop] FATAL: Xvfb failed to initialize socket /tmp/.X11-unix/X${DISP_NUM} within 6 seconds" >&2
    exit 1
fi
echo "[entrypoint-desktop] Xvfb virtual display is ready on $DISPLAY (PID: $XVFB_PID)."

# ------------------------------------------------------------------------------
# 4. Start Lightweight Window Manager: Openbox
# ------------------------------------------------------------------------------
echo "[entrypoint-desktop] Starting Openbox window manager..."
openbox &
OPENBOX_PID=$!
sleep 0.5
echo "[entrypoint-desktop] Openbox started (PID: $OPENBOX_PID)."

# ------------------------------------------------------------------------------
# 5. Start x11vnc (Bound to 127.0.0.1:5900 only)
# ------------------------------------------------------------------------------
echo "[entrypoint-desktop] Starting x11vnc on 127.0.0.1:5900..."
x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 -listen 127.0.0.1 &
X11VNC_PID=$!

# Socket polling for x11vnc (port 5900)
X11VNC_READY=0
for i in $(seq 1 30); do
    if (echo > /dev/tcp/127.0.0.1/5900) 2>/dev/null; then
        X11VNC_READY=1
        break
    fi
    sleep 0.2
done

if [ "$X11VNC_READY" -ne 1 ]; then
    echo "[entrypoint-desktop] FATAL: x11vnc failed to bind to 127.0.0.1:5900 within 6 seconds" >&2
    exit 1
fi
echo "[entrypoint-desktop] x11vnc is ready on 127.0.0.1:5900 (PID: $X11VNC_PID)."

# ------------------------------------------------------------------------------
# 6. Start websockify / noVNC Web Streaming (Port 6080)
# ------------------------------------------------------------------------------
echo "[entrypoint-desktop] Starting websockify / noVNC on 0.0.0.0:6080 -> 127.0.0.1:5900..."
# Ensure index.html exists in novnc web dir if vnc.html is present
NOVNC_DIR="/usr/share/novnc"
if [ -d "$NOVNC_DIR" ] && [ -f "$NOVNC_DIR/vnc.html" ] && [ ! -f "$NOVNC_DIR/index.html" ]; then
    ln -sf "$NOVNC_DIR/vnc.html" "$NOVNC_DIR/index.html"
fi

websockify --web "$NOVNC_DIR" 6080 127.0.0.1:5900 &
WEBSOCKIFY_PID=$!

# Socket polling for websockify (port 6080)
WEBSOCKIFY_READY=0
for i in $(seq 1 30); do
    if (echo > /dev/tcp/127.0.0.1/6080) 2>/dev/null; then
        WEBSOCKIFY_READY=1
        break
    fi
    sleep 0.2
done

if [ "$WEBSOCKIFY_READY" -ne 1 ]; then
    echo "[entrypoint-desktop] FATAL: websockify failed to bind to 0.0.0.0:6080 within 6 seconds" >&2
    exit 1
fi
echo "[entrypoint-desktop] noVNC web streaming is ready on http://0.0.0.0:6080 (PID: $WEBSOCKIFY_PID)."

# ------------------------------------------------------------------------------
# 7. Start Physical Chromium in GUI Mode (headless=False)
# ------------------------------------------------------------------------------
echo "[entrypoint-desktop] Starting physical Chromium GUI with remote CDP on port 9222..."
mkdir -p "$CHROME_USER_DATA_DIR"

chromium \
    --no-sandbox \
    --disable-dev-shm-usage \
    --remote-debugging-port=9222 \
    --remote-debugging-address=0.0.0.0 \
    --window-size="${SCREEN_WIDTH},${SCREEN_HEIGHT}" \
    --window-position=0,0 \
    --start-maximized \
    --no-first-run \
    --no-default-browser-check \
    --disable-popup-blocking \
    --disable-background-networking \
    --disable-sync \
    --user-data-dir="$CHROME_USER_DATA_DIR" \
    "about:blank" &
CHROMIUM_PID=$!

# Socket polling for Chromium CDP (port 9222 /json/version)
CHROMIUM_READY=0
for i in $(seq 1 40); do
    if curl -s -f http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
        CHROMIUM_READY=1
        break
    fi
    sleep 0.25
done

if [ "$CHROMIUM_READY" -ne 1 ]; then
    echo "[entrypoint-desktop] FATAL: Chromium CDP failed to become ready on http://127.0.0.1:9222 within 10 seconds" >&2
    exit 1
fi
echo "[entrypoint-desktop] Chromium GUI is ready with remote CDP active on port 9222 (PID: $CHROMIUM_PID)."

echo "=================================================================="
echo " All SCP Virtual Desktop services are ONLINE and VERIFIED:"
echo " - Xvfb:       $DISPLAY ($SCREEN_WIDTH x $SCREEN_HEIGHT x $SCREEN_DEPTH)"
echo " - Openbox:    ACTIVE"
echo " - x11vnc:     127.0.0.1:5900"
echo " - noVNC Web:  http://0.0.0.0:6080"
echo " - Chromium:   CDP active on http://0.0.0.0:9222"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 8. Execute Provided Command or Wait
# ------------------------------------------------------------------------------
if [ $# -gt 0 ]; then
    echo "[entrypoint-desktop] Executing user command: $*"
    "$@" &
    CHILD_PID=$!
    wait "$CHILD_PID"
else
    echo "[entrypoint-desktop] No command specified; maintaining desktop supervisor loop."
    # Wait indefinitely for signal or child exit
    wait -n "$XVFB_PID" "$CHROMIUM_PID" "$WEBSOCKIFY_PID" || true
fi
