"""小游戏标准契约：所有小游戏必须实现的适配接口。"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional, Tuple


class GameAdapter(abc.ABC):
    """小游戏适配基类。子类必须实现 id/name/description 和 handle_action。

    游戏适配约定:
    - 基础属性: id/name/description/icon/version(类属性声明)
    - 关键词/别名: 写在 data/config/{id}/keywords.json(主插件统一读取,
      用于路由和 LLM 工具描述), 不在代码里声明。
    """

    id: str = ""
    name: str = ""
    description: str = ""
    version: str = "0.1.0"
    icon: str = "🎮"

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.enabled = True
        self._push: Any = None
        self._img: Any = None
        self._tts: Any = None
        self._llm: Any = None
        self._photo: Any = None

    def bind_services(self, push=None, img=None, tts=None, llm=None,
                      photo=None) -> None:
        """绑定插件服务（由注册表在注册时调用），游戏可通过 self 调用。

        photo: PhotoBridge 实例(插件主体通用发图桥接), 游戏可用 self.send_photo
        自主发图, 无需自己实现图库扫描/分类/推送。
        """
        self._push = push
        self._img = img
        self._tts = tts
        self._llm = llm
        self._photo = photo

    # ── 发图桥接(插件主体通用能力) ─────────

    async def send_photo(self, user_id: str, category: Optional[str] = None,
                         caption: str = "", auto: bool = False) -> Dict[str, Any]:
        """通过插件主体 PhotoBridge 自主发一张图到聊天框。

        任何游戏适配后都可直接调用: 图库扫描/分类/推送/上传由桥接统一处理。
        返回 {ok, style, category, rarity, summary, caption, photo}。
        """
        if not self._photo:
            return {"ok": False, "summary": "发图桥接未就绪喵", "error": "no_bridge"}
        return await self._photo.send_photo(user_id, category=category,
                                            caption=caption, auto=auto)

    async def pick_photo_for_delivery(self, category: Optional[str] = None,
                                      caption: str = "") -> Dict[str, Any]:
        """通过桥接取一张图(不推送), 供 handle_action 返回 images 交 brain 推送。

        「游戏适配插件」输出契约: 游戏不直接 push, 只取图数据返回 images,
        brain 统一编排推送。返回 {ok, image: {text, bytes, mime, url}, ...}。
        """
        if not self._photo:
            return {"ok": False, "summary": "发图桥接未就绪喵", "error": "no_bridge"}
        return await self._photo.pick_for_delivery(category=category, caption=caption)

    async def send_auto_photo(self, user_id: str) -> Dict[str, Any]:
        """后台自动发图(桥接): 只发本地图库, 配文随机。"""
        if not self._photo:
            return {"ok": False, "summary": "发图桥接未就绪喵", "error": "no_bridge"}
        return await self._photo.send_auto(user_id)

    def photo_categories(self) -> List[str]:
        """图库分类列表(桥接)。"""
        return self._photo.get_categories() if self._photo else []

    async def upload_photo(self, user_id: str, name: str,
                           data_b64: str = "", data_bytes: bytes = b"",
                           category: str = "默认") -> Dict[str, Any]:
        """用户上传图片到图库(桥接)。"""
        if not self._photo:
            return {"ok": False, "message": "发图桥接未就绪喵"}
        return await self._photo.upload_photo(user_id, name=name,
                                              data_b64=data_b64,
                                              data_bytes=data_bytes,
                                              category=category)

    # ── 输出契约(游戏适配插件) ─────────────
    #
    # 游戏 handle_action 只返回结构化结果, 不参与任何推送:
    #   return {"facts": [...], "outcome": "win", "message": "结算文本",
    #           "images": [{"text": "配文", "bytes": img_bytes, "mime": "image/png"}]}
    # brain 统一负责: 推 message、推 images、渲染高光卡片、生成 summary。
    #
    # 以下 push_* 方法已废弃(仅兼容历史游戏/on_tick 后台提醒使用):
    # 新游戏 handle_action 内不得调用, 应返回 images 数据交给 brain 推送。

    @staticmethod
    def build_image(text: str = "", image_bytes: bytes = b"",
                    mime: str = "image/png", url: str = "") -> Dict[str, Any]:
        """构造 handle_action 返回的 images 元素(由 brain 统一推送)。"""
        img: Dict[str, Any] = {"text": text}
        if url:
            img["url"] = url
        else:
            img["bytes"] = image_bytes
            img["mime"] = mime
        return img

    async def push_text(self, text: str) -> None:
        """推送文本到聊天框(已废弃: 仅 on_tick 后台提醒/历史兼容使用)。"""
        if self._push:
            await self._push.text(text)

    async def push_text_image(self, text: str, image_bytes: bytes,
                              mime: str = "image/png") -> None:
        """推送文本+图片到聊天框(已废弃: 仅历史兼容, 新游戏用 images 返回)。"""
        if self._push:
            await self._push.text_with_image(text, image_bytes, mime)

    async def push_text_image_url(self, text: str, url: str) -> None:
        """推送文本 + 图片 URL(已废弃: 仅历史兼容, 新游戏用 images 返回)。"""
        if self._push:
            await self._push.text_with_image_url(text, url)

    async def push_help(self, title: str, image_bytes: bytes,
                        text: str = "") -> None:
        """推送帮助文档(已废弃: 帮助由 brain.show_help 统一处理)。"""
        if self._push:
            await self._push.help_doc(title, image_bytes, text)

    async def render_card(self, game_name: str, title: str,
                          lines: List[Tuple[str, str]],
                          subtitle: str = "", mood: str = "calm") -> Optional[bytes]:
        """渲染结果卡片图片。"""
        if self._img:
            return await self._img.render_card(game_name, title, lines, subtitle, mood)
        return None

    async def render_help_img(self, game_name: str,
                              commands: List[Tuple[str, str]],
                              footer: str = "") -> Optional[List[bytes]]:
        """渲染帮助文档图片(多页时返回每页 PNG bytes 列表)。"""
        if self._img:
            return await self._img.render_help(game_name, commands, footer)
        return None

    async def render_html(self, html: str, css: str = "", width: int = 720,
                          height: int = 600, game_name: str = "",
                          selector: str = "body", brand_footer: bool = False) -> Optional[bytes]:
        """渲染 HTML 为 PNG 图片（插件端渲染方案，供游戏接入）。

        优先用 Playwright(Chromium) 渲染，支持游戏自定义 HTML/CSS 风格；
        brand_footer=True 时叠加统一品牌落款「N.E.K.O 猫娘小游戏 × 游戏名」。
        渲染失败返回 None，游戏自行降级。
        """
        if not self._img:
            return None
        renderer = getattr(self._img, "render_html", None)
        if not renderer:
            return None
        return await renderer(html, css=css, width=width, height=height,
                              game_name=game_name or self.name,
                              selector=selector, brand_footer=brand_footer)

    async def render_avatar(self, mood: str = "calm", size: int = 128) -> Optional[bytes]:
        """渲染猫娘表情头像。"""
        if self._img:
            return self._img.render_neko_avatar(mood, size)
        return None

    def tts_note(self, text: str) -> None:
        """记录 TTS 语音行。"""
        if self._tts:
            self._tts.note_tts_line(text)

    async def call_llm(self, prompt: str) -> Optional[str]:
        """调用 LLM。"""
        if self._llm:
            return await self._llm.call(prompt)
        return None

    # ── 生命周期（可选覆写） ─────────────────

    async def on_register(self) -> None:
        """注册时初始化（存档迁移等）。"""

    async def on_unload(self) -> None:
        """卸载时清理资源。"""

    async def on_start(self, user_id: str) -> None:
        """会话开始。"""

    async def on_stop(self, user_id: str) -> None:
        """会话结束。"""

    async def on_tick(self, user_id: str) -> None:
        """由大脑每秒 tick 调用（可选覆写，用于超时/不活跃检测等）。"""

    # ── 核心接口（必须实现） ─────────────────

    @abc.abstractmethod
    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """处理玩家行为，返回结构化结果。

        facts: [{"kind": "catch", "name": "鲲", "rarity": "legendary"}, ...]
        outcome: "caught_legendary" / "caught" / "trash" / "win" / "lose" / ...
        message: 游戏自己的结算文本（可选）
        """

    # ── 插件适配接口（可选覆写，默认从 data/config 加载） ─────

    def get_keywords(self) -> List[str]:
        """返回触发关键词列表，用于插件匹配用户输入。
        默认使用游戏名和 id，可在 data/config/{id}/keywords.json 中配置。"""
        if hasattr(self, '_keywords') and self._keywords:
            return self._keywords
        return [self.name, self.id]

    def get_emotion_templates(self) -> Dict[str, List[str]]:
        """返回情感模板字典，键为 outcome/fact kind，值为模板句子列表。
        默认从 data/config/{id}/emotion.json 加载。"""
        if hasattr(self, '_emotion_templates') and self._emotion_templates:
            return self._emotion_templates
        return {}

    def classify_event(self, outcome: str, facts: List[Dict[str, Any]]) -> str:
        """返回事件分类：'highlight' / 'lowlight' / 'routine'。
        默认从 outcome 和 facts 中推断。"""
        oc = (outcome or "").lower()
        if "legendary" in oc or "highlight" in oc or "big_win" in oc:
            return "highlight"
        if "lose" in oc or "lowlight" in oc or "air" in oc:
            return "lowlight"
        for f in facts:
            r = str(f.get("rarity", "")).lower()
            if r in ("legendary", "？"):
                return "highlight"
            if r in ("epic", "rare"):
                return "highlight"
        return "routine"

    async def on_milestone(self, outcome: str, facts: List[Dict[str, Any]],
                           memory: Any) -> None:
        """处理里程碑事件。默认处理稀有度相关里程碑。"""
        for f in facts:
            if str(f.get("rarity", "")).lower() in ("legendary", "？"):
                await memory.bump_stat(self.id, "legendary_caught")

    def format_fact_for_card(self, fact: Dict[str, Any]) -> Tuple[str, str]:
        """格式化事实用于卡片显示，返回 (显示文本, 稀有度标签)。"""
        name = fact.get("name", "") or fact.get("item", "") or str(fact.get("kind", ""))
        rarity = fact.get("rarity", "")
        if fact.get("size"):
            detail = f"{name}（{rarity}）{fact['size']}cm {fact.get('weight', '')}kg"
        else:
            detail = name
        return detail, rarity

    def wants_card(self, outcome: str, facts: List[Dict[str, Any]]) -> bool:
        """高亮事件是否要生成卡片。默认有 facts 时生成。"""
        return bool(facts)

    # ── 状态（可选覆写） ─────────────────────

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        """面板轮询的游戏状态。"""

    def get_meta(self) -> Dict[str, Any]:
        """游戏卡片信息。"""
        return {"id": self.id, "name": self.name,
                "description": self.description, "version": self.version,
                "icon": self.icon, "enabled": self.enabled}

    def support_panel(self) -> Optional[Dict[str, Any]]:
        """面板配置 schema（可选覆写）。返回 None 表示该游戏无配置面板。

        参考锅巴(Guoba)的 schema 风格，声明式描述 config.json 里的字段：
            {
              "schemas": [
                {"label": "超时设置", "component": "Group"},
                {"field": "game_timeout", "label": "超时秒数", "component": "InputNumber",
                 "props": {"min": 60, "max": 3600}, "help": "多久不猜就自动揭晓"},
                {"field": "puzzle_source", "label": "题目来源", "component": "Select",
                 "props": {"options": ["mix", "llm", "local"]}},
                {"field": "enable_x", "label": "开关项", "component": "Switch"},
              ]
            }

        组件类型：Switch / Input / InputNumber / Select / InputTextArea / Group。
        field 对应 config.json 的键（点路径 a.b 表示嵌套）；props 透传给前端控件。
        """
        return None

    # ── 存档工具 ─────────────────────────────

    async def get_user_data(self, user_id: str, default: Any = None) -> Any:
        """读取玩家存档。"""
        return await self.plugin.store_get_user(self.id, user_id, default)

    async def save_user_data(self, user_id: str, data: Any) -> None:
        """写入玩家存档。"""
        await self.plugin.store_save_user(self.id, user_id, data)


def build_fact(kind: str, **fields: Any) -> Dict[str, Any]:
    """构造一条结构化事实。"""
    fact: Dict[str, Any] = {"kind": kind}
    fact.update(fields)
    return fact
