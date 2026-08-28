#!/bin/bash
# MathGraph 展示启动脚本（内容冻结，HMR 关闭）
# 用法：./demo.sh
# 端口：前端 4174  后端 5001
#
# 启动后文件变化不影响展示页面。
# 要把最新改动推到展示端：Ctrl+C 后重新运行 ./demo.sh 即可。

set -e

REPO="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$REPO/backend"
VENV="$BACKEND/.venv"
PYTHON="$VENV/bin/python"

if [ ! -f "$PYTHON" ]; then
  echo "❌ 未找到虚拟环境，请先运行 ./start.sh 一次以初始化环境"
  exit 1
fi

OLD_BACKEND=$(lsof -ti tcp:5001 2>/dev/null || true)
OLD_DEMO=$(lsof -ti tcp:4174 2>/dev/null || true)
[ -n "$OLD_BACKEND" ] && { echo "⚠  清理占用 5001 的进程"; kill $OLD_BACKEND 2>/dev/null; sleep 1; }
[ -n "$OLD_DEMO" ]    && { echo "⚠  清理占用 4174 的进程"; kill $OLD_DEMO    2>/dev/null; sleep 1; }

echo ""
echo "🎯 展示模式（文件监听已关闭，内容冻结）"
echo "   前端  →  http://localhost:4174"
echo "   后端  →  http://localhost:5001"
echo "   要更新展示内容：Ctrl+C 后重新运行 ./demo.sh"
echo ""

cd "$BACKEND" && "$PYTHON" api_v2.py &
BACKEND_PID=$!

cd "$REPO" && npm run dev:demo &
DEMO_PID=$!

trap "echo ''; echo '停止展示模式...'; kill $BACKEND_PID $DEMO_PID 2>/dev/null; exit 0" INT TERM
wait
