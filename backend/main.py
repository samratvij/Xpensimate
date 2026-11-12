from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import (
    alerts,
    balances,
    budgets,
    categories,
    chat,
    devices,
    goals as goals_api,
    health,
    history,
    household,
    income,
    insights,
    meta,
    notifications,
    receipts,
    recurring,
    rules,
    statements,
    summary,
    transactions,
)
from app.core.security import require_token
from app.services import goals, renewals


@asynccontextmanager
async def lifespan(_: FastAPI):
    stops = [
        renewals.start(),  # a week's notice before each subscription renews
        goals.start(),  # behind on the monthly savings target
    ]
    yield
    for stop in stops:
        stop.set()


app = FastAPI(title="Xpensimate", version="0.1.0", lifespan=lifespan)

api_v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
for module in (
    transactions, notifications, receipts, recurring, statements, budgets, categories, summary, insights, alerts, devices,
    rules, meta, health, household, history, income, balances, goals_api, chat
):
    api_v1.include_router(module.router)
app.include_router(api_v1)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/import", include_in_schema=False)
def import_page() -> FileResponse:
    """Statement import page for a laptop browser on the tailnet (it asks for the API token itself)."""
    return FileResponse(Path(__file__).parent / "app" / "web" / "import.html", media_type="text/html")


WEB_APP = Path(__file__).parent / "app" / "web" / "app"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/app/")


@app.middleware("http")
async def revalidate_web_app(request: Request, call_next):
    """The web app has no build step or versioned file names, so browsers must check for a newer copy each load."""
    response = await call_next(request)
    if request.url.path == "/app" or request.url.path.startswith("/app/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Mobile web app (e.g. an iPhone on the tailnet); it asks for the person's API token itself.
app.mount("/app", StaticFiles(directory=WEB_APP, html=True), name="web-app")
