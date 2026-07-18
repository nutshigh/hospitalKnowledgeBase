import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.logging_config import setup_logging
from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.knowledge.internal import router as knowledge_internal_router
from app.modules.report.router import router as report_router
from app.modules.report.batch_router import router as batch_router
from app.core.batch_sweeper import start as start_sweeper
from app.modules.interpretation.router import router as interpretation_router
from app.modules.statistics.router import router as statistics_router
from app.modules.dispatch.router import router as dispatch_router
from app.modules.chat.router import router as chat_router
from app.modules.user_profile.router import router as user_profile_router
from app.modules.tenant.router import router as tenant_router


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)

    from app.ai.config import ensure_milvus_started
    ensure_milvus_started()

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
    app.include_router(batch_router, prefix="/api/v1/reports", tags=["reports-batch"])
    app.include_router(interpretation_router, prefix="/api/v1/interpretations", tags=["interpretations"])
    app.include_router(statistics_router, prefix="/api/v1/statistics", tags=["statistics"])
    app.include_router(dispatch_router, prefix="/api/v1/dispatch", tags=["dispatch"])
    app.include_router(chat_router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(user_profile_router, prefix="/api/v1/profile", tags=["user-profile"])
    app.include_router(tenant_router, prefix="/api/v1/tenants", tags=["tenant"])

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        import logging
        logging.getLogger("app").exception(
            "Unhandled exception on %s %s", request.method, request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "INTERNAL_ERROR"},
        )

    @app.on_event("startup")
    async def _start_batch_sweeper():
        import logging
        _sweeper_log = logging.getLogger("app.batch.sweeper")

        def _on_sweeper_done(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                _sweeper_log.error("BatchSweeper task exited unexpectedly: %r", exc)

        task = asyncio.create_task(start_sweeper())
        task.add_done_callback(_on_sweeper_done)
        app.state.batch_sweeper_task = task

    @app.on_event("shutdown")
    async def _stop_batch_sweeper():
        task = getattr(app.state, "batch_sweeper_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return app


app = create_app()
