from functools import lru_cache
from typing import Optional

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class Settings(BaseSettings):
    app_name: str = "ai-learning"
    database_url: str = "postgresql+psycopg://urpapa:postgres@localhost:5432/ai_learning"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: Optional[str] = None
    llm_model: str = "gpt-4.1-mini"
    upload_dir: str = "uploads"
    upload_max_bytes: int = 5 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_prefix="AI_LEARNING_",
        env_file=".env",
        yaml_file="application.yml",
        yaml_config_section="ai_learning",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
