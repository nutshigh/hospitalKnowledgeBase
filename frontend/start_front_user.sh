#!/bin/bash
# 用户态前端启动脚本: 3011(用户端) + 3012(医生端)
cd /home/wjyy2/hospitalKnowledgeBase/frontend
if ! curl -s -m2 http://localhost:3011/ >/dev/null 2>&1; then
  (cd packages/user-portal && nohup ../../node_modules/.bin/vite --port 3011 --strictPort --mode development --host 0.0.0.0 > /home/wjyy2/logs/fe-user-3011.log 2>&1 < /dev/null &)
fi
if ! curl -s -m2 http://localhost:3012/ >/dev/null 2>&1; then
  (cd packages/doctor-portal && nohup ../../node_modules/.bin/vite --port 3012 --strictPort --mode development --host 0.0.0.0 > /home/wjyy2/logs/fe-doctor-3012.log 2>&1 < /dev/null &)
fi
echo "frontend started"
