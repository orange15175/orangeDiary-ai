import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# Local development reads orangeDiary-ai/.env; deployed containers still use injected environment variables.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class Settings:
    """集中读取环境变量，业务代码不再散落 os.getenv 调用。"""
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout_seconds: float
    java_backend_base_url: str
    java_backend_timeout_seconds: float
    deepseek_input_price_per_million: float
    deepseek_output_price_per_million: float
    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_username: str
    mysql_password: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
            deepseek_timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60")),
            java_backend_base_url=os.getenv("JAVA_BACKEND_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
            java_backend_timeout_seconds=float(os.getenv("JAVA_BACKEND_TIMEOUT_SECONDS", "8")),
            deepseek_input_price_per_million=float(
                os.getenv("DEEPSEEK_INPUT_PRICE_PER_MILLION", "0")
            ),
            deepseek_output_price_per_million=float(
                os.getenv("DEEPSEEK_OUTPUT_PRICE_PER_MILLION", "0")
            ),
            mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1").strip(),
            mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
            mysql_database=os.getenv("MYSQL_DATABASE", "calorie_tracker").strip(),
            mysql_username=os.getenv("MYSQL_USERNAME", "").strip(),
            mysql_password=os.getenv("MYSQL_PASSWORD", ""),
        )
