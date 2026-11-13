"""情感渲染：游戏事实→猫娘的话（三级渲染：模板/LLM/模板兜底）。"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple


class EmotionRenderer:
    """三级渲染器。使用游戏提供的情感模板。"""

    FALLBACK_TEMPLATES: Dict[str, List[str]] = {
        "win": ["赢啦赢啦！是我赢的喵！", "嘿嘿，赢下这一局"],
        "lose": ["唔…输了…但下次赢回来喵", "输了…别难过"],
        "start": ["好呀好呀！开始喵！", "来啦来啦"],
        "stop": ["今天先玩到这喵，改天再来", "下次继续"],
    }

    def __init__(self, llm_provider, llm_throttle) -> None:
        self._llm = llm_provider
        self._rng = random.Random()

    async def render(self, game_name: str, outcome: str,
                     facts: List[Dict[str, Any]],
                     style: Dict[str, Any],
                     game_templates: Optional[Dict[str, List[str]]] = None) -> Tuple[str, str]:
        """返回 (猫娘的话, 级别: highlight/lowlight/routine)。
        game_templates 为游戏提供的情感模板，优先使用。"""
        level = self._classify(outcome, facts)
        if level == "highlight":
            text = await self._llm_render(game_name, outcome, facts, "high", style)
            if text:
                return text, "highlight"
        elif level == "lowlight":
            text = await self._llm_render(game_name, outcome, facts, "low", style)
            if text:
                return text, "lowlight"
        text = self._template_render(facts, outcome, game_templates)
        return text, level

    def _classify(self, outcome: str, facts: List[Dict]) -> str:
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

    # fact 的稀有度 → 模板后缀(游戏在自己的 emotion.json 里写 <kind>_rare / <kind>_common)
    _RARE_VALUES = {"legendary", "epic", "rare", "highlight", "？", "ur", "ssr"}
    _FACT_FALLBACK_KEYS = ("fact", "event")

    def _fact_text(self, fact: Dict, templates: Dict[str, List[str]]) -> str:
        """把一条 fact 渲染成一句话（**完全不认识具体是哪个游戏**）。

        解析顺序（游戏只需按约定在 emotion.json 里写键）：
          ① ``<kind>_rare`` / ``<kind>_common`` —— 按 fact.rarity 选稀有档
          ② ``<kind>``                          —— 不分档的统一说法
          ③ ``fact`` / ``event``                —— 任意 fact 的通用说法
          ④ fact 自带 ``text`` 字段             —— 游戏自己渲染好的整句
        都没有就返回空串(交给上层按 outcome 兜底), 不再猜"这是钓鱼/这是塔罗"。
        """
        kind = str(fact.get("kind") or "").strip()
        rarity = str(fact.get("rarity") or "").strip().lower()
        group = "rare" if rarity in self._RARE_VALUES else "common"
        keys = []
        if kind:
            keys.extend((f"{kind}_{group}", kind))
        keys.extend(self._FACT_FALLBACK_KEYS)
        for key in keys:
            if key in templates:
                return self._fill(self._pick(templates, key), fact)
        return self._fill(str(fact.get("text") or ""), fact, quiet=True)

    @staticmethod
    def _fill(line: str, fact: Dict, *, quiet: bool = False) -> str:
        """用 fact 的字段填模板占位符; 缺字段就原样返回(不炸)。"""
        if not line:
            return ""
        table = {k: v for k, v in fact.items() if isinstance(v, (str, int, float))}
        for key in ("name", "item", "size", "value", "card", "position"):
            table.setdefault(key, "")
        try:
            return str(line).format(**table)
        except Exception:
            return "" if quiet else str(line)

    def _template_render(self, facts: List[Dict], outcome: str,
                         game_templates: Optional[Dict[str, List[str]]] = None) -> str:
        """使用游戏模板渲染；无匹配时使用兜底模板。"""
        templates = game_templates or {}
        lines = [text for text in (self._fact_text(f, templates) for f in facts
                                   if isinstance(f, dict)) if text]

        if not lines:
            oc = (outcome or "").lower()
            if "win" in oc:
                lines.append(self._pick(self.FALLBACK_TEMPLATES, "win"))
            elif "lose" in oc:
                lines.append(self._pick(self.FALLBACK_TEMPLATES, "lose"))
            else:
                lines.append("嗯…就这些啦喵")
        return " ".join(lines)

    async def _llm_render(self, game_name: str, outcome: str,
                          facts: List[Dict], tone: str,
                          style: Dict[str, Any]) -> Optional[str]:
        fact_lines = "\n".join(f"- {f.get('kind')}: {f}" for f in facts[:5])
        mood_desc = "兴奋激动" if tone == "high" else "委屈但不服输"
        prompt = (
            f"你是一只叫「{style.get('name', '喵喵')}」的猫娘，和主人一起玩「{game_name}」。"
            f"结果：{outcome}。事实：{fact_lines}。"
            f"请用{mood_desc}的口吻说 1 句话（25 字内），带猫娘语气词。"
        )
        text = await self._call_llm(prompt)
        if text:
            return text.strip('"').strip()
        return None

    async def _call_llm(self, prompt: str) -> Optional[str]:
        return await self._llm.call(prompt) if self._llm else None

    def _pick(self, templates: Dict[str, List[str]], key: str) -> str:
        pool = templates.get(key, self.FALLBACK_TEMPLATES.get(key, ["唔喵"]))
        return self._rng.choice(pool)