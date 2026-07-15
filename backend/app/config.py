from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    # Optional shared-secret auth. Empty = no auth (local-first default).
    # When set, every REST call, WebSocket and evidence file request must
    # present it (Authorization: Bearer, X-API-Key header, or ?token= query).
    api_token: str = ""

    evidence_dir: Path = Path("./evidence")
    sessions_dir: Path = Path("./sessions")
    db_path: Path = Path("./storage/uat_agents.db")
    # The ONLY directory upload_file may read from. The agent can never
    # exfiltrate arbitrary host files into the target web app.
    uploads_dir: Path = Path("./uploads")

    headless: bool = False
    browser_viewport_width: int = 1366
    browser_viewport_height: int = 820
    # Explicit Chromium binary (managed/CI environments where the Playwright
    # download cache is unavailable). Empty = Playwright's own resolution.
    browser_executable_path: str = ""

    # Comma-separated domain suffixes the agent may goto() (e.g.
    # "uat.example.com,staging.example.com"). Empty = any domain. Manual
    # navigation through the live view is never restricted — this only bounds
    # the agent.
    allowed_domains: str = ""

    # Persist storage_state (cookies/tokens) on confirm-login. Off by default:
    # the file is plaintext credentials at rest. When on, it is written 0600
    # and deleted when the session closes.
    persist_storage_state: bool = False

    def allowed_domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_domains.split(",") if d.strip()]

    def ensure_dirs(self) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
