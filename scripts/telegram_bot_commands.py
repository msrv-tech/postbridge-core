#!/usr/bin/env python3
"""Inspect and reset Telegram bot commands across scopes and languages."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import requests


API_BASE = "https://api.telegram.org"
DEFAULT_LANGUAGES: tuple[str | None, ...] = (None, "en", "ru")


@dataclass(frozen=True)
class ScopeSpec:
    label: str
    payload: dict[str, Any]


def _commands_for_default_locale(default_locale: str) -> dict[str | None, list[dict[str, str]]]:
    localized = {
        "en": [
            {"command": "start", "description": "Connect Telegram"},
            {"command": "help", "description": "Open website"},
        ],
        "ru": [
            {"command": "start", "description": "Главное меню"},
            {"command": "help", "description": "Открыть сайт"},
        ],
    }
    default_locale = (default_locale or "en").strip().lower() or "en"
    default_commands = localized.get(default_locale, localized["en"])
    return {
        None: default_commands,
        "en": default_commands,
        "ru": default_commands,
    }


def _scopes(chat_id: int | None, user_id: int | None) -> list[ScopeSpec]:
    scopes = [
        ScopeSpec("default", {"type": "default"}),
        ScopeSpec("all_private_chats", {"type": "all_private_chats"}),
    ]
    if chat_id is not None:
        scopes.append(ScopeSpec(f"chat:{chat_id}", {"type": "chat", "chat_id": chat_id}))
        scopes.append(
            ScopeSpec(
                f"chat_admins:{chat_id}",
                {"type": "chat_administrators", "chat_id": chat_id},
            )
        )
        if user_id is not None:
            scopes.append(
                ScopeSpec(
                    f"chat_member:{chat_id}:{user_id}",
                    {"type": "chat_member", "chat_id": chat_id, "user_id": user_id},
                )
            )
    return scopes


def _call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{API_BASE}/bot{token}/{method}",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"{method} failed: {body}")
    return body


def inspect(token: str, *, chat_id: int | None, user_id: int | None) -> int:
    rows: list[dict[str, Any]] = []
    for scope in _scopes(chat_id, user_id):
        for language_code in DEFAULT_LANGUAGES:
            payload: dict[str, Any] = {"scope": scope.payload}
            if language_code is not None:
                payload["language_code"] = language_code
            body = _call(token, "getMyCommands", payload)
            rows.append(
                {
                    "scope": scope.label,
                    "language_code": language_code or "<default>",
                    "commands": body.get("result", []),
                }
            )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def reset(
    token: str,
    *,
    default_locale: str,
    chat_id: int | None,
    user_id: int | None,
    include_chat_scopes: bool,
) -> int:
    scopes = _scopes(chat_id if include_chat_scopes else None, user_id if include_chat_scopes else None)
    commands_by_lang = _commands_for_default_locale(default_locale)
    for scope in scopes:
        for language_code in DEFAULT_LANGUAGES:
            payload: dict[str, Any] = {"scope": scope.payload}
            if language_code is not None:
                payload["language_code"] = language_code
            _call(token, "deleteMyCommands", payload)
            set_payload = dict(payload)
            set_payload["commands"] = commands_by_lang[language_code]
            _call(token, "setMyCommands", set_payload)
            print(
                f"updated scope={scope.label} language={language_code or '<default>'}",
                file=sys.stderr,
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("inspect", "reset"))
    parser.add_argument("--token", default=os.getenv("TELEGRAM_BOT_TOKEN"))
    parser.add_argument("--default-locale", default=os.getenv("POSTBRIDGE_DEFAULT_LOCALE", "en"))
    parser.add_argument("--chat-id", type=int)
    parser.add_argument("--user-id", type=int)
    parser.add_argument(
        "--include-chat-scopes",
        action="store_true",
        help="When resetting, also touch chat-specific scopes if --chat-id is provided.",
    )
    args = parser.parse_args()

    if not args.token:
        print("TELEGRAM_BOT_TOKEN is required", file=sys.stderr)
        return 2

    if args.action == "inspect":
        return inspect(args.token, chat_id=args.chat_id, user_id=args.user_id)
    return reset(
        args.token,
        default_locale=args.default_locale,
        chat_id=args.chat_id,
        user_id=args.user_id,
        include_chat_scopes=args.include_chat_scopes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
