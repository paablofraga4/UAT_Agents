from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    evidence_dir: Path = Path("./evidence")
    sessions_dir: Path = Path("./sessions")
    db_path: Path = Path("./storage/uat_agents.db")

    headless: bool = False
    browser_viewport_width: int = 1366
    browser_viewport_height: int = 820

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    def ensure_dirs(self) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
