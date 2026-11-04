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
    paypal_sdk_domains: str = ""  # comma-separated domains for the JS SDK v6 client token

    # AI
    groq_api_key: str = ""
    groq_model_vision: str = "qwen/qwen3.8-27b"
    groq_model_text: str = "openai/gpt-oss-120b"
    copilot_max_tool_calls: int = Field(default=5, ge=1, le=5)

    # Storage / jobs
    database_url: str = "sqlite:///./proofcheck.db"
    render_workflows_enabled: bool = False
    render_api_key: str = ""
    render_workflow_slug: str = ""  # the workflow's slug in Render; tasks are `<slug>/<task>`
    upload_dir: str = "./data/uploads"

    # App
    session_secret: str = ""
    demo_merchant_email: str = ""
    demo_merchant_password_hash: str = ""
    store_names: str = "Lotus & Loom Demo Store"
    daily_llm_call_cap: int = Field(default=500, ge=0)
    retention_days: int = Field(default=30, ge=1)
    cors_allowed_origins: str = "http://localhost:5173"
    demo_mode: bool = False  # enables POST /api/demo/disputes (sandbox demos only)
    cookie_secure: bool = True  # Chrome accepts Secure cookies on http://localhost

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
    def sdk_domain_list(self) -> list[str]:
        return [d.strip() for d in self.paypal_sdk_domains.split(",") if d.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
