"""猫娘人格：性格、心情、说话风格。"""

from __future__ import annotations

import random
from typing import Any, Dict, Optional

EMOTION_POOL = ("excitement", "curiosity", "proud", "upset", "sleepy")
DECAY_RATES = {"excitement": 0.06, "curiosity": 0.05, "proud": 0.07, "upset": 0.04, "sleepy": 0.02}


class MoodArc:
    """情绪弧线：事件触发→峰值→衰减→残留。"""

    def __init__(self, name: str, decay_rate: float = 0.05) -> None:
        self.name = name
        self.value = 0.0
        self.peak = 0.0
        self.decay_rate = decay_rate
        self.residual = 0.0

    def trigger(self, intensity: float) -> None:
        self.value = min(1.0, max(self.value, intensity))
        self.peak = max(self.peak, self.value)
        self.residual = max(self.residual, intensity * 0.12)

    def decay(self) -> None:
        if self.value > self.residual:
            self.value -= self.decay_rate * (self.value - self.residual)
            self.value = max(self.residual, self.value)


class Mood:
    """五根情绪弧线。"""

    def __init__(self) -> None:
        self.arcs = {k: MoodArc(k, DECAY_RATES[k]) for k in EMOTION_POOL}

    def trigger(self, emotion: str, intensity: float = 0.5) -> None:
        if emotion in self.arcs:
            self.arcs[emotion].trigger(min(intensity, 1.0))

    def decay_all(self) -> None:
        for a in self.arcs.values():
            a.decay()

    def primary(self) -> str:
        best = max(self.arcs.values(), key=lambda a: a.value)
        return best.name if best.value > 0.15 else "calm"

    def style(self) -> Dict[str, Any]:
        best = max(self.arcs.values(), key=lambda a: a.value)
        v = best.value
        if best.name == "excitement" and v > 0.5:
            return {"energy": "high", "verbosity": "多话", "exclaim": 3, "pace": "快"}
        if best.name == "proud" and v > 0.5:
            return {"energy": "high", "verbosity": "炫耀", "exclaim": 2, "pace": "中"}
        if best.name == "upset" and v > 0.5:
            return {"energy": "low", "verbosity": "委屈", "exclaim": 1, "pace": "慢"}
        if best.name == "sleepy" and v > 0.4:
            return {"energy": "low", "verbosity": "极简", "exclaim": 0, "pace": "极慢"}
        if v > 0.2:
            return {"energy": "medium", "verbosity": "正常", "exclaim": 1, "pace": "中"}
        return {"energy": "calm", "verbosity": "简洁", "exclaim": 0, "pace": "中"}

    def urge_bonus(self) -> float:
        bonus = 1.0 + self.arcs["excitement"].value * 0.35 + self.arcs["curiosity"].value * 0.25
        bonus -= self.arcs["upset"].value * 0.2 + self.arcs["sleepy"].value * 0.3
        return max(0.4, bonus)

    def snapshot(self) -> Dict[str, float]:
        return {k: round(v.value, 2) for k, v in self.arcs.items()}


class Persona:
    """猫娘人格本体。"""

    def __init__(self, host_persona: Optional[Dict[str, Any]] = None) -> None:
        self.name = (host_persona or {}).get("name", "喵喵")
        self.traits = (host_persona or {}).get("traits", [])
        self.user_call = (host_persona or {}).get("user_call", "主人")
        self.mood = Mood()
        self._rng = random.Random()

    def feel(self, emotion: str, intensity: float = 0.5) -> None:
        self.mood.trigger(emotion, intensity)

    def on_event(self, kind: str) -> None:
        if kind == "highlight":
            self.mood.trigger("excitement", 0.85)
            self.mood.trigger("proud", 0.6)
        elif kind == "lowlight":
            self.mood.trigger("upset", 0.7)
        elif kind == "nice":
            self.mood.trigger("excitement", 0.35)
            self.mood.trigger("curiosity", 0.25)
        elif kind == "boring":
            self.mood.trigger("sleepy", 0.3)
        elif kind == "chat" or kind == "invite":
            self.mood.trigger("curiosity", 0.2)
            self.mood.trigger("excitement", 0.5)

    def polish(self, text: str, style: Optional[Dict[str, Any]] = None) -> str:
        """拟人化修饰：结巴、语气词。"""
        s = style or self.mood.style()
        if self._rng.random() < 0.06 + (0.08 if s.get("energy") == "high" else 0.0):
            if len(text) > 2:
                i = self._rng.randint(0, 1)
                text = text[:i] + text[i] + "、" + text[i:]
        if self._rng.random() < 0.35 and not text.endswith(("！", "?", "？")):
            if not text.endswith(("喵", "呢", "哦", "啦", "呀")):
                text += self._rng.choice(["喵", "呢", "啦", "呀"])
        return text

    def snapshot(self) -> Dict[str, Any]:
        s = self.mood.style()
        return {"mood": self.mood.primary(), "emotions": self.mood.snapshot(),
                "style": s, "name": self.name, "user_call": self.user_call}