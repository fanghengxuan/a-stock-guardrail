#!/bin/bash
# ============================================================
# Guardrail 一键启动（终端通用版，Linux/macOS）
# 后端先起 → 就绪 → 前端起（直连 :8000 真实后端）→ 打开浏览器
# Ctrl+C 同时关闭前后端
# ============================================================
set -uo pipefail
cd "$(dirname "$0")"

BACKEND_PORT=8000
FRONTEND_PORT=5173
BACKEND_URL="http://localhost:${BACKEND_PORT}"
FRONTEND_URL="http://localhost:${FRONTEND_PORT}"
READY_TIMEOUT=30

red()    { printf '\033[31m%s\033[0m\n' "$1"; }
green()  { printf '\033[32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }

port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$1" -sTCP:LISTEN -P -n 2>/dev/null | grep -q LISTEN
  elif command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -q ":$1 "
  else
    return 1
  fi
}

wait_until_ready() {
  local url="$1" name="$2" timeout="$3" i=0
  while [ "$i" -lt "$timeout" ]; do
    if curl -sS --connect-timeout 2 -o /dev/null "$url" 2>/dev/null; then
      green "✅ $name 就绪（${i}s）"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  red "❌ $name 在 ${timeout}s 内未就绪"
  return 1
}

open_browser() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then
    open "$url" 2>/dev/null
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" 2>/dev/null
  else
    echo "（请手动打开 $url）"
  fi
}

kill_port() {
  local port="$1"
  if port_in_use "$port"; then
    yellow "⚠️  端口 $port 被占用，正在强制终止占用进程..."
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | xargs kill -9 2>/dev/null || true
    sleep 1
    if port_in_use "$port"; then
      red "❌ 无法释放端口 $port，请手动检查。"
      exit 1
    fi
    green "✅ 端口 $port 已释放"
  fi
}

kill_port "$BACKEND_PORT"
kill_port "$FRONTEND_PORT"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  yellow "🛑 正在停止服务..."
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  lsof -tiTCP:"$FRONTEND_PORT" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
  lsof -tiTCP:"$BACKEND_PORT" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
  pkill -P $$ 2>/dev/null || true
  green "已停止。"
}
trap cleanup INT TERM EXIT

# 东财 Cookie 降频刷新：文件缺失或超过 24h 未刷才自动执行（Chrome 已登录时静默，失败不阻塞）
EM_COOKIE_FILE="backend/eastmoney_cookie.txt"
EM_COOKIE_FRESH=""
if [ -f "$EM_COOKIE_FILE" ] && [ -n "$(find "$EM_COOKIE_FILE" -mmin -1440 2>/dev/null)" ]; then
  EM_COOKIE_FRESH=1
fi
if [ -n "$EM_COOKIE_FRESH" ]; then
  echo "🍪 东财 Cookie 有效（24h 内已刷新），跳过"
elif [ -f backend/fetch_em_cookie.sh ] && pgrep -x "Google Chrome" >/dev/null 2>&1; then
  echo "🍪 东财 Cookie 缺失或超 24h，刷新..."
  bash backend/fetch_em_cookie.sh 2>/dev/null || yellow "  ⚠️ Cookie 刷新跳过（Chrome 未开启 JS 权限或未登录东财）"
else
  echo "🍪 东财 Cookie 缺失（无 Chrome 可刷新，东财源可能受限）"
fi

# venv 自愈：.venv 是随项目位置的本地产物，若缺失或被整体搬动导致内部路径失效，
# 启动前探测 uvicorn 能否运行，不能则按 uv.lock 在当前目录重建——脚本不写死任何绝对路径
if [ ! -x backend/.venv/bin/uvicorn ] || ! backend/.venv/bin/uvicorn --version >/dev/null 2>&1; then
  yellow "⚠️  后端虚拟环境不可用，正在按 uv.lock 重建 (uv sync)..."
  ( cd backend && uv sync ) || { red "venv 重建失败，请检查网络/uv 后重试"; exit 1; }
  green "✅ venv 重建完成"
fi

echo "🚀 启动后端 (uvicorn :$BACKEND_PORT)..."
( cd backend && uv run uvicorn main:app --port "$BACKEND_PORT" ) &
BACKEND_PID=$!

if ! wait_until_ready "$BACKEND_URL" "后端" "$READY_TIMEOUT"; then
  red "后端启动失败。请手动排查：cd backend && uv run uvicorn main:app --port 8000"
  exit 1
fi

echo "🚀 启动前端 (vite :$FRONTEND_PORT)..."
( cd frontend && npm run dev -- --port "$FRONTEND_PORT" ) &
FRONTEND_PID=$!

if ! wait_until_ready "$FRONTEND_URL" "前端" "$READY_TIMEOUT"; then
  red "前端启动失败。请手动排查：cd frontend && npm run dev"
  exit 1
fi

echo "🌐 打开浏览器..."
open_browser "$FRONTEND_URL"

echo ""
green "✅ 服务运行中："
echo "   前端: $FRONTEND_URL (真实后端模式)"
echo "   后端: $BACKEND_URL"
echo "   按 Ctrl+C 停止全部服务"
wait
