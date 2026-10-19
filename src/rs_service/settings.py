from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """RS-Service 配置；所有字段可用 RS_ 前缀环境变量覆盖。"""

    model_config = SettingsConfigDict(env_prefix="RS_", env_file=".env", extra="ignore")

    data_dir: Path = Path.home() / ".local" / "share" / "rs-service"
    db_path: Path | None = None
    database_url: str | None = None

    celestrak_base_url: str = "https://celestrak.org/NORAD/elements/gp.php"
    celestrak_timeout_seconds: int = 60
    http_proxy: str | None = None

    sync_max_retries: int = 3
    sync_retry_delays: str = "300,900,2700"
    sync_source_code: str = "celestrak-gp"

    api_host: str = "127.0.0.1"
    api_port: int = 8766
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765

    def resolved_db_path(self) -> Path:
        path = self.db_path or (self.data_dir / "rs.db")
        return Path(os.path.expanduser(str(path)))

    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{self.resolved_db_path()}"

    def retry_delays(self) -> list[int]:
        values = [item.strip() for item in self.sync_retry_delays.split(",") if item.strip()]
        return [int(item) for item in values] or [300, 900, 2700]

    def ensure_dirs(self) -> None:
        self.resolved_db_path().parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
