"""Configuration explicite, validée avant tout accès réseau ou disque."""
import json
import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_name: str = "com.visa.cemea.visapay"
    name: str = "Visa Pay"
    description: str = ""
    language: str = "fr"
    country: str = "cd"
    max_score: int = Field(default=2, ge=1, le=5)
    max_fetch: int = Field(default=500, ge=1, le=100000)

    @field_validator("package_name", "language", "country")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("valeur vide")
        return value.strip()


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db_path: Path = Path("reviews.db")
    export_dir: Path = Path("exports")
    apps: list[AppConfig] = Field(default_factory=lambda: [AppConfig()], min_length=1)
    retries: int = Field(default=2, ge=0, le=10)
    pause_seconds: float = Field(default=0.6, ge=0, le=60)
    timeout_seconds: float = Field(default=20, gt=0, le=120)

    @field_validator("apps")
    @classmethod
    def unique_apps(cls, apps: list[AppConfig]) -> list[AppConfig]:
        if len({app.package_name for app in apps}) != len(apps):
            raise ValueError("applications en doublon")
        return apps


def load_settings(path: str | None = None) -> Settings:
    filename = path or os.getenv("PLAYSTORE_CONFIG")
    return Settings.model_validate(json.loads(Path(filename).read_text())) if filename else Settings()
