from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.knowledge.internal import router as knowledge_internal_router
from app.modules.report.router import router as report_router
from app.modules.interpretation.router import router as interpretation_router
from app.modules.statistics.router import router as statistics_router
from app.modules.dispatch.router import router as dispatch_router
from app.modules.chat.router import router as chat_router


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api/v1", tags=["health"])
    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(knowledge_router, prefix="/api/v1/knowledge", tags=["knowledge"])
    app.include_router(knowledge_internal_router, prefix="/api/v1/knowledge/internal", tags=["knowledge-internal"])
    app.include_router(report_router, prefix="/api/v1/reports", tags=["reports"])
    app.include_router(interpretation_router, prefix="/api/v1/interpretations", tags=["interpretations"])
    app.include_router(statistics_router, prefix="/api/v1/statistics", tags=["statistics"])
    app.include_router(dispatch_router, prefix="/api/v1/dispatch", tags=["dispatch"])
    app.include_router(chat_router, prefix="/api/v1/chat", tags=["chat"])

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "INTERNAL_ERROR"},
        )

    return app


app = create_app()
