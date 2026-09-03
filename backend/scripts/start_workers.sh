#!/bin/bash
# 用户态服务启动脚本(测试环境): 后端 + 3 worker
cd /home/wjyy2/hospitalKnowledgeBase/backend
export BULK_WINDOW_START=0 BULK_WINDOW_END=24
export OCR_BASE_URL=http://localhost:8006
# 2026-08-30: 独立 vhost 隔离(root 那边用 hospital_dev, 两边互不干扰)
export RABBITMQ_VHOST=hospital_dev_wjyy2
# 后端 8005(仅当未运行时启动); 0.0.0.0 监听 IPv4(远程浏览器用服务器 IP 访问,
# 本机浏览器 localhost 由前端转为 127.0.0.1; 实测 uvicorn --host :: 的 IPv4 不通)
if ! curl -s -m2 http://127.0.0.1:8005/api/v1/health >/dev/null 2>&1; then
  nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8005 >> /home/wjyy2/logs/backend-8005.log 2>&1 < /dev/null &
fi
nohup env INDICATOR_EXTRACTOR=signal .venv/bin/python -u -c "from app.modules.report.worker import start_worker; start_worker()" >> /home/wjyy2/logs/worker-parsing.log 2>&1 < /dev/null &
nohup .venv/bin/python -u -c "from app.modules.interpretation.worker import start_worker; start_worker()" >> /home/wjyy2/logs/worker-interpretation.log 2>&1 < /dev/null &
nohup .venv/bin/python -u -c "from app.modules.report.extract_worker import start_worker; start_worker()" >> /home/wjyy2/logs/worker-extract.log 2>&1 < /dev/null &
echo "services started"
