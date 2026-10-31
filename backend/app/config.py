"""Settings loaded from the environment (see .env.example)."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PayPal (sandbox only; there is no live-mode switch)
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_webhook_id: str = ""
    paypal_base_url: str = SANDBOX_BASE_URL

    # AI
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_model_cheap: str = "claude-haiku-5-5"
    copilot_max_tool_calls: int = Field(default=5, ge=1, le=5)

    # Storage / jobs
    database_url: str = "sqlite:///./proofcheck.db"
    render_workflows_enabled: bool = False
    upload_dir: str = "./data/uploads"

    # App
    session_secret: str = ""
    demo_merchant_email: str = ""
    demo_merchant_password_hash: str = ""
    store_names: str = "Lotus & Loom Demo Store"
    daily_llm_call_cap: int = Field(default=500, ge=0)
    retention_days: int = Field(default=30, ge=1)
    cors_allowed_origins: str = "http://localhost:5173"

    @field_validator("paypal_base_url")
    @classmethod
    def _sandbox_only(cls, value: str) -> str:
        if value.rstrip("/") != SANDBOX_BASE_URL:
            raise ValueError(f"PAYPAL_BASE_URL must be {SANDBOX_BASE_URL} (sandbox only)")
        return SANDBOX_BASE_URL

    @property
    def store_name_list(self) -> list[str]:
        return [n.strip() for n in self.store_names.split(";") if n.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
