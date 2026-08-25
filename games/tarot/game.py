"""塔罗牌 —— 猫娘陪你占卜。

玩法:
- 「占卜」: 随机选一个牌阵, 抽牌占卜(每张正/逆位 + 解读 + 牌面图)
- 「塔罗牌」: 随机抽一张单牌
- 「塔罗牌 恋人」: 指定牌名的单牌(支持中文名/英文名/数字编号)
- 「塔罗主题」: 切换主题(BilibiliTarot / TouhouTarot)

数据: games/tarot/tarot.json(完整韦特塔罗 78 张 + 9 牌阵)。
图片在 static/img/tarot/<主题>/<Type>/<pic>.png|jpg,
宿主静态服务直接可访问, 游戏返回 images(URL)由 brain 统一推送。

「游戏适配插件」: handle_action 只返回结构化结果(facts/outcome/message/images),
不直接 push; 猫娘交互由 brain 情感渲染 + 宿主 LLM 自然回应。
"""

from __future__ import annotations

import json
import os
import random
from typing import Any, Dict, List, Optional

from ...core.contracts import GameAdapter, build_fact

game_class = "TarotGame"

# 主题目录名(data/ 下, 简洁命名)
THEMES = ["bili", "touhou", "ba"]
_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tarot.json")
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 主题 → 可用的牌类型(主题资源决定)
_THEME_TYPES: Dict[str, List[str]] = {
    "bili": ["MajorArcana", "Cups", "Pentacles", "Swords", "Wands"],
    "touhou": ["MajorArcana"],
    "ba": ["MajorArcana"],
}
_THEME_LABELS: Dict[str, str] = {
    "bili": "B站幻星集", "touhou": "东方", "ba": "碧蓝档案",
}


class TarotGame(GameAdapter):
    id = "tarot"
    name = "塔罗牌"
    description = "猫娘陪你抽塔罗牌占卜, 完整韦特塔罗 78 张 + 多种牌阵"
    icon = "🔮"
    version = "0.1.0"

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._data: Optional[Dict[str, Any]] = None
        self._theme: str = "bili"

    # ── 数据加载 ──────────────────────────

    def _load_data(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data
        try:
            with open(_JSON_PATH, encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._data = {"cards": {}, "formations": {}}
        return self._data

    def _card_pool(self) -> Dict[str, Dict[str, Any]]:
        """按当前主题返回可抽的牌池(排除 Extra 王牌)。"""
        data = self._load_data()
        types = _THEME_TYPES.get(self._theme, ["MajorArcana"])
        return {k: v for k, v in data.get("cards", {}).items()
                if v.get("type") in types}

    # ── 指令路由 ──────────────────────────

    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        c = (cmd or "").strip()

        # 切换主题
        if "主题" in c:
            return await self._switch_theme(c)

        # 指定牌: 「塔罗牌 X」(X 为牌名/编号)
        if c.startswith("塔罗牌") and c != "塔罗牌":
            rest = c.replace("塔罗牌", "", 1).strip()
            if rest:
                return await self._single_card(rest)

        # 单张
        if c in ("塔罗牌", "单张", "抽牌", "来一张"):
            return await self._single_card("")

        # 占卜(牌阵)
        if any(k in c for k in ("占卜", "牌阵", "塔罗占卜")):
            return await self._divine()

        # 空指令/帮助
        if not c or c in ("帮助", "玩法"):
            return {"facts": [build_fact("help")], "outcome": "help",
                    "message": "🔮 塔罗占卜: 说「占卜」抽牌阵, 「塔罗牌」抽单张, "
                               "「塔罗牌 恋人」指定牌。猫娘给你解牌喵~"}

        return {"facts": [], "outcome": "unknown", "message": ""}

    # ── 单张牌 ────────────────────────────

    async def _single_card(self, query: str) -> Dict[str, Any]:
        pool = self._card_pool()
        if not pool:
            return {"facts": [build_fact("error")], "outcome": "error",
                    "message": "塔罗牌数据还没准备好喵, 稍后再试~"}

        # 指定牌: 按编号/中文名/英文名匹配
        card_key = None
        if query:
            q = query.strip().lower()
            for k, v in pool.items():
                if (k == query or v.get("name_cn", "").lower() == q
                        or v.get("name_en", "").lower() == q
                        or str(k).lower() == q):
                    card_key = k
                    break
            if card_key is None:
                names = "、".join(v.get("name_cn", "") for v in list(pool.values())[:10])
                return {"facts": [build_fact("not_found")], "outcome": "not_found",
                        "message": f"没找到「{query}」喵~ 试试: {names} 等(或「塔罗牌」随机一张)"}
        else:
            card_key = random.choice(list(pool.keys()))

        card = pool[card_key]
        up = random.random() < 0.5
        meaning = card["meaning"]["up"] if up else card["meaning"]["down"]
        name_cn = card.get("name_cn", card_key)
        image = self._card_image(card)

        lines = [f"🔮 {name_cn}「{'正位' if up else '逆位'}」"]
        lines.append(f"含义: {meaning}")
        if not up:
            lines.append("(牌面已倒转~)")
        msg = "\n".join(lines)

        images = [image] if image else []
        return {"facts": [build_fact("tarot", card=name_cn, position="up" if up else "down")],
                "outcome": "tarot", "message": msg, "images": images}

    # ── 牌阵占卜 ──────────────────────────

    async def _divine(self) -> Dict[str, Any]:
        data = self._load_data()
        formations = data.get("formations", {})
        if not formations:
            return {"facts": [build_fact("error")], "outcome": "error",
                    "message": "牌阵数据还没准备好喵, 稍后再试~"}
        pool = self._card_pool()

        fname = random.choice(list(formations.keys()))
        formation = formations[fname]
        cards_num = int(formation.get("cards_num", 3))
        is_cut = bool(formation.get("is_cut", False))
        reps = random.choice(formation.get("representations") or [["过去", "现在", "未来"]])

        # 抽牌(可重复, 塔罗允许同一张多次出现; 但主题资源有限时不重复更好)
        keys = list(pool.keys())
        if len(keys) >= cards_num:
            chosen = random.sample(keys, cards_num)
        else:
            chosen = [random.choice(keys) for _ in range(cards_num)]

        lines = [f"🔮 牌阵: {fname}"]
        images = []
        facts = []
        for i, key in enumerate(chosen):
            card = pool[key]
            up = random.random() < 0.5
            meaning = card["meaning"]["up"] if up else card["meaning"]["down"]
            name_cn = card.get("name_cn", key)
            rep = reps[i] if i < len(reps) else f"位置{i+1}"
            if is_cut and i == cards_num - 1:
                rep = f"切牌「{rep}」"
            lines.append(f"\n{rep}: {name_cn}「{'正位' if up else '逆位'}」")
            lines.append(f"   {meaning}")
            img = self._card_image(card)
            if img:
                images.append({**img, "text": f"{rep}: {name_cn}"})
            facts.append(build_fact("tarot", card=name_cn, position="up" if up else "down",
                                    position_name=rep))

        return {"facts": facts, "outcome": "divine",
                "message": "\n".join(lines), "images": images}

    # ── 主题切换 ──────────────────────────

    async def _switch_theme(self, c: str) -> Dict[str, Any]:
        for theme in THEMES:
            label = _THEME_LABELS.get(theme, theme)
            if theme.lower() in c.lower() or label in c:
                self._theme = theme
                return {"facts": [build_fact("theme", theme=theme)], "outcome": "theme",
                        "message": f"已切换塔罗牌主题: {label} 喵~"}
        names = "、".join(_THEME_LABELS.get(t, t) for t in THEMES)
        return {"facts": [build_fact("theme_help")], "outcome": "theme_help",
                "message": f"当前主题: {_THEME_LABELS.get(self._theme, self._theme)}。"
                           f"说「塔罗主题 {names}」切换喵~"}

    # ── 图片 ──────────────────────────────

    def _data_root(self):
        """游戏 data 目录(测试可注入 _local_scan_dir)。"""
        override = getattr(self, "_local_scan_dir", None)
        if override:
            return override
        return _DATA_DIR

    def _card_image(self, card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从游戏 data 目录读牌面图 bytes, 返回 images 元素(交 brain 推送)。

        文件路径: data/<主题>/<Type>/<pic>.<png|jpg>。pic 字段与文件名匹配
        (如大阿卡纳 0-愚者, 小阿卡纳 圣杯-01)。
        """
        ttype = card.get("type", "MajorArcana")
        pic = card.get("pic", "")
        if not pic:
            return None
        base = os.path.join(self._data_root(), self._theme, ttype)
        for cand in (".png", ".jpg", ".jpeg"):
            p = os.path.join(base, pic + cand)
            if os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        raw = f.read()
                    if raw:
                        return {"bytes": raw,
                                "mime": "image/png" if cand == ".png" else "image/jpeg",
                                "text": card.get("name_cn", "")}
                except OSError:
                    return None
        return None

    # ── 状态 / 面板 ───────────────────────

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        data = self._load_data()
        return {"theme": _THEME_LABELS.get(self._theme, self._theme),
                "cards": len(data.get("cards", {})),
                "formations": len(data.get("formations", {}))}

    def support_panel(self) -> Optional[Dict[str, Any]]:
        return {"schemas": [
            {"label": "主题", "component": "Group"},
            {"field": "theme", "label": "默认主题", "component": "Select",
             "props": {"options": [{"label": _THEME_LABELS.get(t, t), "value": t}
                                   for t in THEMES]},
             "help": "占卜使用的牌面主题"},
        ]}

    def classify_event(self, outcome: str, facts: List[Dict[str, Any]]) -> str:
        # 塔罗结果无好坏, 一律 routine(猫娘陪伴式解读)
        return "routine"

    def wants_card(self, outcome: str, facts: List[Dict[str, Any]]) -> bool:
        return False  # 牌面图已随 images 返回
