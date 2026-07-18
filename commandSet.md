创建用户：
curl -X POST http://localhost:8000/api/v1/auth/register -H "Content-Type: application '{"username":"user2","password":"123456","role":"user","hospital_id":"H001"}'
role:user,doctor,admin

创建医院表：
curl -X POST http://localhost:8000/api/v1/tenants -H "Content-Type: application/json" -d '{"hospital_id":"H002","hospital_name":"第二医院"}'

重启后端：
pkill -f "uvicorn app.main:app" && bash start.sh

重启worker：
pkill -f "app.modules.interpretation.worker" && bash start.sh