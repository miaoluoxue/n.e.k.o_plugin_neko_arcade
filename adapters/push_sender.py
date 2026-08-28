"""推送适配：通过主项目 push_message 发送文字/图片/音频。

图片显示策略（重要）：
- 旧宿主：图片只能走「存 static/cards/ → 文本内嵌 markdown 图片 URL」通道
  （前端 ReactMarkdown 渲染），markdown 是唯一用户可见方式。
- 新宿主（#2835 合并后）：SDK 提供 ``ctx.images.upload()`` —— 上传图片返回
  canonical image part，配合 ``visibility=["chat"]`` 直接在聊天窗渲染为
  原生图片气泡，无需 markdown 变通。本模块优先走原生通道，SDK 不支持时
  回退 markdown（兼容旧宿主）。

注意：宿主不支持 audio/video parts(见 text_with_audio 注释)。
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
        """推文本 + 图片。

        优先走原生图片通道(#2835): ctx.images.upload() 上传 → canonical
        image part + visibility=["chat"] → 聊天窗渲染原生图片气泡。
        SDK 不支持(旧宿主)时回退 markdown URL 通道(static/cards)。
        """
        if await self._push_native_image(text, image_bytes):
            return
        # 回退: 旧宿主 markdown 通道
        url = await self.save_image(image_bytes, mime)
        if url:
            content = (
                f"{text}\n\n"
                f'<img src="{url}" alt="游戏图片" '
                f'style="max-width:100%;height:auto;border-radius:8px;" />'
            )
        else:
            content = text
        await self._push([{"type": "text", "text": content}])

    async def text_with_image_url(self, text: str, url: str) -> None:
        """推文本 + 图片 URL。

        优先原生通道(URL 已由 ctx.images.upload() 产生时); 否则 markdown 引用。
        """
        if await self._push_native_image(text, url=url):
            return
        content = (
            f"{text}\n\n"
            f'<img src="{url}" alt="游戏图片" '
            f'style="max-width:100%;height:auto;border-radius:8px;" />'
        ) if url else text
        await self._push([{"type": "text", "text": content}])

    async def _push_native_image(self, text: str, image_bytes: Optional[bytes] = None,
                                 url: Optional[str] = None) -> bool:
        """尝试走原生图片通道(ctx.images.upload + canonical image part)。

        返回 True 表示已推送(原生通道可用); False 表示 SDK 不支持, 调用方
        应回退 markdown。visibility=["chat"] 让图片在用户聊天窗可见。
        """
        try:
            images_api = getattr(self.plugin.ctx, "images", None)
            upload = getattr(images_api, "upload", None)
            if not callable(upload):
                return False
            if url is None and not image_bytes:
                return False
            if url is None:
                # 上传 bytes → canonical part
                part = await upload(image_bytes or b"", timeout=8.0)
            else:
                # 已是 upload 产生的 URL, 直接构造 part
                part = {"type": "image", "url": url, "mime": "image/jpeg"}
            parts = []
            if text:
                parts.append({"type": "text", "text": text})
            parts.append(part)
            self.plugin.push_message(
                source=self.source,
                parts=parts,
                visibility=["chat"],
                ai_behavior="read",
                target_lanlan=self._resolve_target_lanlan() or None,
            )
            return True
        except Exception:
            # SDK 不支持/上传失败 → 回退 markdown
            return False

    def static_url(self, relative_path: str) -> str:
        """把 static 下的相对路径转成可访问的 http URL。

        relative_path 形如 "img/neko/可爱/xx.png"(相对 static 目录)。
        """
        return f"{self._base_url()}{relative_path.lstrip('/')}"

    # 注意: 宿主不支持 audio/video parts —— character_runtime 对 type != "image"
    # 的 part 直接 warning 后 drop(stream_audio 是实时麦克风 PCM 管线, 非通用
    # 文件注入器)。因此不要尝试 push audio part 给用户。
    # 语音播报的正确姿势: 宿主会自动把 chat 通道文字 TTS 播放(官方 short_tts_line
    # 契约), 插件只需保证文本是 TTS-friendly 短句(见 TTSClient), 无需推音频数据。

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
