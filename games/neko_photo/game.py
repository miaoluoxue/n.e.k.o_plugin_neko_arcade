"""喵图相册 —— 猫娘聊途中会随机发图的游戏。

玩法：
- 「发图 / 来张图 / 照片 / 自拍 / 喵图」→ 猫娘随机发一张图
- 「喵图 <分类>」→ 只从指定分类里随机发(如「喵图 可爱」「发张日常的图」)
- 「图鉴 / 相册」→ 查看已收集的猫娘照片
- 「图库 / 分类」→ 查看图库里有哪些分类
- 后台自动发图(background_tick): 无游戏会话时, 猫娘聊天中也会自动随机发图

图片能力全部走插件主体 PhotoBridge(adapters/photo_bridge.py):
- 图库扫描 / 分类管理 / 挑图 / 推送 / 用户上传 由桥接统一处理
- 本游戏只负责玩法(指令路由 / 图鉴 / 每日上限)与后台发图节流
- 任何新游戏想发图, 直接 await self.send_photo(...) 走同一个桥接
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Tuple

from ...core.contracts import GameAdapter, build_fact

game_class = "NekoPhotoGame"

# 动态渲染心情标签(与 PhotoBridge/ImageRenderer 一致)
MOOD_LABELS: Dict[str, str] = {
    "excitement": "兴奋", "curiosity": "好奇", "proud": "得意",
    "upset": "委屈", "sleepy": "犯困", "calm": "淡定",
}


class NekoPhotoGame(GameAdapter):
    id = "neko_photo"
    name = "喵图相册"
    description = "猫娘聊途中会随机发图给你看, 支持分类图库和上传收集"
    icon = "📸"
    version = "0.3.0"
    # 后台自动发图: 无游戏会话时 brain.tick 也调用 on_tick,
    # 让猫娘在任何聊天过程中都自动随机发图(无需用户命令)
    background_tick = True

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._next_auto_ts = 0.0

    # ── 指令路由 ──────────────────────────

    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        c = (cmd or "").strip()

        # 停止 / 退出(会话交给大脑 stop_game 结束, 这里提示)
        if any(k in c for k in ("停止", "退出", "不看了", "关掉")):
            return {"facts": [build_fact("stop")], "outcome": "stop",
                    "message": "相册先收起来啦, 想看了再喊我喵~"}

        # 发图: 发图 / 来张图 / 照片 / 自拍 / 喵图 / 图片 / 来一张 / 看看
        # 支持分类: 「喵图 可爱」「发张 日常 的图」(发图优先于分类查看)
        if any(k in c for k in ("发图", "来张", "来一张", "照片", "自拍",
                                "喵图", "图片", "看看", "给我看", "晒")):
            category = self._extract_category(c)
            return await self._send_photo(user_id, category=category)

        # 图库 / 分类列表
        if any(k in c for k in ("图库", "分类", "有哪些图")):
            return await self._categories(user_id)

        # 图鉴 / 相册
        if any(k in c for k in ("图鉴", "相册", "收集", "图册")):
            return await self._album(user_id)

        # 空指令/只想玩 → 主动发一张
        if not c or c in (self.name, self.id):
            return await self._send_photo(user_id)

        return {"facts": [], "outcome": "unknown", "message": ""}

    def _extract_category(self, cmd: str) -> Optional[str]:
        """从指令里提取分类名。例: 「喵图 可爱」→ 可爱; 「发张日常的图」→ 日常。

        两步策略:
        1. 优先: 在原始指令里直接找已有分类名(不剥词, 最长匹配)——自创文件夹
           无论叫什么名字都能命中, 不会被"图/照片"等词破坏。
        2. 回退: 若指令是「喵图 X / 发张X的图」这类明确带分类意图的形式, 且 X
           不是已有分类, 把 X 原样返回(由上层提示该分类无图)。
        """
        cats = self.photo_categories()
        # 1. 直接匹配已有分类(原始指令, 不剥词)
        for cat in sorted(cats, key=len, reverse=True):
            if cat and cat in cmd:
                return cat
        # 2. 明确带分类意图 → 剥掉指令前缀词, 剩余文本当分类名
        stripped = cmd
        for kw in ("发一张", "发张", "来一张", "来张", "喵图", "发图", "看看",
                   "照片", "自拍", "图片", "给我看", "晒", "的图", "图"):
            stripped = stripped.replace(kw, " ")
        stripped = " ".join(stripped.split())
        if stripped and any(k in cmd for k in ("喵图", "发张", "发一张", "来张", "来一张")):
            return stripped
        return None

    # ── 发图(走桥接) ──────────────────────

    async def _send_photo(self, user_id: str,
                          category: Optional[str] = None) -> Dict[str, Any]:
        data = await self._load_data(user_id)
        max_photos = int(self._cfg("max_photos_per_day", 50) or 50)
        today = time.strftime("%Y-%m-%d")
        if data.get("day") != today:
            data["day"] = today
            data["today_count"] = 0
        if data.get("today_count", 0) >= max_photos:
            return {"facts": [build_fact("limit")], "outcome": "limit",
                    "message": f"今天已经发过 {max_photos} 张图啦, 明天再来看喵~"}

        # 走插件主体 PhotoBridge: 挑图 + 推送统一由桥接完成
        result = await self.send_photo(user_id, category=category)
        if not result.get("ok"):
            if category:
                return {"facts": [build_fact("error")], "outcome": "error",
                        "message": f"分类「{category}」里还没有图喵, 说「图库」看看有哪些分类~"}
            return {"facts": [build_fact("error")], "outcome": "error",
                    "message": "呜……图片都跑丢了, 稍后再试试喵"}

        # 图鉴记录(游戏自己的玩法)
        data["today_count"] = data.get("today_count", 0) + 1
        data["stats"]["total"] = data["stats"].get("total", 0) + 1
        style = result.get("style", "unknown")
        rarity = result.get("rarity", "common")
        collected = data["collection"]
        is_new = style not in collected
        collected[style] = {
            "count": collected.get(style, {}).get("count", 0) + 1,
            "rarity": rarity,
            "first_ts": collected.get(style, {}).get("first_ts", time.time()),
        }
        if rarity == "rare":
            data["stats"]["rare"] = data["stats"].get("rare", 0) + 1
        await self.save_user_data(user_id, data)

        # 稀有款 → 高光事件(brain 出卡片 + LLM 兴奋回应)
        if rarity == "rare":
            outcome = "rare"
        elif is_new:
            outcome = "collect_new"
        else:
            outcome = "photo"
        facts = [build_fact("photo", style=style, rarity=rarity, new=is_new)]

        label = MOOD_LABELS.get(style, style)
        extra = ""
        if is_new:
            extra = f"(新图鉴: {label})"
        # 图片+配文已由桥接推送, 标记 pushed 防止 brain 重复推送 message
        return {"facts": facts, "outcome": outcome,
                "message": f"发了一张 {label} 的照片给你喵{extra}", "pushed": True}

    async def send_random_photo(self, user_id: str,
                                caption: str = "") -> Dict[str, Any]:
        """LLM 工具入口: 猫娘聊天中自主随机发一张图(走桥接)。

        不检查每日上限(工具调用频率天然低), 返回给 LLM 的简短描述。
        """
        result = await self.send_photo(user_id, caption=caption)
        if not result.get("ok"):
            return {"ok": False, "summary": result.get("summary", "发图失败喵")}
        # 计入图鉴(与主动发图一致)
        data = await self._load_data(user_id)
        style = result.get("style", "unknown")
        rarity = result.get("rarity", "common")
        collected = data["collection"]
        collected[style] = {
            "count": collected.get(style, {}).get("count", 0) + 1,
            "rarity": rarity,
            "first_ts": collected.get(style, {}).get("first_ts", time.time()),
        }
        data["stats"]["total"] = data["stats"].get("total", 0) + 1
        if rarity == "rare":
            data["stats"]["rare"] = data["stats"].get("rare", 0) + 1
        await self.save_user_data(user_id, data)
        return {"ok": True, "summary": result.get("summary", ""),
                "style": style, "rarity": rarity,
                "category": result.get("category", "")}

    # ── 用户上传图片(走桥接) ──────────────

    async def upload_photo(self, user_id: str, name: str,
                           data_b64: str = "", data_bytes: bytes = b"",
                           category: str = "默认") -> Dict[str, Any]:
        """保存用户上传的图片到图库指定分类(委托 PhotoBridge)。"""
        return await super().upload_photo(user_id, name=name, data_b64=data_b64,
                                          data_bytes=data_bytes, category=category)

    # ── 图鉴 / 分类 ───────────────────────

    async def _album(self, user_id: str) -> Dict[str, Any]:
        data = await self._load_data(user_id)
        collected = data.get("collection", {})
        stats = data.get("stats", {})
        if not collected:
            return {"facts": [build_fact("album", count=0)], "outcome": "album",
                    "message": "相册还是空的喵, 说「发图」让我拍一张给你~"}
        lines = [f"📸 喵图相册 · 共收集 {len(collected)} 款 · 看过 {stats.get('total', 0)} 张"]
        for style, info in collected.items():
            label = MOOD_LABELS.get(style, style)
            mark = "✨" if info.get("rarity") == "rare" else "·"
            lines.append(f"  {mark} {label} ×{info.get('count', 0)}")
        lines.append("说「发图」继续收集喵~")
        return {"facts": [build_fact("album", count=len(collected))],
                "outcome": "album", "message": "\n".join(lines)}

    async def _categories(self, user_id: str) -> Dict[str, Any]:
        """列出图库分类(走桥接)。"""
        cats = self.photo_categories()
        if not cats:
            return {"facts": [build_fact("categories", count=0)], "outcome": "categories",
                    "message": "图库还是空的喵, 去面板上传几张图吧~"}
        lines = ["📂 猫娘图库分类:"]
        imgs = self._photo.scan_images() if self._photo else []
        for cat in cats:
            n = len([i for i in imgs if i.get("category") == cat])
            lines.append(f"  · {cat} ({n} 张)")
        lines.append("说「喵图 分类名」就能只看这个分类的照片喵~")
        return {"facts": [build_fact("categories", count=len(cats))],
                "outcome": "categories", "message": "\n".join(lines)}

    # ── 后台自动发图(on_tick, 走桥接) ─────

    async def on_tick(self, user_id: str) -> None:
        """由 brain 每秒调用(含无会话的后台 tick), 每隔随机间隔自动发一张图。"""
        if not self._cfg("auto_send_enabled", True):
            return
        now = time.time()
        if now < self._next_auto_ts:
            return
        # 随机间隔后下一次(可配置): 默认 60~180 秒, 避免刷屏
        lo = int(self._cfg("auto_min_interval", 60) or 60)
        hi = int(self._cfg("auto_max_interval", 180) or 180)
        self._next_auto_ts = now + random.randint(max(10, lo), max(lo, hi))

        # 走桥接自动发图: 只发本地图库, 配文随机
        result = await self.send_auto_photo(user_id)
        if not result.get("ok"):
            return

        # 自动发图也计入图鉴(但不推送结算文本, 避免打扰)
        data = await self._load_data(user_id)
        style = result.get("style", "unknown")
        rarity = result.get("rarity", "common")
        collected = data["collection"]
        collected[style] = {
            "count": collected.get(style, {}).get("count", 0) + 1,
            "rarity": rarity,
            "first_ts": collected.get(style, {}).get("first_ts", time.time()),
        }
        data["stats"]["total"] = data["stats"].get("total", 0) + 1
        if rarity == "rare":
            data["stats"]["rare"] = data["stats"].get("rare", 0) + 1
        await self.save_user_data(user_id, data)

    # ── 状态 / 面板 / 事件 ─────────────────

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        data = await self._load_data(user_id)
        stats = data.get("stats", {})
        imgs = self._photo.scan_images() if self._photo else []
        return {"collected": len(data.get("collection", {})),
                "total": stats.get("total", 0),
                "rare": stats.get("rare", 0),
                "today": data.get("today_count", 0),
                "categories": self.photo_categories(),
                "image_count": len(imgs)}

    def classify_event(self, outcome: str, facts: List[Dict[str, Any]]) -> str:
        oc = (outcome or "").lower()
        if oc == "rare":
            return "highlight"
        if oc == "collect_new":
            return "highlight"
        if oc in ("limit", "error"):
            return "lowlight"
        return "routine"

    def wants_card(self, outcome: str, facts: List[Dict[str, Any]]) -> bool:
        return (outcome or "").lower() == "rare"

    def format_fact_for_card(self, fact: Dict[str, Any]) -> Tuple[str, str]:
        style = fact.get("style", "")
        label = MOOD_LABELS.get(style, style)
        rarity = fact.get("rarity", "")
        if fact.get("new"):
            return f"✨ 收集到新图鉴: {label}!", rarity
        return f"📸 {label} 的照片", rarity

    def support_panel(self) -> Optional[Dict[str, Any]]:
        return {"schemas": [
            {"label": "发图设置", "component": "Group"},
            {"field": "max_photos_per_day", "label": "每日发图上限", "component": "InputNumber",
             "props": {"min": 1, "max": 500}, "help": "每天最多主动发几张图"},
            {"label": "聊途中随机发图", "component": "Group"},
            {"field": "auto_send_enabled", "label": "启用随机发图", "component": "Switch",
             "help": "会话激活时猫娘每隔随机时间主动发图"},
            {"field": "auto_min_interval", "label": "最短间隔(秒)", "component": "InputNumber",
             "props": {"min": 10, "max": 600}, "help": "随机发图的最短间隔"},
            {"field": "auto_max_interval", "label": "最长间隔(秒)", "component": "InputNumber",
             "props": {"min": 10, "max": 600}, "help": "随机发图的最长间隔"},
        ]}

    # ── 工具 ──────────────────────────────

    def _cfg(self, key: str, default: Any = None) -> Any:
        return (getattr(self, "_config", None) or {}).get(key, default)

    def _new_data(self) -> Dict[str, Any]:
        return {"day": "", "today_count": 0,
                "collection": {}, "stats": {"total": 0, "rare": 0}}

    async def _load_data(self, user_id: str) -> Dict[str, Any]:
        data = await self.get_user_data(user_id)
        if not isinstance(data, dict) or not data:
            data = self._new_data()
            await self.save_user_data(user_id, data)
        data.setdefault("collection", {})
        data.setdefault("stats", {"total": 0, "rare": 0})
        return data
