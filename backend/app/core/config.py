from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://bis:standards@localhost/bis_standards",
        description="SQLAlchemy database URL",
    )
    secret_key: str = "dev-secret"
    api_v1_prefix: str = "/api/v1"

    # LLM (Google Gemini generateContent API). Unset key → extractive mode.
    llm_api_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        description="Base URL of the Gemini API",
    )
    llm_api_key: str = Field(default="", description="Google AI Studio API key")
    llm_model: str = Field(default="gemini-3.6-flash", description="Gemini model name")
    llm_timeout_seconds: float = Field(default=20.0, description="LLM request timeout")

settings = Settings()