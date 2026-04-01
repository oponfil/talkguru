# dashboard/server.py — HTTP-сервер дашборда на aiohttp
#
# Запускается параллельно с Telegram-ботом, использует порт Railway ($PORT).
# Маршруты: /dashboard (HTML), /api/stats, /api/logs, /api/users

from __future__ import annotations

from typing import Callable

import os
import pathlib

import jinja2
from aiohttp import web

from config import (
    DASHBOARD_AUTO_REFRESH_DURATION_SEC,
    DASHBOARD_REFRESH_INTERVAL_SEC,
    EMOJI_TO_STYLE,
)
from dashboard import stats
from dashboard.auth import DASHBOARD_KEY, check_auth, set_auth_cookie
from database.users import get_dashboard_user_stats

TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates"


def _require_auth(handler: Callable) -> Callable:
    """Декоратор: возвращает 401 если запрос не аутентифицирован."""

    async def wrapper(request: web.Request) -> web.StreamResponse:
        if not check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        return await handler(request)

    return wrapper


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------


async def handle_dashboard(request: web.Request) -> web.Response:
    """Отдаёт HTML-страницу дашборда."""
    if not check_auth(request):
        return web.Response(
            text="401 Unauthorized — append ?key=YOUR_KEY to URL",
            status=401,
            content_type="text/plain",
        )

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=False,
    )
    template = env.get_template("dashboard.html")
    style_emoji_pairs = [[style, emoji] for emoji, style in EMOJI_TO_STYLE.items()]
    html = template.render(
        style_emoji_pairs=style_emoji_pairs,
        dashboard_refresh_interval_ms=DASHBOARD_REFRESH_INTERVAL_SEC * 1000,
        dashboard_auto_refresh_duration_ms=DASHBOARD_AUTO_REFRESH_DURATION_SEC * 1000,
    )

    response = web.Response(text=html, content_type="text/html")
    set_auth_cookie(response)
    return response


@_require_auth
async def handle_stats(request: web.Request) -> web.Response:
    """Возвращает текущие метрики в JSON."""
    return web.json_response(stats.get_stats())


@_require_auth
async def handle_logs(request: web.Request) -> web.Response:
    """Возвращает последние записи логов в JSON."""
    limit = int(request.query.get("limit", stats.MAX_LOG_ENTRIES))
    return web.json_response(stats.get_logs(limit=limit))


@_require_auth
async def handle_users(request: web.Request) -> web.Response:
    """Возвращает статистику пользователей из Supabase."""
    user_stats = await get_dashboard_user_stats()
    return web.json_response(user_stats)


# ---------------------------------------------------------------------------
# Фабрика приложения
# ---------------------------------------------------------------------------


def create_app() -> web.Application:
    """Создаёт aiohttp-приложение со всеми маршрутами."""
    app = web.Application()
    app.router.add_get("/dashboard", handle_dashboard)
    app.router.add_get("/api/stats", handle_stats)
    app.router.add_get("/api/logs", handle_logs)
    app.router.add_get("/api/users", handle_users)
    return app


async def start_dashboard_server() -> web.AppRunner | None:
    """Запускает HTTP-сервер дашборда на $PORT.

    Возвращает runner (для cleanup) или None если DASHBOARD_KEY не задан.
    """
    if not DASHBOARD_KEY:
        print("⚠️  DASHBOARD_KEY not set — dashboard disabled.")
        return None

    port = int(os.getenv("PORT", "8080"))
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"📊 Dashboard running on port {port} (/dashboard?key=...)")
    return runner
