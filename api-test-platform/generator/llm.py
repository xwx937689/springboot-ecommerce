"""Phase 5 - 可插拔 LLM 客户端。

provider:
  openai    -> OpenAI 及一切兼容 /chat/completions 的服务（通义 DashScope 兼容模式、
               DeepSeek、Moonshot、豆包、Ollama 等，配 LLM_BASE_URL 即可）
  anthropic -> Claude（Messages API，httpx 直连）
  mock      -> 不调用 LLM（返回空，让编排器退化为纯规则基线）
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from .config import LLMSettings


class LLMError(RuntimeError):
    pass


@dataclass
class LLMReply:
    text: str
    model: str
    usage: dict


class OpenAICompatClient:
    """OpenAI / 通义 / DeepSeek / Moonshot / Ollama 等兼容端点。"""

    def __init__(self, settings: LLMSettings):
        if not settings.api_key:
            raise LLMError("LLM_API_KEY 未配置（.env）")
        self.settings = settings

    def complete(self, system: str, user: str) -> LLMReply:
        from openai import OpenAI

        client = OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url or None,
            timeout=self.settings.timeout,
        )
        resp = client.chat.completions.create(
            model=self.settings.model or "gpt-4o-mini",
            temperature=self.settings.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return LLMReply(
            text=resp.choices[0].message.content or "",
            model=resp.model,
            usage=({"prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens} if resp.usage else {}),
        )


class AnthropicClient:
    def __init__(self, settings: LLMSettings):
        if not settings.api_key:
            raise LLMError("LLM_API_KEY 未配置（.env）")
        self.settings = settings

    def complete(self, system: str, user: str) -> LLMReply:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.settings.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.settings.model or "claude-sonnet-4-5",
                "max_tokens": 4096,
                "temperature": self.settings.temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.settings.timeout,
        )
        if resp.status_code != 200:
            raise LLMError(f"Anthropic HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return LLMReply(
            text="".join(b.get("text", "") for b in data.get("content", [])),
            model=data.get("model", ""),
            usage=data.get("usage", {}),
        )


def make_client(settings: LLMSettings):
    if settings.provider == "openai":
        return OpenAICompatClient(settings)
    if settings.provider == "anthropic":
        return AnthropicClient(settings)
    if settings.provider == "mock":
        return None
    raise LLMError(f"未知 LLM_PROVIDER: {settings.provider}（可选 openai/anthropic/mock）")


def extract_json_array(text: str) -> list[dict]:
    """从 LLM 回复中稳健提取 JSON 数组：容忍 ```json 包裹与前后杂文。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise LLMError("回复中未找到 JSON 数组")
    return json.loads(text[start:end + 1])
