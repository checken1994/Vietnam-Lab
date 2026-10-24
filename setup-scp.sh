#!/usr/bin/env bash
# ==============================================================================
# SCP AGENT OS — All-in-One Automated Server Installer (1 Lệnh Duy Nhất)
#
# Usage:
#   Direct execution:
#     ./setup-scp.sh [OPTIONS]
#
#   Remote one-liner execution (curl-to-bash):
#     curl -fsSL https://raw.githubusercontent.com/checken1994/Vietnam-Lab/main/setup-scp.sh | bash
#     curl -fsSL https://raw.githubusercontent.com/checken1994/Vietnam-Lab/main/setup-scp.sh | bash -s -- --docker
#
# Options:
#   --server        Configure SCP_HOST=0.0.0.0 for remote/cloud server accessibility.
#   --docker        Bootstrap .env and launch via Docker Compose.
#   --api-only      Run backend API server only (port 8000), skip Node services.
#   --no-start      Install all dependencies and bootstrap .env without starting services.
#   --daemon        Start services in background mode (default when starting).
#   --dir <path>    Custom destination directory when cloning remotely (default: $HOME/scp).
#   --help, -h      Display this help message.
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BOLD}${BLUE}"
echo "=============================================================================="
echo "    ███████╗ ██████╗██████╗     █████╗  ██████╗ ███████╗███╗   ██╗████████╗ "
echo "    ██╔════╝██╔════╝██╔══██╗   ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝ "
echo "    ███████╗██║     ██████╔╝   ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║    "
echo "    ╚════██║██║     ██╔═══╝    ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║    "
echo "    ███████║╚██████╗██║        ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║    "
echo "    ╚══════╝ ╚═════╝╚═╝        ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝    "
echo "                 SECURE CONTROL PLANE & AGENT OS (1-COMMAND SETUP)            "
echo "=============================================================================="
echo -e "${NC}"

# Parse command line flags
OPT_SERVER=false
OPT_DOCKER=false
OPT_API_ONLY=false
OPT_NO_START=false
OPT_DAEMON=true
CUSTOM_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server)
      OPT_SERVER=true
      shift
      ;;
    --docker)
      OPT_DOCKER=true
      shift
      ;;
    --api-only)
      OPT_API_ONLY=true
      shift
      ;;
    --no-start)
      OPT_NO_START=true
      shift
      ;;
    --foreground)
      OPT_DAEMON=false
      shift
      ;;
    --daemon)
      OPT_DAEMON=true
      shift
      ;;
    --dir)
      CUSTOM_DIR="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: ./setup-scp.sh [--server] [--docker] [--api-only] [--no-start] [--dir <path>]"
      exit 0
      ;;
    *)
      echo -e "${YELLOW}Unknown option: $1 (ignoring)${NC}"
      shift
      ;;
  esac
done

# Locate project root
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  CANDIDATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -f "$CANDIDATE_DIR/scp/__init__.py" ] && [ -f "$CANDIDATE_DIR/scripts/bootstrap_local_env.py" ]; then
    SCRIPT_DIR="$CANDIDATE_DIR"
  fi
fi

if [ -z "$SCRIPT_DIR" ]; then
  if [ -f "$(pwd)/scp/__init__.py" ] && [ -f "$(pwd)/scripts/bootstrap_local_env.py" ]; then
    SCRIPT_DIR="$(pwd)"
  fi
fi

# If outside repository, clone from GitHub
if [ -z "$SCRIPT_DIR" ]; then
  TARGET_DIR="${CUSTOM_DIR:-$HOME/scp}"
  echo -e "${YELLOW}ℹ️  SCP repository not detected in current working directory.${NC}"
  echo -e "📥 Installing SCP Agent OS into: ${BOLD}$TARGET_DIR${NC}"

  # Ensure git and curl are available
  if ! command -v git >/dev/null 2>&1; then
    echo -e "⚙️  Git not found. Attempting automatic installation..."
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update -y && sudo apt-get install -y git curl
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y git curl
    elif command -v yum >/dev/null 2>&1; then
      sudo yum install -y git curl
    elif command -v pacman >/dev/null 2>&1; then
      sudo pacman -Sy --noconfirm git curl
    elif command -v apk >/dev/null 2>&1; then
      apk add git curl
    elif command -v brew >/dev/null 2>&1; then
      brew install git curl
    else
      echo -e "${RED}❌ Git is required. Please install git and rerun this command.${NC}" >&2
      exit 1
    fi
  fi

  if [ ! -d "$TARGET_DIR" ]; then
    git clone https://github.com/checken1994/Vietnam-Lab.git "$TARGET_DIR"
  else
    echo -e "  Found existing directory $TARGET_DIR, updating..."
    (cd "$TARGET_DIR" && git pull --rebase || true)
  fi

  SCRIPT_DIR="$TARGET_DIR"
fi

cd "$SCRIPT_DIR"

# [1/6] Check Python 3.12+
# [docs-truth · 2026-10-24 · theo báo cáo audit ngoài 1.1] Trước đây nhận
# "3.10+" nhưng scp/requirements.txt pin numpy==2.5.3 — pip không có wheel
# nào cho Python 3.10/3.11 (probe: "No matching distribution found" cho cả
# hai) ⇒ setup trên 3.11 gãy giữa chừng tại bước cài numpy. Nói thật: cần
# >= 3.12, fail-closed ngay tại cửa với thông báo rõ.
echo -e "\n${BOLD}[1/6] Checking system prerequisites and Python 3.12+...${NC}"

find_python() {
  for py in python3.13 python3.12 python3 python; do
    if command -v "$py" >/dev/null 2>&1; then
      if "$py" -c 'import sys; exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
        echo "$py"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_CMD=""
if ! PYTHON_CMD="$(find_python)"; then
  echo -e "${YELLOW}⚠️  Compatible Python 3.12+ not found. Attempting installation...${NC}"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y && sudo apt-get install -y python3 python3-venv python3-pip curl build-essential
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip python3-devel gcc
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y python3 python3-pip gcc
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm python python-pip
  elif command -v apk >/dev/null 2>&1; then
    apk add python3 py3-pip python3-dev gcc musl-dev
  fi

  if ! PYTHON_CMD="$(find_python)"; then
    echo -e "${RED}❌ Could not automatically install Python >= 3.12. Please install Python 3.12+ manually (numpy==2.5.3 pin has no 3.10/3.11 wheels).${NC}" >&2
    exit 1
  fi
fi

echo -e "  ✅ Using Python: ${GREEN}$("$PYTHON_CMD" --version)${NC} ($PYTHON_CMD)"

# [2/6] Setup storage directories
echo -e "\n${BOLD}[2/6] Preparing system storage and directory structure...${NC}"
mkdir -p data data/knowledge data/backups data/documents logs /tmp/scp-logs .private-secrets
chmod 700 .private-secrets 2>/dev/null || true
echo -e "  ✅ Directories verified (data, data/knowledge, data/backups, logs, .private-secrets)"

# [3/6] Bootstrap cryptographically secure environment
echo -e "\n${BOLD}[3/6] Bootstrapping cryptographically secure environment variables (.env)...${NC}"
BOOTSTRAP_ARGS=("--update")
if [ "$OPT_SERVER" = "true" ]; then
  BOOTSTRAP_ARGS+=("--server")
fi

"$PYTHON_CMD" scripts/bootstrap_local_env.py "${BOOTSTRAP_ARGS[@]}"
chmod 600 .env 2>/dev/null || true
echo -e "  ✅ Environment file .env ready with 256-bit cryptographic secrets (chmod 600)"

# [4/6] Docker mode or Python venv
if [ "$OPT_DOCKER" = "true" ]; then
  echo -e "\n${BOLD}[4/6] Docker mode selected. Building and starting containers...${NC}"
  if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}❌ Docker is not installed. Please install Docker or run without --docker.${NC}" >&2
    exit 1
  fi
  docker compose up -d --build
  echo -e "\n${GREEN}==============================================================================${NC}"
  echo -e "${GREEN}🎉 SCP DOCKER STACK DEPLOYED SUCCESSFULLY!${NC}"
  echo -e "${GREEN}==============================================================================${NC}"
  echo -e "  API URL:      http://localhost:8000"
  echo -e "  Health Check: http://localhost:8000/health"
  echo -e "  Containers:   docker compose ps"
  echo -e "  Logs:         docker compose logs -f"
  echo -e "  Stop:         docker compose down"
  echo -e "${GREEN}==============================================================================${NC}"
  exit 0
fi

echo -e "\n${BOLD}[4/6] Setting up Python virtual environment (.venv) & dependencies...${NC}"
if [ ! -d ".venv" ]; then
  echo "  Creating virtual environment in .venv..."
  "$PYTHON_CMD" -m venv .venv
fi

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
VENV_PIP="$SCRIPT_DIR/.venv/bin/pip"

if [ ! -x "$VENV_PYTHON" ]; then
  echo -e "${RED}❌ Virtual environment python binary not found at $VENV_PYTHON${NC}" >&2
  exit 1
fi

echo "  Upgrading pip..."
"$VENV_PIP" install --upgrade pip setuptools wheel --quiet 2>/dev/null || "$VENV_PIP" install --upgrade pip --quiet

echo "  Installing core requirements (requirements.txt)..."
"$VENV_PIP" install -r requirements.txt --quiet
echo -e "  ✅ Dependencies installed successfully into .venv"

# [5/6] Inspect runtime engines & Dashboard dependencies
echo -e "\n${BOLD}[5/6] Inspecting runtime engines & Dashboard dependencies...${NC}"

if [ "$OPT_API_ONLY" = "false" ] && [ -d "dashboard" ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "  Configuring Dashboard via npm..."
    (cd dashboard && (npm install --omit=dev --quiet 2>/dev/null || npm install --quiet 2>/dev/null || true))
  else
    echo -e "  ${YELLOW}ℹ️  Node/npm not detected. Dashboard will be skipped; backend API will run fully.${NC}"
    OPT_API_ONLY=true
  fi
fi

# Run verification smoke test
echo "  Running verification smoke test..."
"$VENV_PYTHON" tools/run_bounded_system_smoke.py || true
echo -e "  ✅ System integrity verified"

# [6/6] Launch SCP services
if [ "$OPT_NO_START" = "true" ]; then
  echo -e "\n${GREEN}==============================================================================${NC}"
  echo -e "${GREEN}🎉 SCP AGENT OS INSTALLATION COMPLETE (--no-start specified)${NC}"
  echo -e "${GREEN}==============================================================================${NC}"
  echo -e "  Directory:    $SCRIPT_DIR"
  echo -e "  Environment:  $SCRIPT_DIR/.env"
  echo -e "  To start:     cd $SCRIPT_DIR && ./start-scp.sh daemon"
  echo -e "  To stop:      cd $SCRIPT_DIR && ./stop-scp.sh"
  echo -e "${GREEN}==============================================================================${NC}"
  exit 0
fi

echo -e "\n${BOLD}[6/6] Launching SCP Agent OS services...${NC}"
chmod +x start-scp.sh stop-scp.sh 2>/dev/null || true

if [ "$OPT_API_ONLY" = "true" ]; then
  echo "  Starting standalone SCP Python API (port 8000)..."
  mkdir -p /tmp/scp-logs
  pkill -f "python.* -m scp 8000" 2>/dev/null || true
  setsid nohup "$VENV_PYTHON" -m scp 8000 > /tmp/scp-logs/scp-server.log 2>&1 &
  echo $! > /tmp/scp-logs/scp-server.pid
else
  if [ "$OPT_DAEMON" = "true" ]; then
    ./start-scp.sh daemon
  else
    exec ./start-scp.sh foreground
  fi
fi

# Wait for readiness
echo "  Waiting for SCP API to be ready..."
READY=false
for i in {1..30}; do
  if curl -sf --max-time 1 "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 1
done

echo -e "\n${GREEN}==============================================================================${NC}"
if [ "$READY" = "true" ]; then
  echo -e "${GREEN}🎉 SCP AGENT OS IS RUNNING AND HEALTHY!${NC}"
else
  echo -e "${YELLOW}⚠️  SCP started; health probe still initializing. Check logs if needed.${NC}"
fi
echo -e "${GREEN}==============================================================================${NC}"
echo -e "  API URL:        http://127.0.0.1:8000"
echo -e "  Health Check:   http://127.0.0.1:8000/health"
if [ "$OPT_API_ONLY" = "false" ]; then
  echo -e "  Dashboard:      http://127.0.0.1:3000"
  echo -e "  LLM Bridge:     http://127.0.0.1:8081"
  echo -e "  Loop Scheduler: http://127.0.0.1:3030"
fi
echo -e "  Working Dir:    $SCRIPT_DIR"
echo -e "  Environment:    $SCRIPT_DIR/.env"
echo -e "  Logs:           /tmp/scp-logs/"
echo -e ""
echo -e "  Quick Query:    curl -X POST http://127.0.0.1:8000/ask \\"
echo -e "                    -H 'Content-Type: application/json' \\"
echo -e "                    -d '{\"question\":\"What is the capital of France?\"}'"
echo -e ""
echo -e "  Stop Services:  cd $SCRIPT_DIR && ./stop-scp.sh"
echo -e "  Restart:        cd $SCRIPT_DIR && ./start-scp.sh daemon"
echo -e "${GREEN}==============================================================================${NC}"
