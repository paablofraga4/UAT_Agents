"""Domain knowledge base ("playbooks").

Each web domain the agent operates gets a Markdown playbook — prior context
about the app: its tabs/sections, how to do common things, and gotchas (reject
the cookie banner first, the UI is in Spanish, messaging needs the search box…).
The Pilot reads it before and during a task, so it starts with a mental model
instead of rediscovering the app every run.

Two layers, both keyed by registrable-ish domain (`www.linkedin.com` →
`linkedin.com`):

- **Curated** (`knowledge_dir/<domain>.md`): hand-authored, committed, stable.
  This is what a human drops in — e.g. `linkedin.com.md`.
- **Learned** (`knowledge_learned_dir/<domain>.md`): findings the agent appends
  after tasks (runtime, gitignored, bounded). This is the "va guardando los
  hallazgos" part.

Both are concatenated (curated first) and injected into the Pilot prompt.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings


# Caps so the playbook can't blow up the LLM context or grow without bound.
MAX_PLAYBOOK_CHARS = 6000        # total injected into the prompt
MAX_LEARNED_CHARS = 8000         # on-disk learned file
MAX_LEARNED_ENTRIES = 40         # newest kept, oldest dropped

_ENTRY_SEP = "\n\n---\n\n"
_lock = threading.Lock()
# (path, mtime) → text cache so per-turn loads don't re-read/​re-stat churn.
_cache: dict[Path, tuple[float, str]] = {}


def domain_for_url(url: str | None) -> str | None:
    """`https://www.linkedin.com/feed/` → `linkedin.com`. None if no host."""
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower().strip()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _safe_name(domain: str) -> str:
    # Domains are host names; still, never let one escape the knowledge dir.
    return domain.replace("/", "_").replace("\\", "_").replace("..", "_")


def curated_path(domain: str) -> Path:
    return settings.knowledge_dir / f"{_safe_name(domain)}.md"


def learned_path(domain: str) -> Path:
    return settings.knowledge_learned_dir / f"{_safe_name(domain)}.md"


def _read_cached(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    _cache[path] = (mtime, text)
    return text


def load_playbook(domain: str | None) -> str:
    """Curated + learned knowledge for a domain, bounded for prompt injection.
    Empty string when nothing is known about the domain."""
    if not domain:
        return ""
    curated = _read_cached(curated_path(domain)).strip()
    learned = _read_cached(learned_path(domain)).strip()

    parts: list[str] = []
    if curated:
        parts.append(curated)
    if learned:
        parts.append("## Learned from previous runs\n\n" + learned)
    if not parts:
        return ""

    doc = "\n\n".join(parts)
    if len(doc) > MAX_PLAYBOOK_CHARS:
        # Keep the curated head (most reliable) and trim the tail.
        doc = doc[:MAX_PLAYBOOK_CHARS].rsplit("\n", 1)[0] + "\n…[playbook truncated]"
    return doc


def load_playbook_for_url(url: str | None) -> str:
    return load_playbook(domain_for_url(url))


def record_findings(domain: str | None, task: str, findings: str) -> None:
    """Append a findings entry to the domain's learned file, keeping it bounded.
    Best-effort: never raises into the caller."""
    findings = (findings or "").strip()
    if not domain or not findings:
        return
    # Ignore the model's "nothing new" sentinels.
    if findings.lower() in {"none", "n/a", "nada", "sin hallazgos", "no findings"}:
        return

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    task_line = (task or "").strip().replace("\n", " ")[:120]
    entry = f"### {stamp} — {task_line}\n{findings}"

    path = learned_path(domain)
    try:
        with _lock:
            existing = ""
            if path.exists():
                existing = path.read_text(encoding="utf-8").strip()
            entries = [e for e in existing.split(_ENTRY_SEP) if e.strip()] if existing else []
            entries.append(entry)
            entries = entries[-MAX_LEARNED_ENTRIES:]
            doc = _ENTRY_SEP.join(entries)
            while len(doc) > MAX_LEARNED_CHARS and len(entries) > 1:
                entries.pop(0)
                doc = _ENTRY_SEP.join(entries)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(doc + "\n", encoding="utf-8")
            _cache.pop(path, None)
    except OSError:
        pass


def known_domains() -> list[str]:
    """Domains that have a curated and/or learned playbook."""
    seen: set[str] = set()
    for d in (settings.knowledge_dir, settings.knowledge_learned_dir):
        try:
            for f in d.glob("*.md"):
                seen.add(f.stem)
        except OSError:
            continue
    return sorted(seen)
