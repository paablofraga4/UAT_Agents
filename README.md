# UAT Agents

AI browser operator for UAT / sandbox / staging web applications.

The user opens a target web app from this workspace, **logs in with their own
credentials inside the embedded live browser view**, and then asks an AI agent
to perform actions inside the application using natural language. The agent
observes, decides one step at a time, executes (via Playwright), validates and
produces an auditable evidence report.

> This is an **AI-controlled browser workspace**, not a chatbot.

---

## Architecture

```
┌─────────────────────┐   REST + WebSockets        ┌──────────────────────────┐
│  Next.js workspace  │ ─────────────────────────► │  FastAPI backend         │
│  ── live browser    │  events: step/log/report   │  ── Session Broker       │
│     view + remote   │  frames: live JPEG stream  │  ── Browser Runner       │
│     mouse/keyboard  │ ◄───────────────────────── │  ── ReAct agent (graph)  │
└─────────────────────┘                            │  ── Evidence Store       │
                                                   └──────────┬───────────────┘
                                                              ▼
                                                   ┌──────────────────────────┐
                                                   │  Playwright (Chromium)   │
                                                   │  one browser per session │
                                                   │  headed OR headless      │
                                                   └──────────────────────────┘
```

### Agent loop (ReAct)

```
observer → pilot ──act──► executor → observer (cycle)
              │
              ├──done────► reporter → END
              └──give_up─► reporter → END
```

Each turn the **Pilot** receives the instruction, the step history, the current
page context **and the viewport screenshot** (multimodal), and decides exactly
one action. Its reply is constrained by a strict Structured-Outputs JSON
schema, so malformed actions are impossible by construction.

The **Observer** reports the page app-agnostically: main-content text, a
guaranteed PRIMARY NAV summary, open dialog warnings, and a **numbered list of
interactive elements** (`[12][button] Save`). Each number maps to a stamped DOM
attribute, so the preferred click action is `click_element(12)` — no text or
locator guessing.

The agent **never** receives passwords, MFA codes, cookies or tokens. It only
sees a session id, page context, the user instruction and prior step results.
Page content is explicitly treated as untrusted data (prompt-injection rule in
the system prompt), uploads are confined to a dedicated directory, and agent
navigation can be fenced with a domain allowlist.

---

## Stack

- **Frontend**: Next.js 14 (App Router) + React + Tailwind — includes the live
  interactive browser view (JPEG stream + remote mouse/keyboard)
- **Backend**: FastAPI + WebSockets
- **Browser**: Playwright (Chromium, headed or headless), one worker thread +
  browser per session
- **Agents**: plain async ReAct loop + OpenAI (default: `gpt-4o`, vision + structured outputs) — no heavy agent framework
- **Storage**: SQLite (sessions/tasks) + filesystem (screenshots, reports)
- **Tests**: pytest + a local fixture web app + headless Chromium (CI on
  GitHub Actions)

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
2. The target app appears in the **live browser view**. Click inside it and
   **log in with your credentials** (MFA included) — everything you type goes
   straight to the real page; nothing is stored or shown to the agent. With
   `HEADLESS=true` this works fully remotely; with `HEADLESS=false` a local
   Chromium window mirrors it.
3. Click **I am logged in**.
4. Type a natural-language instruction — or switch the selector to **Form
   test** to have the agent design and execute a test matrix against a form.
5. Watch the agent decide, act and validate step by step, live.
6. Read the report under `evidence/<session>/<task>/report.md` (plus
   `steps.json` and `debug.json` with machine-readable diagnostics).

### Running the tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

The suite drives every browser action against a local fixture app (forms with
validation, an incrementally-loading chat with a contenteditable composer,
modals, hidden file inputs) through real headless Chromium, and runs the agent
loop with the LLM mocked. CI runs it on every PR.

---

## Allowed browser actions

The Pilot can only emit actions from this allowlist; the Runner enforces it
again at execution time:

- `click_element(element_id)` ← preferred: click by observed number
- `click_text(text)` / `click_role(role, name)`
- `goto(url)` (subject to `ALLOWED_DOMAINS`)
- `fill_label(label, value)` / `fill_placeholder(placeholder, value)`
- `type_text(target, value)` — real inputs AND contenteditable/rich editors
  (LinkedIn, Slack, Notion, ProseMirror, Quill, Lexical…)
- `select_option(label, value)`
- `press_key(key)`
- `scroll(target?, direction, amount)` — scrolls the right container (chat
  lists, dropdowns, infinite feeds)
- `upload_file(target, paths)` — paths confined to `UPLOADS_DIR`
- `wait(ms)` / `wait_for_text(text)`
- `screenshot()`
- `assert_text(text)` / `assert_field_value(label, value)` /
  `assert_url_contains(text)`
- `extract_context()`

Destructive actions are not part of the vocabulary.

---

## Security model

1. No credential collection, ever — login happens in the live view, straight
   into the real page.
2. Cookies / tokens never reach the LLM. `storage_state` persistence is **off
   by default** (`PERSIST_STORAGE_STATE`); when enabled it is written `0600`
   and deleted when the session closes.
3. Optional shared-secret auth (`API_TOKEN`): protects every REST endpoint,
   WebSocket and evidence file. Set it before exposing the backend beyond
   localhost.
4. Evidence (screenshots/reports) served through a guarded route (token +
   path-traversal check), not an open static mount.
5. `upload_file` is confined to `UPLOADS_DIR` — the agent cannot exfiltrate
   arbitrary host files.
6. Agent navigation can be fenced with `ALLOWED_DOMAINS` (suffix allowlist).
7. Page content is treated as untrusted data — the Pilot is instructed to
   ignore instructions embedded in web pages.
8. Every action is logged + screenshotted; reports include machine-readable
   failure diagnostics.
9. One agent task at a time per session (409 otherwise).
10. Allowlist-only action vocabulary; no automated login; no production targets.

---

## What this is NOT

A chatbot, a scraper, a credential manager, a production RPA bot, a replacement
for QA engineers.

## What this IS

An AI browser operator for UAT workflows with in-workspace user authentication,
controlled browser automation, visible execution and auditable evidence.

---

## Roadmap

- [x] Embedded live browser view + remote input (login without a local window)
- [x] Numbered-element observation + `click_element`
- [x] Multimodal Pilot (screenshots) + structured outputs
- [x] Form test mode (test matrix + pass/fail report)
- [x] Deterministic test suite + CI
- [ ] CDP screencast transport (higher fps than JPEG polling)
- [ ] Browser containers (one isolated container per session)
- [ ] Application Profiles (learned knowledge of target apps)
- [ ] Recipes (deterministic replays of successful flows)
- [ ] Human takeover mid-task (pause agent, act, resume)
- [ ] Multi-user auth (per-user session ownership, SSO)
- [ ] MCP server exposing browser actions / profiles / recipes
