# UAT Agents

Local-first AI browser operator for UAT / sandbox / staging web applications.

The user opens a target web app from this workspace, **logs in manually with their own credentials**, and then asks an AI agent to perform actions inside the application using natural language. The agent observes, plans, executes (via Playwright), validates and produces an auditable evidence report.

> This is an **AI-controlled browser workspace**, not a chatbot.

---

## Architecture

```
┌─────────────────────┐      WebSocket / REST      ┌──────────────────────────┐
│  Next.js workspace  │ ─────────────────────────► │  FastAPI backend         │
│  (React UI)         │ ◄───────────────────────── │  ── Session Broker       │
└─────────────────────┘   events: step / log /     │  ── Browser Runner       │
                          screenshot / report      │  ── LangGraph Agents     │
                                                   │  ── Evidence Store       │
                                                   └──────────────────────────┘
                                                              │
                                                              ▼
                                                   ┌──────────────────────────┐
                                                   │  Playwright (Chromium)   │
                                                   │  user-controlled session │
                                                   └──────────────────────────┘
```

### Multi-agent graph (LangGraph)

```
        ┌──────────┐
        │ Observer │  perceives page (URL, title, AX tree, visible text, screenshot)
        └────┬─────┘
             ▼
        ┌──────────┐
        │ Planner  │  natural language → structured Plan (allowlisted actions only)
        └────┬─────┘
             ▼
        ┌──────────┐
        │ Executor │  runs steps through Browser Runner
        └────┬─────┘
             ▼
        ┌──────────┐
        │Validator │  checks expected outcome
        └────┬─────┘
             ▼
        ┌──────────┐
        │ Reporter │  produces final evidence report
        └──────────┘
```

The agent **never** receives passwords, MFA codes, cookies or tokens. It only sees a session id, page context, the user instruction and prior step results.

---

## Stack

- **Frontend**: Next.js 14 (App Router) + React + Tailwind
- **Backend**: FastAPI + WebSockets
- **Browser**: Playwright (Chromium, headed)
- **Agents**: LangGraph + OpenAI (default: `gpt-4o`)
- **Observability**: Langfuse (optional)
- **Storage**: SQLite (sessions/tasks) + filesystem (screenshots, reports)

---

## Quick start

### 1. Backend

> **Requires Python 3.11 or 3.12.** On Python 3.14 some deps lack prebuilt wheels and pip will try to compile native code (Rust + MSVC).

```bash
cd backend
py -3.12 -m venv .venv          # Windows: pick 3.12 explicitly
# python3.12 -m venv .venv      # macOS / Linux
. .venv/Scripts/activate           # Windows
# source .venv/bin/activate        # macOS / Linux
pip install -r requirements.txt
playwright install chromium
cp ../.env.example .env
# edit .env and set OPENAI_API_KEY
python run.py --reload          # use this on Windows (sets Proactor loop policy)
# or, on macOS / Linux:
# uvicorn app.main:app --reload --port 8765
```

> Port 8000 is often reserved by Windows (Hyper-V/WSL excluded port range → `WinError 10013`). 8765 is a safe default.

### 2. Frontend

```bash
cd frontend
echo "NEXT_PUBLIC_API_BASE=http://127.0.0.1:8765" > .env.local
npm install
npm run dev
```

Then open http://localhost:3000.

### 3. End-to-end flow

1. Enter the target URL (e.g. your UAT app) and click **Open Session**.
2. A Chromium window opens. **Log in manually** (with MFA if needed).
3. Click **I am logged in** in the workspace.
4. Type a natural language instruction in the command box.
5. Watch the agent plan, execute and validate.
6. Read the report under `evidence/<session>/<task>/report.md`.

---

## Allowed browser actions (MVP)

The Planner can only emit actions from this allowlist; the Runner enforces it:

- `goto(url)`
- `click_text(text)` / `click_role(role, name)`
- `fill_label(label, value)` / `fill_placeholder(placeholder, value)`
- `select_option(label, value)`
- `press_key(key)`
- `wait(ms)` / `wait_for_text(text)`
- `screenshot()`
- `assert_text(text)`
- `extract_context()`

Destructive actions are not part of the MVP.

---

## Safety principles

1. No credential collection, ever.
2. Cookies / tokens never reach the LLM.
3. No automated login.
4. No destructive actions.
5. No real patient data.
6. No production targets.
7. Every action is logged + screenshotted.
8. Stop on first failed step (no aggressive guessing).
9. Allowlist-only action vocabulary.
10. Modular for future security hardening.

---

## What this is NOT

A chatbot, a scraper, a credential manager, an autonomous web agent, a production RPA bot, a replacement for QA engineers.

## What this IS

A **local-first prototype** of an AI browser operator for UAT workflows with manual user authentication, controlled browser automation, visible execution and auditable evidence.

---

## Roadmap

- [ ] Embedded browser streaming (CDP / noVNC / WebRTC)
- [ ] Browser containers (one isolated browser per session)
- [ ] Application Profiles (learned knowledge of target apps)
- [ ] Recipes (deterministic replays of successful flows)
- [ ] Self-repair on failed steps
- [ ] Human takeover mid-task
- [ ] Enterprise SSO (Entra ID)
- [ ] MCP server exposing browser actions / profiles / recipes
