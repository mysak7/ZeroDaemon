"""Application settings loaded from config/settings.yaml + environment variables."""

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

_CONFIG_PATH = Path("config/settings.yaml")


def _load_yaml_defaults() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZERODAEMON_",
        env_file=".env",
        extra="ignore",
    )

    db_path: str = "zerodaemon.db"
    rag_path: str = "zerodaemon_rag"
    models_config_path: str = "config/models.yaml"
    log_level: str = "INFO"
    daemon_poll_interval: int = 86400
    daemon_paused: bool = False
    ollama_base_url: str = "http://localhost:11434"

    # API keys — loaded from env vars (ANTHROPIC_API_KEY / OPENAI_API_KEY)
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    syl_api_key: str = Field(default="", alias="SYL_API_KEY")
    syl_base_url: str = "http://syl:8001/v1"
    mcp_server_url: str = ""
    mcp_api_key: str = Field(default="", alias="MCP_API_KEY")

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs):
        sources = super().settings_customise_sources(settings_cls, **kwargs)
        return sources

    def model_post_init(self, __context) -> None:
        # Merge YAML defaults for keys not set via env
        defaults = _load_yaml_defaults()
        for key, value in defaults.items():
            if key in self.model_fields and not os.environ.get(f"ZERODAEMON_{key.upper()}"):
                try:
                    object.__setattr__(self, key, value)
                except Exception:
                    pass


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
