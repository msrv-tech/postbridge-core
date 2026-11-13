"""Drive the real singleton Core app through ASGI in isolated child processes.

Provider methods are substituted before importing main. Settings validation and
PostgreSQL startup remain real; no callback is invoked directly by these tests.
"""
import json
import os
import subprocess
import sys

import pytest


_SCRIPT = r'''
import asyncio
import json
import os
from types import SimpleNamespace
import sys

case = sys.argv[1]
os.environ.update(APP_ENV="test", POSTBRIDGE_APP_MODE="selfhost", BOT_MODE="webhook", TELEGRAM_BOT_TOKEN="synthetic-token",
    BOT_WEBHOOK_BASE_URL="https://webhook.example.invalid", BOT_WEBHOOK_PATH="/internal/test-hook",
    BOT_WEBHOOK_SECRET="synthetic-webhook-secret", SENTRY_DSN="", TELEGRAM_PROXY_URL="")
if case == "polling": os.environ["BOT_MODE"]="long_polling"
if case == "missing_token": os.environ["TELEGRAM_BOT_TOKEN"]=""
if case == "missing_base": os.environ["BOT_WEBHOOK_BASE_URL"]=""
if case == "sentry": os.environ["SENTRY_DSN"]="https://synthetic@example.invalid/1"

from postbridge.botkit.platforms.telegram import runtime
from aiogram.client.session.aiohttp import AiohttpSession
from fastapi.testclient import TestClient

stats = {"events":[], "webhooks":[], "bots":0, "closes":0, "dp_shutdown":0,
    "feeds":0, "health":[], "auth_statuses":[], "commands":0, "delete_commands":0,
    "provider_transport_calls":0, "error":None}

async def forbidden_transport(*args, **kwargs):
    stats["provider_transport_calls"] += 1
    raise AssertionError("No provider transport permitted")
AiohttpSession.make_request = forbidden_transport

class FakeSession:
    async def close(self):
        stats["closes"] += 1
        stats["events"].append("bot_close")
        if case == "close_failure": raise RuntimeError("synthetic close failure")
        if case == "close_timeout": await asyncio.Event().wait()

class FakeBot:
    def __init__(self, **kwargs):
        stats["bots"] += 1
        self.session=FakeSession()
    async def delete_my_commands(self, **kwargs): stats["delete_commands"] += 1
    async def set_my_commands(self, **kwargs): stats["commands"] += 1
    async def set_chat_menu_button(self, **kwargs): pass
    async def set_webhook(self, url, **kwargs):
        assert url == "https://webhook.example.invalid/internal/test-hook"
        assert kwargs["secret_token"] == "synthetic-webhook-secret"
        assert "drop_pending_updates" not in kwargs
        stats["webhooks"].append({"url":url, "allowed_updates":kwargs["allowed_updates"],
            "secret_matches":True, "drop_pending_argument_present":False})
        if case == "startup_failure": raise RuntimeError("synthetic startup failure")
        if case == "startup_timeout": await asyncio.Event().wait()
    async def delete_webhook(self, **kwargs): raise AssertionError("Do not delete webhook")
    async def send_message(self, *args, **kwargs): raise AssertionError("Do not send")
    async def get_updates(self, **kwargs): raise AssertionError("Do not poll")
runtime.Bot=FakeBot
runtime.get_backend=lambda:SimpleNamespace(site_base_url=lambda:"https://ui.example.invalid")

class FakeDispatcher:
    async def feed_webhook_update(self, bot, update):
        assert isinstance(bot,FakeBot) and update.update_id==101
        stats["feeds"] += 1
    async def emit_shutdown(self, **kwargs):
        stats["dp_shutdown"] += 1
        stats["events"].append("dispatcher_shutdown")
        if case == "shutdown_failure": raise RuntimeError("synthetic dispatcher shutdown failure")
        if case == "shutdown_timeout": await asyncio.Event().wait()
def dispatcher():
    if runtime._dp is None: runtime._dp=FakeDispatcher()
    return runtime._dp
runtime._get_dispatcher=dispatcher
original_configure=runtime._configure_bot_ui
async def configure():
    stats["events"].append("telegram_configure")
    await original_configure()
runtime._configure_bot_ui=configure
if case=="startup_timeout": runtime._WEBHOOK_STARTUP_TIMEOUT_SECONDS=0.02
if case in {"shutdown_timeout","close_timeout"}: runtime._WEBHOOK_SHUTDOWN_TIMEOUT_SECONDS=0.02

# Import the production singleton only after substituting the provider. This
# exercises main.app's complete custom router/lifespan composition, not a clone.
from postbridge.api import main
from postbridge.db import SESSION_LOCAL
from sqlalchemy import text
# Celery validates during import; invalidate only after importing the real app
# so this case verifies the ASGI settings gate, before DB/Telegram startup.
if case == "settings_failure": os.environ["POSTBRIDGE_APP_MODE"]="invalid"
validate=main.validate_base_settings
initialize=main.init_db
def validation(settings):
    stats["events"].append("validate")
    validate(settings)
def database():
    stats["events"].append("db")
    if case=="db_failure": raise RuntimeError("synthetic database startup failure")
    initialize()
main.validate_base_settings=validation
main.init_db=database
main.sentry_sdk.init=lambda **kwargs:stats["events"].append("sentry")

route_count=len(main.app.router.routes)
runtime.setup_telegram_bot_webhook(main.app)
runtime.setup_telegram_bot_webhook(main.app)
stats["setup_idempotent"]=len(main.app.router.routes)==route_count
try:
    for _ in range(2 if case=="repeat_lifespan" else 1):
        with TestClient(main.app) as client:
            stats["health"].append(client.get("/health").status_code)
            with SESSION_LOCAL() as db: assert db.execute(text("SELECT 1")).scalar()==1
            if case not in {"missing_token","polling"}:
                stats["auth_statuses"].append(client.post("/internal/test-hook",json={"update_id":101}).status_code)
                stats["auth_statuses"].append(client.post("/internal/test-hook",json={"update_id":101},
                    headers={"X-Telegram-Bot-Api-Secret-Token":"wrong"}).status_code)
                stats["auth_statuses"].append(client.post("/internal/test-hook",json={"update_id":101},
                    headers={"X-Telegram-Bot-Api-Secret-Token":"synthetic-webhook-secret"}).status_code)
                assert client.post("/internal/test-hook",json={"invalid":True},
                    headers={"X-Telegram-Bot-Api-Secret-Token":"synthetic-webhook-secret"}).status_code==500
            else:
                stats["disabled_route_status"]=client.post("/internal/test-hook",json={"update_id":101}).status_code
            if case=="body_failure": raise RuntimeError("synthetic request context failure")
except Exception as exc:
    stats["error"]=type(exc).__name__
stats["cache_cleared"]=(runtime._bot is None and runtime._dp is None)
print("LIFESPAN_RESULT="+json.dumps(stats,sort_keys=True))
'''


def run_app(case):
    result = subprocess.run([sys.executable,"-c",_SCRIPT,case],env=dict(os.environ),
        capture_output=True,text=True,timeout=50)
    assert result.returncode==0,result.stderr
    line=next((x for x in reversed(result.stdout.splitlines()) if x.startswith("LIFESPAN_RESULT=")),None)
    assert line is not None,result.stderr
    data=json.loads(line.partition("=")[2])
    assert data["provider_transport_calls"]==0
    return data


@pytest.mark.parametrize("case",["webhook","repeat_lifespan","missing_base","sentry"])
def test_real_main_asgi_lifespan_initializes_once_and_preserves_startup_and_auth(case):
    data=run_app(case)
    cycles=2 if case=="repeat_lifespan" else 1
    assert data["error"] is None,data
    assert data["health"]==[200]*cycles
    assert data["setup_idempotent"]
    assert len(data["webhooks"])==(0 if case=="missing_base" else cycles)
    assert data["bots"]==data["closes"]==data["dp_shutdown"]==cycles
    assert data["commands"]==data["delete_commands"]==6*cycles
    assert data["auth_statuses"]==[403,403,200]*cycles
    assert data["feeds"]==cycles and data["cache_cleared"]
    events=data["events"]
    assert events.index("validate")<events.index("db")<events.index("telegram_configure")
    assert events.index("dispatcher_shutdown")<events.index("bot_close")
    if case=="sentry":assert events[0]=="sentry"
    if data["webhooks"]:
        assert data["webhooks"][0]["allowed_updates"]==[
            "message","callback_query","my_chat_member","channel_post","edited_channel_post"]


@pytest.mark.parametrize("case",["missing_token","polling"])
def test_real_main_disabled_bot_keeps_core_database_startup_without_provider(case):
    data=run_app(case)
    assert data["error"] is None and data["health"]==[200]
    assert data["bots"]==data["closes"]==data["feeds"]==0
    # The existing GET-only SPA fallback returns 405 for an absent POST route.
    assert data["webhooks"]==[] and data["disabled_route_status"]==405
    assert data["events"]==["validate","db"]


@pytest.mark.parametrize("case,kind",[("startup_failure","RuntimeError"),("startup_timeout","TimeoutError")])
def test_real_main_startup_failure_is_not_ready_and_closes_bot(case,kind):
    data=run_app(case)
    assert data["error"]==kind and data["health"]==[]
    assert data["bots"]==data["closes"]==1
    assert data["cache_cleared"] and data["dp_shutdown"]==0


@pytest.mark.parametrize("case,kind",[("body_failure","RuntimeError"),("shutdown_failure","RuntimeError"),
    ("shutdown_timeout","TimeoutError"),("close_failure","RuntimeError"),("close_timeout","TimeoutError")])
def test_real_main_shutdown_failure_or_body_error_still_releases_owned_session(case,kind):
    data=run_app(case)
    assert data["error"]==kind and data["health"]==[200]
    assert data["bots"]==data["closes"]==data["dp_shutdown"]==1
    assert data["cache_cleared"]


@pytest.mark.parametrize("case,kind,events",[("settings_failure","ConfigurationError",["validate"]),
    ("db_failure","RuntimeError",["validate","db"])])
def test_real_main_core_startup_failure_prevents_telegram_side_effects(case,kind,events):
    data=run_app(case)
    assert data["error"]==kind and data["events"]==events
    assert data["bots"]==data["closes"]==0 and data["webhooks"]==[] and data["health"]==[]
