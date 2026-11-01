"""Configuration explicite, validée avant tout accès réseau ou disque."""
import json
import os
import re
from typing import Literal
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def validate_email(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
        raise ValueError("Adresse email simple invalide")
    return value.lower()


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_name: str = "com.visa.cemea.visapay"
    name: str = "Visa Pay"
    description: str = ""
    language: str = "fr"
    country: str = "cd"
    max_score: int = Field(default=2, ge=1, le=5)
    max_fetch: int = Field(default=500, ge=1, le=100000)
    recipient_email: str | None = None
    recipient_authorized: bool = False
    interval_minutes: int = Field(default=1440, ge=1, le=525600)

    @field_validator('recipient_email')
    @classmethod
    def valid_recipient(cls, value: str | None) -> str | None:
        return validate_email(value) if value is not None else None

    @model_validator(mode='after')
    def authorized_recipient(self):
        if self.recipient_authorized and not self.recipient_email:
            raise ValueError('Destinataire autorisé manquant')
        return self

    @field_validator("package_name", "language", "country")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("valeur vide")
        return value.strip()


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    model: str = Field(default="gpt-4o-mini", min_length=1, max_length=200)
    max_reviews_per_batch: int = Field(default=40, ge=1, le=100)
    max_batches_per_run: int = Field(default=5, ge=1, le=100)
    max_input_chars: int = Field(default=24000, ge=4000, le=200000)
    max_output_tokens: int = Field(default=3000, ge=256, le=16000)
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def complete_prices(self):
        if (self.input_cost_per_million is None) != (self.output_cost_per_million is None):
            raise ValueError("Fournir les deux tarifs ou aucun")
        return self


class EmailConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: bool = False
    provider: Literal['smtp', 'resend'] = 'smtp'
    host: str | None = None
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal['starttls', 'ssl'] = 'starttls'
    from_address: str | None = None
    auth_required: bool = True
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_deliveries_per_run: int = Field(default=10, ge=1, le=100)

    @field_validator('from_address')
    @classmethod
    def valid_sender(cls, value: str | None) -> str | None:
        return validate_email(value) if value is not None else None

    @field_validator('host')
    @classmethod
    def valid_host(cls, value: str | None) -> str | None:
        if value is not None and (not value or any(c.isspace() for c in value) or '/' in value):
            raise ValueError('Hôte SMTP invalide')
        return value

    @model_validator(mode='after')
    def configured(self):
        if self.enabled and not self.from_address:
            raise ValueError('Email activé : expéditeur requis')
        if self.enabled and self.provider == 'smtp' and not self.host:
            raise ValueError('SMTP activé : hôte requis')
        return self


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailConfig = Field(default_factory=EmailConfig)
    report_dir: Path = Path('reports')
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
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
    env_path = Path(filename).resolve().parent / '.env' if filename else Path.cwd() / '.env'
    load_dotenv(env_path, override=False)
    return Settings.model_validate(json.loads(Path(filename).read_text())) if filename else Settings()
