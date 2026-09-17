from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.routing import Mount

from AI_chatbot import main as run_streamlit

app = FastAPI(title="AskBuddy")


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/streamlit")


@app.api_route("/streamlit", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def streamlit_proxy() -> dict[str, str]:
    # Vercel Python runtime cannot directly serve Streamlit UI as a web app.
    # This route acts as a compatibility landing page for deployment checks,
    # while the Streamlit app itself is intended to run locally with:
    #   streamlit run AI_chatbot.py
    return {
        "message": "AskBuddy is configured for local Streamlit execution.",
        "run": "streamlit run AI_chatbot.py",
        "environment": {
            "GOOGLE_API_KEY": "set" if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") else "missing",
            "ASKBUDDY_MODEL": os.getenv("ASKBUDDY_MODEL", "gemini-3.6-flash"),
        },
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
