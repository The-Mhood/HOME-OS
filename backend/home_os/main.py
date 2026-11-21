import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from home_os.api.routes_agent import router as agent_router
from home_os.api.routes_auth_and_system import router as auth_router
from home_os.api.routes_comms_media_auto_sec import router as domain_router
from home_os.api.routes_devices import router as devices_router
from home_os.config import Settings, get_settings
from home_os.runtime import HomeOsRuntime
from home_os.security.rate_limit import RateLimitExceeded


async def _automation_schedule_worker(runtime: HomeOsRuntime) -> None:
    last_minute = ""
    while True:
        try:
            now = datetime.now(timezone.utc)
            hhmm = now.strftime("%H:%M")
            if hhmm != last_minute:
                last_minute = hhmm
                with runtime.SessionLocal() as db:
                    await runtime.automations.evaluate_schedule_tick(
                        db=db,
                        current_hhmm=hhmm,
                        weekday=now.weekday(),
                    )
        except asyncio.CancelledError:
            break
        except Exception:
            pass
        await asyncio.sleep(15.0)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    runtime = HomeOsRuntime(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sched_task = asyncio.create_task(_automation_schedule_worker(runtime))
        try:
            yield
        finally:
            sched_task.cancel()
            try:
                await sched_task
            except asyncio.CancelledError:
                pass
            runtime.engine.dispose()

    app = FastAPI(
        title=cfg.app_name,
        version=cfg.app_version,
        description="Universal Personal Device Management Platform — Local Command Core",
        lifespan=lifespan,
    )
    app.state.runtime = runtime

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_and_rate_limit_middleware(request: Request, call_next):
        client_ip = request.client.host if request.client else "local"
        if request.url.path.startswith("/api/"):
            try:
                runtime.rate_limiter.check(
                    f"ip:{client_ip}",
                    limit=cfg.rate_limit_requests_per_minute,
                )
            except RateLimitExceeded as exc:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(exc.retry_after_seconds)},
                )

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-HomeOS-Version"] = cfg.app_version
        return response

    app.include_router(auth_router, prefix="/api/v1", tags=["Auth & Dashboard"])
    app.include_router(devices_router, prefix="/api/v1", tags=["Devices & Commands"])
    app.include_router(agent_router, prefix="/api/v1", tags=["Agent Channels"])
    app.include_router(domain_router, prefix="/api/v1", tags=["Communications, Media, Automations & Security"])

    # Serve compiled desktop web bundle if present
    dist_dir = Path(__file__).resolve().parents[2] / "desktop" / "dist"
    if dist_dir.exists() and (dist_dir / "index.html").exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "API route not found"})
            candidate = (dist_dir / full_path).resolve()
            if candidate.is_file() and str(candidate).startswith(str(dist_dir.resolve())):
                return FileResponse(str(candidate))
            return FileResponse(str(dist_dir / "index.html"))

    return app


app = create_app()
