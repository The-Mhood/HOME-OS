from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOMEOS_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "HOME OS"
    app_version: str = "1.0.0"
    # Secure-by-default: never bind to public interfaces unless explicitly configured
    bind_host: str = Field(default="127.0.0.1")
    bind_port: int = Field(default=8420)
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".local" / "share" / "home-os" / "data")
    static_dir: Path | None = Field(default=None)
    database_url: str | None = Field(default=None)

    # Security settings
    session_ttl_minutes: int = Field(default=720)
    pairing_ttl_seconds: int = Field(default=300)
    replay_window_seconds: int = Field(default=60)
    rate_limit_requests_per_minute: int = Field(default=180)
    auth_rate_limit_per_minute: int = Field(default=20)
    authorized_file_roots: list[str] = Field(default_factory=list)
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8420",
            "http://127.0.0.1:8420",
            "app://.",
        ]
    )
    cors_origin_regex: str | None = Field(
        default=r"^https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+|[a-zA-Z0-9-]+\.local|[a-z0-9-]+\.e2b\.app)(:\d+)?$"
    )

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db_file = (self.data_dir / "home_os.db").resolve()
        return f"sqlite:///{db_file}"

    @property
    def vault_key_path(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return (self.data_dir / "master.key").resolve()

    @property
    def tls_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return (self.data_dir / "tls").resolve()

    @property
    def transfers_dir(self) -> Path:
        d = (self.data_dir / "transfers").resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d


def get_settings() -> Settings:
    return Settings()
