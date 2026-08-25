"""推送适配：通过主项目 push_message 发送文字/图片/音频。

图片显示策略（重要）：
宿主(character_runtime)只把 ai_behavior="respond"/"read" 的图片 parts 当作
AI 视觉输入(stream_image 喂给 LLM)，ai_behavior="blind" 的图片 parts 被直接
丢弃，用户聊天窗口看不到。但前端聊天窗口(react-neko-chat)用 ReactMarkdown
渲染文本，``![图](http://...)`` 会被渲染成真实图片。

因此本模块的图片推送统一走「存 static/cards/ → 文本内嵌 markdown 图片 URL」
通道：图片写入插件 static 目录，宿主静态服务(/plugin/neko_arcade/ui/)直接
提供访问，前端 markdown 渲染出图。这是当前宿主版本唯一可靠的游戏图片显示
通道（验证: 48916 端口 /plugin/neko_arcade/ui/*.png 返回 200）。
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, List, Optional

DEFAULT_PLUGIN_SERVER_PORT = "48916"


class PushSender:
    """封装主项目 push_message，支持文本、图片、音频混合推送。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.source = "neko_arcade"

    # ── 图片落盘 → markdown URL ─────────────────────────

    def _static_dir(self) -> Optional[Path]:
        """插件 static 目录(宿主静态 UI 服务 /plugin/neko_arcade/ui/ 的根)。"""
        try:
            cfg_dir = getattr(self.plugin, "config_dir", None)
            if cfg_dir is None:
                return None
            return Path(cfg_dir) / "static"
        except Exception:
            return None

    def _base_url(self) -> str:
        """宿主插件 server 地址(USER_PLUGIN_SERVER_PORT, 默认 48916)。"""
        port = os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "") or \
               os.getenv("USER_PLUGIN_SERVER_PORT", "") or DEFAULT_PLUGIN_SERVER_PORT
        return f"http://127.0.0.1:{port}/plugin/neko_arcade/ui/"

    async def save_image(self, image_bytes: bytes, mime: str = "image/png") -> Optional[str]:
        """把图片写入 static/cards/，返回可访问的 http URL；失败返回 None。

        文件名带毫秒时间戳 + 内容哈希，避免缓存与冲突。
        """
        static = self._static_dir()
        if not static:
            return None
        try:
            cards = static / "cards"
            cards.mkdir(parents=True, exist_ok=True)
            if "jpeg" in mime or "jpg" in mime:
                ext = ".jpg"
            elif "gif" in mime:
                ext = ".gif"
            else:
                ext = ".png"
            digest = hashlib.md5(image_bytes[:4096]).hexdigest()[:8]
            name = f"{int(time.time() * 1000)}_{digest}{ext}"
            (cards / name).write_bytes(image_bytes)
            return f"{self._base_url()}cards/{name}"
        except Exception:
            return None

    # ── 推送 ──────────────────────────────────────────

    async def text(self, text: str, visibility: Optional[List[str]] = None,
                   ai_behavior: str = "blind") -> None:
        """推一条纯文本消息。"""
        await self._push([{"type": "text", "text": text}], visibility, ai_behavior)

    async def text_with_image(self, text: str, image_bytes: bytes,
                              mime: str = "image/png") -> None:
        """推文本 + 图片（图片经 static + markdown 通道显示）。"""
        url = await self.save_image(image_bytes, mime)
        if url:
            # 前端 ReactMarkdown 会把 ![alt](http://...) 渲染成真实图片
            content = f"{text}\n\n![游戏图片]({url})"
        else:
            content = text
        await self._push([{"type": "text", "text": content}])

    async def text_with_image_url(self, text: str, url: str) -> None:
        """推文本 + 图片 URL（图片已存在于 static 下, 直接 markdown 引用）。"""
        content = f"{text}\n\n![游戏图片]({url})" if url else text
        await self._push([{"type": "text", "text": content}])

    def static_url(self, relative_path: str) -> str:
        """把 static 下的相对路径转成可访问的 http URL。

        relative_path 形如 "img/neko/可爱/xx.png"(相对 static 目录)。
        """
        return f"{self._base_url()}{relative_path.lstrip('/')}"

    async def text_with_audio(self, text: str, audio_bytes: bytes,
                              mime: str = "audio/wav") -> None:
        """推文本 + 音频（语音播报）。"""
        await self._push([
            {"type": "text", "text": text},
            {"type": "audio", "data": audio_bytes, "mime": mime},
        ])

    async def help_doc(self, title: str, image_bytes: bytes,
                       text: Optional[str] = None) -> None:
        """推帮助文档图片（图片经 static + markdown 通道显示）。"""
        url = await self.save_image(image_bytes, "image/png")
        lines = []
        if text:
            lines.append(text)
        if url:
            lines.append(f"![{title}]({url})")
        if not lines:
            return
        await self._push([{"type": "text", "text": "\n".join(lines)}])

    async def _push(self, parts: List[dict],
                    visibility: Optional[List[str]] = None,
                    ai_behavior: str = "blind") -> None:
        # 注意：SDK 的 push_message 是同步方法（返回回执对象，不是协程），不能 await。
        # 之前写成 await self.plugin.push_message(...) 会抛
        # "TypeError: object dict can't be used in 'await' expression"。
        #
        # target_lanlan：多猫娘(多角色)环境下不带目标角色名, 宿主 _get_session_manager("")
        # 拿不到会话, 多会话时 fallback 也为空 → 推送会被宿主直接丢弃(lifekit/neko_live
        # 都显式带 target_lanlan)。解析优先级: ctx 当前角色 → 环境变量 → 无(单角色兜底)。
        target = self._resolve_target_lanlan()
        self.plugin.push_message(
            source=self.source,
            parts=parts,
            visibility=visibility or ["chat", "hud"],
            ai_behavior=ai_behavior,
            target_lanlan=target or None,
        )

    def _resolve_target_lanlan(self) -> str:
        """解析推送目标角色名(猫娘名)。

        顺序: ctx._current_lanlan → ctx._host_ctx._current_lanlan →
        环境变量(NEKO_TARGET_LANLAN / NEKO_LANLAN_NAME / NEKO_HER_NAME)。
        拿不到返回空串(宿主单角色 fallback 可兜住)。
        """
        ctx = getattr(self.plugin, "ctx", None)
        host_ctx = getattr(ctx, "_host_ctx", None) if ctx is not None else None
        for source in (
            getattr(ctx, "_current_lanlan", None) if ctx is not None else None,
            getattr(host_ctx, "_current_lanlan", None) if host_ctx is not None else None,
            os.getenv("NEKO_TARGET_LANLAN", ""),
            os.getenv("NEKO_LANLAN_NAME", ""),
            os.getenv("NEKO_HER_NAME", ""),
        ):
            if isinstance(source, str) and source.strip():
                return source.strip()
        return ""
