"""移植自 nonebot-plugin-remake 的 utils.py, 去掉 nonebot 依赖(换标准 logging)。"""

import logging
import re

logger = logging.getLogger("neko_arcade.remake")


class DummyList(list):
    def __init__(self, lst: list[int]):
        super().__init__(lst)

    def __contains__(self, obj: object) -> bool:
        if isinstance(obj, set):
            for x in self:
                if x in obj:
                    return True
            return False
        return super().__contains__(obj)


def parse_condition(cond: str):
    reg_attr = re.compile("[A-Z]{3}")
    cond2 = (
        reg_attr.sub(
            lambda m: f'getattr(x, "{m.group()}")', cond.replace("AEVT", "AVT")
        )
        .replace("?[", " in DummyList([")
        .replace("![", "not in DummyList([")
        .replace("]", "])")
        .replace("|", " or ")
    )
    while True:
        try:
            func = eval(f"lambda x: {cond2}")
            func.__doc__ = cond2
            return func
        except Exception:
            logger.warning("[WARNING] missing ) in %s", cond)
            cond2 += ")"
