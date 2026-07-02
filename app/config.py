"""Runtime configuration, loaded from environment (PINCHIVE_* vars)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PINCHIVE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default=Path("./data"))
    redis_url: str = Field(default="redis://localhost:6379")

    max_concurrency: int = Field(default=2, ge=1, le=16)
    dl_sleep: float = Field(default=0.8, ge=0.0)

    # Credential keep-alive cron. The job re-hits Pinterest and persists the
    # rotated session cookie so a registered credential stays alive on its own.
    # refresh_every_hours > 0 runs every N hours (e.g. 6 -> 00,06,12,18);
    # set it to 0 to run once a day at refresh_hour instead.
    refresh_every_hours: int = Field(default=6, ge=0, le=24)
    refresh_hour: int = Field(default=4, ge=0, le=23)
    refresh_minute: int = Field(default=0, ge=0, le=59)

    # Optional Playwright re-login fallback: when a session is genuinely dead
    # (server-side logout), try a headless browser login to mint fresh cookies.
    # Off by default; requires the image built with the browser (INSTALL_PLAYWRIGHT)
    # and a stored login profile. See docs/credential-refresh.md.
    use_playwright_fallback: bool = Field(default=False)

    # Automatic board re-sync: periodically re-download boards to pick up new
    # pins (cheap — the per-board archive means only new pins are fetched).
    # Runs every N hours; 0 disables the cron entirely. Boards can opt out
    # individually (Board.auto_resync).
    resync_every_hours: int = Field(default=24, ge=0, le=24)
    resync_minute: int = Field(default=30, ge=0, le=59)

    # UI page sizes.
    per_page_boards: int = Field(default=24, ge=1, le=200)
    per_page_pins: int = Field(default=60, ge=1, le=500)
    per_page_dupes: int = Field(default=20, ge=1, le=200)

    def refresh_hours(self) -> set[int]:
        """The set of hours the keep-alive cron fires at."""
        if self.refresh_every_hours and self.refresh_every_hours > 0:
            return set(range(0, 24, self.refresh_every_hours))
        return {self.refresh_hour}

    def resync_hours(self) -> set[int]:
        """Hours the board auto-resync cron fires at (empty = disabled)."""
        if self.resync_every_hours and self.resync_every_hours > 0:
            return set(range(0, 24, self.resync_every_hours))
        return set()

    # ---- derived paths ----
    @property
    def db_path(self) -> Path:
        return self.data_dir / "pinchive.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def boards_dir(self) -> Path:
        return self.data_dir / "boards"

    @property
    def cookies_dir(self) -> Path:
        return self.data_dir / "cookies"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.boards_dir, self.cookies_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
