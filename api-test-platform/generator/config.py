"""Phase 5 - 配置加载（.env + 环境变量，零第三方依赖）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """极简 .env 加载器：KEY=VALUE，支持 # 注释与空行；不覆盖已存在的环境变量。"""
    env_file = path or Path(__file__).resolve().parent.parent / ".env"
    loaded: dict[str, str] = {}
    if not env_file.exists():
        return loaded
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
        loaded[key] = value
    return loaded


@dataclass
class LLMSettings:
    provider: str = "mock"          # mock | openai | anthropic
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    temperature: float = 0.2
    timeout: int = 60

    @classmethod
    def from_env(cls) -> "LLMSettings":
        load_dotenv()
        provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        api_key = os.getenv("LLM_API_KEY", "").strip()
        model = os.getenv("LLM_MODEL", "").strip()
        base_url = os.getenv("LLM_BASE_URL", "").strip()

        # 约定：检测到 DEEPSEEK_API_KEY 且未显式配置 LLM_* 时，自动采用 DeepSeek
        # （OpenAI 兼容协议），零额外配置即可用。
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key and deepseek_key:
            provider = provider or "openai"
            api_key = deepseek_key
            model = model or "deepseek-chat"
            base_url = base_url or "https://api.deepseek.com/v1"

        if not provider:
            provider = "mock"
        return cls(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
            timeout=int(os.getenv("LLM_TIMEOUT", "60")),
        )
