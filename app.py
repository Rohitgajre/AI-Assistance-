from __future__ import annotations

import json
import os
from typing import Any


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Minimal ASGI app for Vercel compatibility without third-party runtime deps."""
    if scope.get("type") != "http":
        return

    path = scope.get("path", "/")
    if path in {"", "/"}:
        payload = {
            "message": "AskBuddy is configured for local Streamlit execution.",
            "run": "streamlit run AI_chatbot.py",
            "environment": {
                "GOOGLE_API_KEY": "set"
                if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
                else "missing",
                "ASKBUDDY_MODEL": os.getenv("ASKBUDDY_MODEL", "gemini-3.6-flash"),
            },
        }
        status_code = 200
    elif path == "/health":
        payload = {"status": "ok"}
        status_code = 200
    else:
        payload = {"error": "Not found"}
        status_code = 404

    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
