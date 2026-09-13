"""本地联调专用轻量入口（**仅本地开发使用，不用于生产部署**）。

背景：完整 app.main 依赖 Milvus/RabbitMQ/vLLM 等基础设施与重量级 Python 依赖，
本地联调统计接口时无需这些。本入口只挂载 statistics 相关路由，
可用最小依赖集（fastapi/sqlalchemy/pymysql/pydantic-settings）运行：

    uvicorn app.stat_dev_main:app --host 0.0.0.0 --port 8100

生产环境仍使用 app.main:app（本文件的挂载逻辑与之一致）。
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.modules.statistics.router import router as statistics_router
from app.modules.statistics.disease_router import router as statistics_disease_router

app = FastAPI(title="Hospital Statistics Dev Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(statistics_router, prefix="/api/v1/statistics", tags=["statistics"])
app.include_router(statistics_disease_router, prefix="/api/v1/statistics", tags=["statistics-disease"])
