#!/bin/bash
# 开发期看门狗：mini_racer(V8) 原生崩溃会带死 uvicorn 进程（FATAL，Python 层不可捕获），
# 此脚本负责 2s 内自动拉起。根治方案（akshare 子进程隔离）落地后撤掉。
cd "$(dirname "$0")/.." || exit 1
while :; do
  .venv/bin/python -m uvicorn main:app --port 8000 >> /tmp/guardrail-backend.log 2>&1
  echo "[watchdog] backend died, restart $(date +%T)" >> /tmp/guardrail-backend.log
  sleep 1
done
