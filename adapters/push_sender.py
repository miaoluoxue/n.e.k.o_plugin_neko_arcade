"""推送适配：通过主项目 push_message 发送文字/图片/音频。"""

from __future__ import annotations

from typing import Any, List, Optional


class PushSender:
    """封装主项目 push_message，支持文本、图片、音频混合推送。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.source = "neko_arcade"

    async def text(self, text: str, visibility: Optional[List[str]] = None,
                   ai_behavior: str = "blind") -> None:
        """推一条纯文本消息。"""
        await self._push([{"type": "text", "text": text}], visibility, ai_behavior)

    async def text_with_image(self, text: str, image_bytes: bytes,
                              mime: str = "image/png") -> None:
        """推文本 + 图片。"""
        await self._push([
            {"type": "text", "text": text},
            {"type": "image", "data": image_bytes, "mime": mime},
        ])

    async def text_with_audio(self, text: str, audio_bytes: bytes,
                              mime: str = "audio/wav") -> None:
        """推文本 + 音频（语音播报）。"""
        await self._push([
            {"type": "text", "text": text},
            {"type": "audio", "data": audio_bytes, "mime": mime},
        ])

    async def help_doc(self, title: str, image_bytes: bytes,
                       text: Optional[str] = None) -> None:
        """推帮助文档图片。"""
        parts: List[dict] = []
        if text:
            parts.append({"type": "text", "text": text})
        parts.append({"type": "image", "data": image_bytes, "mime": "image/png"})
        await self._push(parts)

    async def _push(self, parts: List[dict],
                    visibility: Optional[List[str]] = None,
                    ai_behavior: str = "blind") -> None:
        # 注意：SDK 的 push_message 是同步方法（返回回执对象，不是协程），不能 await。
        # 之前写成 await self.plugin.push_message(...) 会抛
        # "TypeError: object dict can't be used in 'await' expression"。
        self.plugin.push_message(
            source=self.source,
            parts=parts,
            visibility=visibility or ["chat", "hud"],
            ai_behavior=ai_behavior,
        )
