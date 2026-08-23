"""LLM 客户端：宿主注入优先，配置自建兜底。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, List, Optional

log = logging.getLogger("neko_arcade.llm")


class LLMThrottle:
    """每分钟最多 N 次调用的限流器。"""

    def __init__(self, max_calls_per_minute: int = 15) -> None:
        self.max_calls = max(1, max_calls_per_minute)
        self._calls: List[float] = []

    def acquire(self) -> bool:
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self.max_calls:
            return False
        self._calls.append(now)
        return True


class LLMClient:
    """统一 LLM 调用：OpenAI / Anthropic / Gemini / 兼容 OpenAI 本地。"""

    def __init__(self, provider: str, model: str, api_key: str = "",
                 base_url: str = "", timeout: float = 25.0) -> None:
        self.provider = (provider or "").lower()
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    async def call(self, prompt: str) -> str:
        try:
            import httpx
        except ImportError:
            raise RuntimeError("需要 httpx")
        if self.provider in ("openai", "openai_compatible"):
            url = self.base_url or "https://api.openai.com/v1/chat/completions"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            body = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.8}
        elif self.provider == "anthropic":
            url = self.base_url or "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            body = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 512}
        elif self.provider == "gemini":
            url = (self.base_url or "https://generativelanguage.googleapis.com/v1beta/models") + f"/{self.model}:generateContent"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                url += f"?key={self.api_key}"
            body = {"contents": [{"parts": [{"text": prompt}]}]}
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        if self.provider == "anthropic":
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if self.provider == "gemini":
            return data["candidates"][0]["content"]["parts"][0]["text"]
        return data["choices"][0]["message"]["content"].strip()


class LLMProvider:
    """装配宿主注入或配置自建的 LLM，提供限流调用。"""

    def __init__(self, max_calls_per_minute: int = 15) -> None:
        self._host_call: Optional[Callable[[str], Any]] = None
        self._client: Optional[LLMClient] = None
        self._throttle = LLMThrottle(max_calls_per_minute)

    def set_host_call(self, call: Callable[[str], Any]) -> None:
        """宿主注入（主项目 __call_llm）。"""
        self._host_call = call

    def set_client(self, provider: str, model: str, api_key: str = "", base_url: str = "") -> None:
        """配置自建客户端。"""
        if provider and model:
            self._client = LLMClient(provider, model, api_key, base_url)

    async def call(self, prompt: str) -> Optional[str]:
        """限流内调用 LLM，失败返回 None。

        优先级：配置自建客户端(新 LLM) → 宿主注入(__call_llm)。
        符合「配置了所有游戏都走新 LLM, 没配用主的」。
        """
        if not self._throttle.acquire():
            log.warning("LLM 限流，拒绝调用")
            return None
        try:
            if self._client:
                return await self._client.call(prompt)
            if self._host_call:
                result = self._host_call(prompt)
                return await result if asyncio.iscoroutine(result) else str(result)
        except Exception as exc:
            log.warning("LLM 调用失败: %s", exc)
        return None
