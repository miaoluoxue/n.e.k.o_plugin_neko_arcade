"""单文件小游戏示例：猜硬币。整个游戏只有一个文件。"""

import random
import re
import time
from typing import Optional

from ...core.contracts import GameAdapter, build_fact

game_class = "CoinFlipGame"

_HEADS = ("正", "正面")
_TAILS = ("反", "反面")
_PREFIXES = ("猜硬币", "抛硬币", "硬币", "我猜", "猜")
_TRAILING_PARTICLES = "吧呀啊呢喵"


def _stats(data: dict) -> dict:
    """返回可写的 stats 槽；兼容旧存档缺字段或字段类型损坏。"""
    stats = data.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        data["stats"] = stats
    for key in ("wins", "plays"):
        try:
            stats[key] = max(0, int(stats.get(key, 0) or 0))
        except (TypeError, ValueError):
            stats[key] = 0
    return stats


def parse_pick(text: str) -> Optional[str]:
    """从输入里认出「正/反」；认不出返回 None。

    旧实现是 `"正" in c else "反"` —— 随便打一句话都会被当成「反」并白扣一局，
    这里改成**只认明确的正/反**，认不出就再问一次（不消耗次数）。
    """
    raw = (text or "").strip().lower()
    if not raw:
        return None

    # 英文只接受完整词，避免 "heads-up" / "ahead" 被当成 heads。
    words = re.findall(r"[a-z]+", raw)
    if len(words) == 1 and words[0] in ("heads", "head"):
        return "正"
    if len(words) == 1 and words[0] in ("tails", "tail"):
        return "反"

    # 中文去掉标点、已知动作前缀和句尾语气词后，必须精确命中正/反。
    clean = re.sub(r"[\s，,。.!！?？:：;；]+", "", raw)
    for prefix in _PREFIXES:
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    clean = clean.rstrip(_TRAILING_PARTICLES)
    if clean in _HEADS:
        return "正"
    if clean in _TAILS:
        return "反"
    return None


class CoinFlipGame(GameAdapter):
    id = "coinflip"
    name = "猜硬币"
    description = "和猫娘玩猜硬币正反面"
    icon = "🪙"

    #: 猜完一局就结束会话（一局定胜负的玩法，不该一直挂在游戏模式里）。
    #: need_pick / limit 不算整局结束，所以不列。
    end_session_outcomes = ("win", "lose")

    async def handle_action(self, user_id, cmd, args=None):
        c = (cmd or "").strip()
        # 只说游戏名没给正反 → 提示
        if not c or c in ("猜硬币", "猜", "硬币"):
            return {"facts": [], "outcome": "done",
                    "message": "说「猜硬币 正」或「猜硬币 反」喵"}

        # 先认出正/反：认不出就再问一次，**不扣次数、不算对局**
        pick = parse_pick(c)
        if pick is None:
            return {"facts": [], "outcome": "need_pick",
                    "message": "要猜正面还是反面喵？说「猜硬币 正」或「猜硬币 反」~"}

        data = await self.get_user_data(user_id, {"stats": {"wins": 0, "plays": 0}})
        if not isinstance(data, dict):
            data = {}
        stats = _stats(data)
        # 每日次数限制（读 config.json，缺省 20）
        max_plays = int((getattr(self, "_config", None) or {}).get("max_plays_per_day", 20) or 20)
        today = time.strftime("%Y-%m-%d")
        if data.get("day") != today:
            data["day"] = today
            data["plays_today"] = 0
        if data.get("plays_today", 0) >= max_plays:
            return {"facts": [], "outcome": "limit",
                    "message": f"今天已经玩够 {max_plays} 局啦，明天再来喵~"}
        data["plays_today"] = data.get("plays_today", 0) + 1

        result = random.choice(["正", "反"])
        win = pick == result
        fact = build_fact("win" if win else "lose", pick=pick, result=result)
        outcome = "win" if win else "lose"

        # 持久化统计数据
        stats["plays"] += 1
        if win:
            stats["wins"] += 1
        await self.save_user_data(user_id, data)

        return {"facts": [fact],
                "outcome": outcome,
                "message": f"硬币落下…是{result}！你猜{pick}，" + ("赢啦！" if win else "输啦…")}

    async def get_status(self, user_id="default"):
        data = await self.get_user_data(user_id, {}) or {}
        if not isinstance(data, dict):
            data = {}
        stats = _stats(data)
        return {"wins": stats.get("wins", 0), "plays": stats.get("plays", 0)}

    def support_panel(self):
        return {"schemas": [
            {"field": "max_plays_per_day", "label": "每日可玩次数", "component": "InputNumber",
             "props": {"min": 1, "max": 1000}, "help": "每天最多能玩几局猜硬币"},
        ]}
