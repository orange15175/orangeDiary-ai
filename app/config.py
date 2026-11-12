import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """集中读取环境变量，业务代码不再散落 os.getenv 调用。"""
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout_seconds: float
    deepseek_input_price_per_million: float
    deepseek_output_price_per_million: float
    ai_service_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
            deepseek_timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60")),
            deepseek_input_price_per_million=float(
                os.getenv("DEEPSEEK_INPUT_PRICE_PER_MILLION", "0")
            ),
            deepseek_output_price_per_million=float(
                os.getenv("DEEPSEEK_OUTPUT_PRICE_PER_MILLION", "0")
            ),
            ai_service_token=os.getenv("AI_SERVICE_TOKEN", "").strip(),
        )
