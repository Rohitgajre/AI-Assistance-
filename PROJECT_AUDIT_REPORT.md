# Project Audit Report

## 1. Executive Summary

AskBuddy is a single-page Streamlit proof-of-concept that calls Google Gemini
through LangChain. The original implementation was functional but had an eager
global client, no user-safe provider failure path, no configuration guidance,
unbounded dependency scope, and no tests. This audit implements safe, focused
improvements without changing the core question-and-answer behavior.

## 2. Project Overview

The application accepts a question in a Streamlit chat UI, invokes a Gemini chat
model, and stores the visible transcript in Streamlit session state. There is no
persisted data.

## 3. Technology Stack

Python, Streamlit, LangChain Google GenAI integration, `python-dotenv`, Google
Gemini, pytest, and Ruff (development only).

## 4. Existing Architecture

The original repository contained one UI-and-integration script. The current
structure remains a deliberately small modular monolith: the Streamlit entry
point handles rendering/configuration and `chat_service.py` owns the testable
provider call boundary. No backend API routes, database, RAG, agents, Docker, or
CI/CD configuration exists.

## 5. Folder Structure

```
AI_chatbot.py          Streamlit entry point
chat_service.py        Model response boundary
tests/                 Unit tests
.env.example           Safe configuration template
requirements*.txt      Runtime and development dependencies
pyproject.toml         Tooling configuration
```

## 6. Issues Found

| Issue | Severity | File | Description | Recommended Fix | Status |
| --- | --- | --- | --- | --- | --- |
| Model created at import time | High | `AI_chatbot.py` | Startup/configuration errors could crash the app and clients were not explicitly reused. | Cache lazy client creation. | Fixed |
| Missing provider error handling | High | `AI_chatbot.py` | Failures exposed an unfriendly application error path. | Log server-side exception and show a safe message. | Fixed |
| No key/model configuration UX | Medium | `AI_chatbot.py` | Users had no clear setup feedback or model override. | Validate key presence and document environment variables. | Fixed |
| Broad, unused dependencies | Medium | `requirements.txt` | Packages for providers and workflows not used by the app enlarged supply-chain surface. | Retain only runtime dependencies; move tools to development file. | Fixed |
| No automated tests | Medium | project | Core response behavior had no regression coverage. | Add offline pytest tests. | Fixed |
| No lockfile or deployment automation | Low | project | Builds are not fully reproducible and deployment is manual. | Add a lockfile/CI once a target platform is selected. | Open |

## 7. Security Findings

`.env` is ignored and was not tracked in the current Git index. The audit did not
print or copy its contents. The source checks for an approved key name and never
logs the key or user prompt. There is no authentication or authorization because
this is not a multi-user backend; deploy behind appropriate access controls if
required.

The reachable Git history contains only the initial commit and no tracked `.env`;
no history rewriting was performed. Dependency vulnerability scanning could not
be run because the environment has no installed project dependencies.

## 8. Performance Findings

Caching the model client prevents reconstruction on each Streamlit rerun. One
request is still synchronous by design; it has a 30-second timeout and two
provider retries. There are no database queries, large files, or batch workloads.

## 9. Database Findings

Not applicable: no database connection, schema, query, or stored user data is
present.

## 10. AI/ML Findings

This is direct LLM invocation, not RAG or an agent workflow. Added controlled
temperature, timeout, retry limit, configurable model, empty-response rejection,
and failure handling. Remaining production concerns are provider quota/cost
monitoring, prompt/version evaluation, and content policy controls, which depend
on the intended user population and hosting platform.

## 11. API Findings

Not applicable: the project exposes no HTTP API beyond Streamlit's application
server.

## 12. Code Quality Findings

The refactor adds module/function documentation, constants, type annotations,
PEP 8 formatting, a `main()` guard, and a framework-independent service module.

## 13. Changes Implemented

- Safe startup validation, cached model creation, timeout/retries, and clear chat control.
- Server-side exception logging with a generic UI error.
- Minimal bounded runtime dependencies and separate development tooling.
- Configuration template, test configuration, tests, and updated project docs.

## 14. Files Modified

`AI_chatbot.py`, `README.md`, and `requirements.txt` were revised. Added
`chat_service.py`, `.env.example`, `requirements-dev.txt`, `pyproject.toml`,
`tests/test_chat_service.py`, and this report.

## 15. Tests Executed

The following checks were run on 2026-09-17:

| Check | Result | Notes |
| --- | --- | --- |
| `.venv\\Scripts\\python.exe -m py_compile AI_chatbot.py chat_service.py tests/test_chat_service.py` | Passed | Syntax compilation succeeded. |
| Offline `ask_model` smoke assertions | Passed | Verified trimmed output and empty-output rejection without calling Gemini. |
| `.venv\\Scripts\\python.exe -m pytest` | Passed | 3 passed in 0.08s. Pytest emitted a non-failing cache-path warning. |
| `.venv\\Scripts\\python.exe -m ruff check .` | Passed | All checks passed. |

No live Gemini request was made because doing so would require a real key and
incur an external call.

## 16. Remaining Issues

No authentication, rate limiting, persistence, observability backend, CI, Docker
image, lockfile, or deployment definition exists. These are not necessary for the
current local tool but are needed selectively for public or multi-user deployment.

## 17. Deployment Readiness

Ready for a local development launch once dependencies and a valid key are
configured. Not yet production-ready for public exposure until hosting-specific
secret management, HTTPS/access controls, monitoring, rate/cost limits, and CI
are added.

## 18. Recommended Next Steps

1. Install dependencies, run tests/lint, and manually test a valid Gemini call.
2. Choose a deployment target, then add a lockfile, CI, health monitoring, and a
   least-privilege secret integration.
3. If exposing it publicly, define an authentication, abuse-prevention, content
   safety, and data-retention policy before launch.
