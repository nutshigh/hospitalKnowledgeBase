#!/bin/bash
# 8005 后端看护(用户态测试环境, 2026-09-11): 进程被外部关闭后自动拉起。
# 背景: 8005 多次被外部"优雅关闭"(收到 SIGTERM), worker 未受影响;
# 无 root 权限查 syslog/journal 定位凶手, 用看护循环保证可用性。
cd /home/wjyy2/hospitalKnowledgeBase/backend
while true; do
  if ! curl -s -m2 http://127.0.0.1:8005/api/v1/health >/dev/null 2>&1; then
    setsid nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8005 \
      >> /home/wjyy2/logs/backend-8005.log 2>&1 < /dev/null &
  fi
  sleep 15
done
