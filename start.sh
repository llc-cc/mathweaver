#!/bin/bash
# MathGraph 编写/调试启动脚本（HMR 热重载）
# 用法：./start.sh
# 端口：前端 5173  后端 5001
# 展示模式请用：./demo.sh（端口 4174，内容冻结）

set -e

REPO="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$REPO/backend"
VENV="$BACKEND/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

# ── 1. 检查 venv ──────────────────────────────────────────────────────
if [ ! -f "$PYTHON" ]; then
  echo "⚠  未找到虚拟环境，正在创建 $VENV ..."
  python3 -m venv "$VENV"
fi

# ── 2. 安装/更新依赖 ──────────────────────────────────────────────────
echo "📦 检查依赖..."
"$PIP" install -q -r "$BACKEND/requirements.txt"

# ── 3. 清理残留进程 ──────────────────────────────────────────────────
OLD_BACKEND=$(lsof -ti tcp:5001 2>/dev/null || true)
OLD_FRONTEND=$(lsof -ti tcp:5173 2>/dev/null || true)
[ -n "$OLD_BACKEND" ]  && { echo "⚠  清理占用 5001 的进程: $OLD_BACKEND";  kill $OLD_BACKEND  2>/dev/null; sleep 1; }
[ -n "$OLD_FRONTEND" ] && { echo "⚠  清理占用 5173 的进程: $OLD_FRONTEND"; kill $OLD_FRONTEND 2>/dev/null; sleep 1; }

# ── 4. 启动后端 (5001) ────────────────────────────────────────────────
echo "🚀 启动后端 API  →  http://localhost:5001"
cd "$BACKEND"
"$PYTHON" api_v2.py &
BACKEND_PID=$!
echo "   后端 PID: $BACKEND_PID"

# ── 5. 启动前端 (5173) ────────────────────────────────────────────────
echo "🎨 启动前端      →  http://localhost:5173"
cd "$REPO"
npm run dev &
FRONTEND_PID=$!
echo "   前端 PID: $FRONTEND_PID"

echo ""
echo "✅ 编写模式已启动（HMR 开启）"
echo "   前端  →  http://localhost:5173"
echo "   后端  →  http://localhost:5001"
echo "   按 Ctrl+C 停止 | 展示模式请另开终端运行 ./demo.sh"

# ── 6. 等待 Ctrl+C ────────────────────────────────────────────────────
trap "echo ''; echo '停止中...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
