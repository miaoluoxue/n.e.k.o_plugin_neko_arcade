# -*- coding: utf-8 -*-
"""帮助配置契约: 把游戏侧的 help.json 归一化成文档模型, 并做三层寻址。

层次(与插件统一帮助系统一致):

    游戏 → 分组(group) → 指令(command)

设计原则:
- **向后兼容**: 老的 ``{"text": ..., "commands": [["指令", "说明"], ...]}`` 完全照旧;
  ``groups`` 是新增的可选字段, 不写就还是"扁平单页帮助", 10 个既有游戏零改动。
- **游戏只给数据**: 分组名/别名/语义图标名/版式块/指令表 —— 样式全部由插件主题决定。
- **零配置兜底**: ``auto_groups: true`` 时按命名模式自动聚簇, 细分的游戏也能自动得到分组。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 帮助意图: 聊天里出现这些词时, 由插件统一拦截并渲染帮助(游戏无需实现)
HELP_INTENT_WORDS = ("帮助", "攻略", "玩法", "怎么玩", "说明", "指令表", "help")

# 分组关键词 → 语义图标名(零配置聚簇时的兜底映射)
_ICON_HINTS: Sequence[Tuple[Sequence[str], str]] = (
    (("纳戒", "背包", "装备", "道具", "物品"), "bag"),
    (("战斗", "打劫", "讨伐", "团本", "秘境", "比试", "打怪"), "sword"),
    (("宗门", "门派", "俸禄", "仓库", "药田"), "sect"),
    (("仙宠", "宠物", "灵宠"), "pet"),
    (("炼丹", "炼器", "采药", "采矿", "合成", "加工", "配方"), "furnace"),
    (("市集", "市场", "拍卖", "交易", "出售", "购买"), "coin"),
    (("修行", "修炼", "突破", "境界", "闭关", "炼体"), "meditate"),
    (("日常", "签到", "任务", "成就"), "daily"),
    (("猫娘", "道侣", "亲密", "师"), "heart"),
    (("诸天", "小世界", "位面"), "star"),
    (("魔", "神石", "堕"), "flame"),
)


def _norm(text: Any) -> str:
    """归一化用于匹配: 去空白/标点, 转小写。"""
    return re.sub(r"[\s\u3000·,，。.、:：!！?？()（）\[\]【】<>《》\"'`~-]+", "",
                  str(text or "")).lower()


@dataclass
class Command:
    """一条指令。老格式 ``["装备 X", "穿戴装备"]`` 归一化后 aliases/params 为空。"""

    cmd: str
    desc: str = ""
    aliases: List[str] = field(default_factory=list)
    kind: str = "action"          # view / action
    params: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)
    group: str = ""

    @property
    def is_view(self) -> bool:
        """只读面板类指令(如「我的纳戒」): 无参数占位符, 或显式 kind=view。"""
        if self.kind == "view":
            return True
        return not self.params and " " not in self.cmd

    def names(self) -> List[str]:
        return [self.cmd, *self.aliases]

    def as_pair(self) -> Tuple[str, str]:
        return self.cmd, self.desc


@dataclass
class Group:
    """一个功能分组(如「装备与道具」)。"""

    id: str
    name: str
    icon: str = ""
    aliases: List[str] = field(default_factory=list)
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    commands: List[Command] = field(default_factory=list)
    groups: List["Group"] = field(default_factory=list)   # 二级目录(可选)

    def names(self) -> List[str]:
        return [self.name, *self.aliases]


@dataclass
class HelpDoc:
    """一个游戏的完整帮助文档。groups 为空 = 老的扁平单页模式。"""

    game_id: str = ""
    game_name: str = ""
    title: str = ""
    subtitle: str = ""
    text: str = ""
    banner: str = "auto"
    theme: str = "inherit"
    groups: List[Group] = field(default_factory=list)
    flat: List[Command] = field(default_factory=list)

    @property
    def grouped(self) -> bool:
        return bool(self.groups)

    @property
    def command_count(self) -> int:
        if self.groups:
            return sum(len(g.commands) for g in self.groups
                       if not g.groups) or sum(len(g.commands) for g in self.groups)
        return len(self.flat)

    def all_commands(self) -> List[Command]:
        if not self.groups:
            return list(self.flat)
        out: List[Command] = []
        for g in self.groups:
            out.extend(g.commands)
        return out


@dataclass
class Page:
    """寻址结果: 渲染哪一页。"""

    kind: str                     # catalog / group / command / flat
    title: str = ""
    subtitle: str = ""
    group: Optional[Group] = None
    command: Optional[Command] = None
    miss: bool = False            # 主题没命中(渲染目录页 + 提示)
    suggestions: List[str] = field(default_factory=list)
    topic: str = ""               # 原始主题词(未命中时回显给用户)


def _parse_command(raw: Any, group_id: str = "") -> Optional[Command]:
    """接受两种写法: ``["指令", "说明"]`` 或 ``{"cmd": ..., "desc": ...}``。"""
    if isinstance(raw, dict):
        cmd = str(raw.get("cmd") or raw.get("name") or "").strip()
        if not cmd:
            return None
        return Command(
            cmd=cmd,
            desc=str(raw.get("desc") or raw.get("description") or ""),
            aliases=[str(a) for a in (raw.get("aliases") or []) if str(a).strip()],
            kind=str(raw.get("kind") or "action"),
            params=[str(p) for p in (raw.get("params") or [])],
            related=[str(r) for r in (raw.get("related") or [])],
            group=str(raw.get("group") or group_id),
        )
    if isinstance(raw, (list, tuple)) and len(raw) >= 2 and str(raw[0]).strip():
        return Command(cmd=str(raw[0]).strip(), desc=str(raw[1] or ""), group=group_id)
    return None


def _parse_group(raw: Dict[str, Any], index: int) -> Optional[Group]:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("title") or "").strip()
    gid = str(raw.get("id") or "").strip() or (name or f"g{index}")
    if not name:
        name = gid
    commands = [c for c in (_parse_command(r, gid) for r in (raw.get("commands") or []))
                if c is not None]
    subs = [g for g in (_parse_group(r, i) for i, r in enumerate(raw.get("groups") or []))
            if g is not None]
    return Group(
        id=gid,
        name=name,
        icon=str(raw.get("icon") or "").strip(),
        aliases=[str(a) for a in (raw.get("aliases") or []) if str(a).strip()],
        blocks=[b for b in (raw.get("blocks") or []) if isinstance(b, dict)],
        commands=commands,
        groups=subs,
    )


def _icon_for(group_name: str, commands: Sequence[Command]) -> str:
    haystack = group_name + "".join(c.cmd for c in commands[:8])
    for words, icon in _ICON_HINTS:
        if any(w in haystack for w in words):
            return icon
    return "star"


def auto_groups(flat: Sequence[Command]) -> List[Group]:
    """零配置聚簇: 按关键词把扁平指令表分成若干功能组(细分游戏也能自动出目录)。

    规则很轻: 逐条指令按 ``_ICON_HINTS`` 命中第一个关键词桶; 都不命中则进「其他玩法」。
    """
    buckets: Dict[str, List[Command]] = {}
    order: List[str] = []
    for c in flat:
        hay = c.cmd + c.desc
        icon = ""
        for words, ic in _ICON_HINTS:
            if any(w in hay for w in words):
                icon = ic
                break
        key = icon or "misc"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(c)
    label = {ic: name for name, ic in (
        ("装备与道具", "bag"), ("战斗挑战", "sword"), ("宗门", "sect"), ("仙宠", "pet"),
        ("生活职业", "furnace"), ("市集经济", "coin"), ("修行之路", "meditate"),
        ("日常与成就", "daily"), ("猫娘与道侣", "heart"), ("诸天与小世界", "star"),
        ("魔道与神道", "flame"), ("其他玩法", "misc"),
    )}
    return [Group(id=key, name=label.get(key, "其他玩法"), icon=key,
                  commands=buckets[key]) for key in order]


def normalize_help(raw: Optional[Dict[str, Any]], game_id: str = "",
                   game_name: str = "") -> HelpDoc:
    """把 help.json 的原始 dict 归一化成 :class:`HelpDoc`(新旧格式通吃)。"""
    raw = raw or {}
    flat = [c for c in (_parse_command(r) for r in (raw.get("commands") or []))
            if c is not None]
    groups = [g for g in (_parse_group(r, i) for i, r in enumerate(raw.get("groups") or []))
              if g is not None]
    if not groups and raw.get("auto_groups") and flat:
        groups = auto_groups(flat)
        # 自动聚簇只用于"生成目录"; 指令仍保留在分组里, flat 保留给回退
    return HelpDoc(
        game_id=game_id,
        game_name=game_name,
        title=str(raw.get("title") or game_name or ""),
        subtitle=str(raw.get("subtitle") or ""),
        text=str(raw.get("text") or ""),
        banner=str(raw.get("banner") or "auto"),
        theme=str(raw.get("theme") or "inherit"),
        groups=groups,
        flat=flat,
    )


def _score(needle: str, candidates: Sequence[str]) -> int:
    """匹配打分: 2=完全相等, 1=包含。均以归一化形式比较。"""
    n = _norm(needle)
    if not n:
        return 0
    best = 0
    for cand in candidates:
        c = _norm(cand)
        if not c:
            continue
        if c == n:
            return 2
        if n in c or c in n:
            best = max(best, 1)
    return best


def resolve_topic(doc: HelpDoc, topic: str = "") -> Page:
    """三层寻址: 指令 > 分组 > 游戏目录; 未命中回目录页并给出近似建议。"""
    topic = (topic or "").strip()
    if not doc.groups:
        return Page(kind="flat", title=doc.title or doc.game_name,
                    subtitle=doc.subtitle, miss=bool(topic))
    if not topic:
        return Page(kind="catalog", title=doc.title or doc.game_name,
                    subtitle=doc.subtitle)

    groups: List[Group] = []
    for g in doc.groups:
        groups.append(g)
        groups.extend(g.groups or [])

    # ① 精确匹配(指令 → 别名 → 分组 → 分组别名), 精确永远优先于包含
    for g in groups:
        for c in g.commands:
            if _score(topic, c.names()) == 2:
                return Page(kind="command", title=c.cmd, group=g, command=c,
                            subtitle=g.name, miss=False)
    for g in groups:
        if _score(topic, g.names()) == 2:
            return Page(kind="group", title=g.name, group=g, subtitle=doc.title,
                        miss=False)
    # ② 包含匹配: 用户把分组名整个打出来(如「鱼缸与鱼市怎么用」)时优先给分组页
    norm_topic = _norm(topic)
    for g in groups:
        if any(_norm(n) and _norm(n) in norm_topic for n in g.names()):
            return Page(kind="group", title=g.name, group=g, subtitle=doc.title,
                        miss=False)
    for g in groups:
        for c in g.commands:
            if _score(topic, c.names()) == 1:
                return Page(kind="command", title=c.cmd, group=g, command=c,
                            subtitle=g.name, miss=False)
    for g in groups:
        if _score(topic, g.names()) == 1:
            return Page(kind="group", title=g.name, group=g, subtitle=doc.title,
                        miss=False)
    # ③ 未命中 → 目录页 + 近似建议
    n = _norm(topic)
    suggestions = [g.name for g in groups
                   if any(ch in _norm(g.name) for ch in n)][:3]
    if not suggestions:
        suggestions = [g.name for g in groups[:3]]
    return Page(kind="catalog", title=doc.title or doc.game_name, subtitle=doc.subtitle,
                miss=True, suggestions=suggestions, topic=topic)


def split_help_intent(text: str, strip_words: Sequence[str] = ()) -> Optional[str]:
    """识别帮助意图, 返回主题部分(无主题返回空串); 不是帮助意图返回 None。

    ``strip_words`` 用于去掉游戏名/触发词残留(调用方传游戏名 + keywords.json):
    例: 「修仙帮助」→ ""; 「修仙帮助 战斗」→ "战斗"(strip_words=("修仙",)) → "战斗";
    「怎么玩」→ ""; 「钓鱼攻略」→ "".
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    hit = next((w for w in HELP_INTENT_WORDS if w in raw.lower() and w in raw), None)
    if hit is None:
        hit = next((w for w in HELP_INTENT_WORDS if w in raw.lower()), None)
    if hit is None:
        return None
    rest = re.sub(re.escape(hit), " ", raw, flags=re.IGNORECASE)
    # 去掉游戏名与触发词残留(长的先去掉, 避免"修仙"先吃掉"修仙帮助"的一部分)
    for word in sorted({str(w) for w in strip_words if str(w).strip()},
                       key=len, reverse=True):
        rest = rest.replace(word, " ")
    return re.sub(r"[\s\u3000]+", " ", rest).strip()
