from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .api.events import router as events_router
from .api.text import router as text_router
from .api.voice import router as voice_router
from .db.database import initialize_database


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Neo Backend",
    description="Локальный API голосового помощника Neo",
    version="0.1.0",
    lifespan=lifespan,
)


class JsonUtf8CharsetMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("application/json") and "charset=" not in content_type:
            response.headers["content-type"] = "application/json; charset=utf-8"
        return response


app.add_middleware(JsonUtf8CharsetMiddleware)
app.include_router(events_router)
app.include_router(text_router)
app.include_router(voice_router)

@app.get("/health", tags=["service"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Оставляем catch-all монтирование последним, чтобы маршруты API и health оставались доступными.
frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")