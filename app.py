from __future__ import annotations

import json
import os
from typing import Any


def _build_chat_page() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AskBuddy</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #0f172a;
      --panel: #111827;
      --card: #1f2937;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #7c3aed;
      --accent-2: #22c55e;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: linear-gradient(135deg, #020617, #111827 45%, #1e1b4b);
      color: var(--text);
      min-height: 100vh;
      display: grid;
      place-items: center;
    }
    .shell {
      width: min(900px, 92vw);
      background: rgba(15, 23, 42, 0.8);
      border: 1px solid rgba(148, 163, 184, 0.2);
      border-radius: 18px;
      box-shadow: 0 30px 60px rgba(0,0,0,0.35);
      overflow: hidden;
    }
    .topbar {
      padding: 1rem 1.25rem;
      border-bottom: 1px solid rgba(148,163,184,0.2);
      background: rgba(17,24,39,0.9);
    }
    h1 { margin: 0; font-size: clamp(1.4rem, 2vw, 2rem); }
    .content {
      padding: 1rem;
    }
    .chat {
      min-height: 320px;
      max-height: 52vh;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      padding: 0.75rem;
      border-radius: 12px;
      background: rgba(17, 24, 39, 0.9);
      border: 1px solid rgba(148,163,184,0.2);
    }
    .bubble {
      max-width: 82%;
      padding: 0.8rem 1rem;
      border-radius: 16px;
      line-height: 1.5;
      white-space: pre-wrap;
    }
    .user { align-self: flex-end; background: var(--accent); }
    .bot { align-self: flex-start; background: var(--card); }
    form {
      display: flex;
      gap: 0.75rem;
      margin-top: 1rem;
    }
    textarea {
      flex: 1;
      border: 1px solid rgba(148,163,184,0.25);
      border-radius: 12px;
      background: rgba(15, 23, 42, 0.8);
      color: var(--text);
      resize: vertical;
      min-height: 56px;
      padding: 0.9rem 1rem;
      font: inherit;
    }
    button {
      border: none;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--accent), #3b82f6);
      color: white;
      font-weight: 700;
      padding: 0.9rem 1.3rem;
      cursor: pointer;
    }
    .hint {
      margin-top: 0.75rem;
      color: var(--muted);
      font-size: 0.9rem;
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <h1>AskBuddy</h1>
    </div>
    <div class="content">
      <div id="chat" class="chat">
        <div class="bubble bot">Hi! Ask a question and I’ll answer with the configured Gemini model.</div>
      </div>
      <form id="chat-form">
        <textarea id="prompt" placeholder="Ask a question..." required></textarea>
        <button type="submit">Send</button>
      </form>
      <div class="hint">Configured model: <span id="model">gemini-3.6-flash</span></div>
    </div>
  </div>

  <script>
    const chat = document.getElementById('chat');
    const form = document.getElementById('chat-form');
    const prompt = document.getElementById('prompt');
    const modelName = document.getElementById('model');
    modelName.textContent = 'gemini-3.6-flash';

    function addMessage(text, role) {
      const div = document.createElement('div');
      div.className = `bubble ${role}`;
      div.textContent = text;
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const value = prompt.value.trim();
      if (!value) return;

      addMessage(value, 'user');
      prompt.value = '';

      const sendButton = form.querySelector('button');
      sendButton.disabled = true;
      sendButton.textContent = 'Thinking…';

      try {
        const response = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: value })
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || 'Request failed');
        }
        addMessage(data.answer || 'No answer returned.', 'bot');
      } catch (error) {
        addMessage(error.message || 'Something went wrong.', 'bot');
      } finally {
        sendButton.disabled = false;
        sendButton.textContent = 'Send';
      }
    });
  </script>
</body>
</html>
"""


async def _read_body(receive: Any) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        elif message["type"] == "http.disconnect":
            break
    return b"".join(chunks)


def _json_response(status: int, payload: dict[str, Any]) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps(payload).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    return body, headers


def _html_response() -> tuple[bytes, list[tuple[bytes, bytes]]]:
    body = _build_chat_page().encode("utf-8")
    headers = [
        (b"content-type", b"text/html; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    return body, headers


def _generate_answer(prompt: str) -> str:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY or GEMINI_API_KEY")

    model_name = os.getenv("ASKBUDDY_MODEL", "gemini-3.6-flash")

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from chat_service import ask_model
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError("Required runtime dependencies are not installed.") from exc

    llm = ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=api_key,
        temperature=0.2,
        max_retries=2,
        timeout=30,
    )
    return ask_model(llm, prompt)


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Serve the AskBuddy user interface and chat API."""
    if scope.get("type") != "http":
        return

    path = scope.get("path", "/")

    if path in {"", "/"}:
        body, headers = _html_response()
        status = 200
    elif path == "/health":
        body, headers = _json_response(200, {"status": "ok"})
        status = 200
    elif path == "/api/chat":
        raw = await _read_body(receive)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            data = {}

        prompt_text = str(data.get("prompt", "")).strip()
        if not prompt_text:
            body, headers = _json_response(400, {"error": "Prompt is required."})
            status = 400
        else:
            try:
                answer = _generate_answer(prompt_text)
                body, headers = _json_response(200, {"answer": answer})
                status = 200
            except Exception as exc:  # pragma: no cover - network/runtime guard
                body, headers = _json_response(
                    500,
                    {"error": str(exc) or "Unable to generate a response."},
                )
                status = 500
    else:
        body, headers = _json_response(404, {"error": "Not found"})
        status = 404

    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
