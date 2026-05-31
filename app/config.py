"""Central configuration — all env-var access lives here."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "development"
    secret_key: str = "dev-secret-key"
    log_level: str = "INFO"

    # Database
    database_url: str = "sqlite+aiosqlite:///./recruitment.db"

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from_name: str = "HR Team"
    email_from_address: str = "hr@example.com"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    twilio_sms_from: str = ""

    # Portals
    naukri_api_key: str = ""
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    indeed_publisher_id: str = ""
    foundit_api_key: str = ""

    # Scheduling
    calendar_base_url: str = "https://calendar.example.com"
    interview_confirmation_base_url: str = "http://localhost:8000"

    # Google Sheets integration (email queue + master sheet)
    google_sa_credentials_json: str = ""   # full JSON string (for Vercel env vars)
    google_sa_credentials_file: str = ""   # path to .json file (for local dev)
    sheets_email_queue_id: str = "1u9hSfBLiAZW06x8zVNa_AAYrI-biMKQX2c7rXwiOl5c"
    sheets_master_id: str = "1ni68KrCfUmV-5iooy2wI201mfPgKnHOcVzQA2i4XSDI"
    use_sheets_email_queue: bool = True    # route emails through queue sheet

    # Cron security
    cron_secret: str = ""                  # set in Vercel env — protects /cron/* endpoints

    # Feature flags
    use_mock_adapters: bool = True
    auto_outreach_enabled: bool = True
    outreach_delay_seconds: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
