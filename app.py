"""Compatibility shim for deployment platforms that require a top-level app object.

This project is intended to run as a Streamlit app via `streamlit run AI_chatbot.py`.
This file exists only to satisfy deployment tooling that insists on a top-level
`app` object. It does not replace the real Streamlit UI.
"""

from __future__ import annotations

import json
from typing import Any


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Minimal ASGI entry that satisfies platform expectations."""
    if scope.get("type") != "http":
        return

    body = json.dumps({
        "status": "ok",
        "message": "AskBuddy is running via Streamlit. Use 'streamlit run AI_chatbot.py' locally.",
    }).encode("utf-8")

    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body})
