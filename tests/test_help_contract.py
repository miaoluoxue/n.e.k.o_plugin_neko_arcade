# -*- coding: utf-8 -*-
"""统一帮助系统契约测试: 新旧格式归一化、三层寻址、别名与兜底。

不依赖浏览器与网络: 只测数据层(core/help/contract.py)。
"""
from __future__ import annotations

import json
from pathlib import Path

from plugin.plugins.neko_arcade.core.help import (
    auto_groups,
    normalize_help,
    resolve_topic,
    split_help_intent,
)

OLD_FORMAT = {
    "text": "钓鱼说明",
    "commands": [["钓鱼", "抛竿钓鱼"], ["鱼缸", "查看收藏的鱼"], ["鱼市", "出售鱼获"]],
}

NEW_FORMAT = {
    "text": "钓鱼说明",
    "commands": [["钓鱼", "抛竿钓鱼"]],
    "title": "钓鱼",
    "subtitle": "每日抛竿",
    "groups": [
        {"id": "fish", "name": "钓鱼玩法", "icon": "fishing",
         "aliases": ["抛竿", "钓"],
         "commands": [
             ["钓鱼", "抛竿钓鱼"],
             {"cmd": "钓鱼 3", "desc": "连抛 3 次", "params": ["次数"],
              "aliases": ["连钓"]},
         ]},
        {"id": "tank", "name": "鱼缸与鱼市", "icon": "coin", "aliases": ["鱼市", "卖鱼"],
         "commands": [["鱼缸", "查看收藏的鱼"], ["鱼市", "出售鱼获"]]},
    ],
}


# ── 归一化 ──────────────────────────────────────────────
def test_old_format_becomes_flat_doc() -> None:
    doc = normalize_help(OLD_FORMAT, "fishing", "钓鱼")
    assert doc.groups == []
    assert not doc.grouped
    assert doc.command_count == 3
    assert [c.cmd for c in doc.flat] == ["钓鱼", "鱼缸", "鱼市"]
    # 老格式没有 groups → 仍然渲染"扁平单页", 与升级前行为一致
    assert resolve_topic(doc, "").kind == "flat"
    assert resolve_topic(doc, "鱼市").kind == "flat"


def test_new_format_parses_groups_aliases_and_object_commands() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    assert doc.grouped and len(doc.groups) == 2
    assert doc.title == "钓鱼" and doc.subtitle == "每日抛竿"
    fish = doc.groups[0]
    assert fish.aliases == ["抛竿", "钓"]
    assert [c.cmd for c in fish.commands] == ["钓鱼", "钓鱼 3"]
    assert fish.commands[1].params == ["次数"]
    assert fish.commands[1].aliases == ["连钓"]
    assert doc.command_count == 4


def test_view_and_action_kind_detection() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    plain = doc.groups[1].commands[0]
    with_param = doc.groups[0].commands[1]
    assert plain.is_view           # 无参数占位符 = 查看类
    assert not with_param.is_view  # 带 params = 动作类


# ── 三层寻址 ────────────────────────────────────────────
def test_resolve_empty_topic_is_catalog() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    page = resolve_topic(doc, "")
    assert page.kind == "catalog" and page.title == "钓鱼"


def test_resolve_group_by_name_and_alias() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    assert resolve_topic(doc, "鱼缸与鱼市").kind == "group"
    by_alias = resolve_topic(doc, "卖鱼")
    assert by_alias.kind == "group" and by_alias.group.id == "tank"


def test_resolve_command_beats_group() -> None:
    """指令级优先于分组级: 「钓鱼」既像分组别名又是指令名, 必须命中指令页。"""
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    page = resolve_topic(doc, "钓鱼")
    assert page.kind == "command"
    assert page.command.cmd == "钓鱼"


def test_resolve_command_alias_and_param_form() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    assert resolve_topic(doc, "连钓").command.cmd == "钓鱼 3"
    assert resolve_topic(doc, "钓鱼 3").kind == "command"


def test_resolve_miss_returns_catalog_with_suggestions() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    page = resolve_topic(doc, "完全不存在的东西")
    assert page.kind == "catalog"
    assert page.miss and page.suggestions
    assert page.topic == "完全不存在的东西"


# ── 零配置兜底 ──────────────────────────────────────────
def test_auto_groups_cluster_flat_commands() -> None:
    flat = normalize_help(OLD_FORMAT, "fishing", "钓鱼").flat
    groups = auto_groups(flat)
    assert groups and sum(len(g.commands) for g in groups) == len(flat)
    assert all(g.name and g.icon for g in groups)


def test_auto_groups_opt_in_only() -> None:
    """没写 auto_groups 就不自动聚簇(保持老行为); 写了才生成目录。"""
    assert normalize_help(OLD_FORMAT, "fishing", "钓鱼").groups == []
    opted = normalize_help({**OLD_FORMAT, "auto_groups": True}, "fishing", "钓鱼")
    assert opted.groups


def test_group_nesting_is_not_supported() -> None:
    """分组只有一层: JSON 里再嵌 groups 一律忽略, 不做多级目录(避免过度嵌套)。"""
    raw = {
        "groups": [{
            "id": "outer", "name": "外层", "icon": "star",
            "commands": [["外层指令", "说明"]],
            "groups": [{"id": "inner", "name": "内层",
                        "commands": [["内层指令", "说明"]]}],
        }]
    }
    doc = normalize_help(raw, "g", "G")
    assert len(doc.groups) == 1
    outer = doc.groups[0]
    assert not hasattr(outer, "groups")            # 模型里没有嵌套分组的槽位
    assert [c.cmd for c in outer.commands] == ["外层指令"]
    assert doc.command_count == 1
    # 内层名字不参与寻址(它压根没被解析), 只回目录页
    page = resolve_topic(doc, "内层")
    assert page.kind == "catalog" and page.miss


# ── 帮助意图识别 ────────────────────────────────────────
def test_split_help_intent() -> None:
    assert split_help_intent("帮助") == ""
    assert split_help_intent("修仙帮助", strip_words=("修仙",)) == ""
    assert split_help_intent("修仙帮助 战斗", strip_words=("修仙",)) == "战斗"
    # 游戏名/触发词由调用方传入剥离(brain 传游戏名 + keywords.json);
    # 不传时保留原文, 由调用方自行处理
    assert split_help_intent("修仙帮助") == "修仙"
    assert split_help_intent("修仙纳戒帮助", strip_words=("修仙",)) == "纳戒"
    assert split_help_intent("怎么玩") == ""
    assert split_help_intent("help") == ""
    assert split_help_intent("抛竿") is None
    assert split_help_intent("") is None


# ── 真实配置: xiuxian 11 个分组 ─────────────────────────
def _xiuxian_doc():
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "data" / "config" / "xiuxian" / "help.json")
                     .read_text(encoding="utf-8"))
    return normalize_help(raw, "xiuxian", "诸天修仙")


def test_xiuxian_help_covers_all_commands() -> None:
    doc = _xiuxian_doc()
    assert doc.grouped and len(doc.groups) == 11
    assert doc.command_count == 61
    # 老字段保留(面板 get_game_config 仍读 commands/text)
    assert doc.flat and doc.text


def test_xiuxian_topics_resolve() -> None:
    doc = _xiuxian_doc()
    bag = resolve_topic(doc, "纳戒")
    assert bag.kind == "group" and bag.group.name == "装备与道具"
    assert bag.group.blocks          # 纳戒页带版式块(装备栏/背包格)
    battle = resolve_topic(doc, "战斗")
    assert battle.kind == "group" and battle.group.name == "战斗挑战"
    cmd = resolve_topic(doc, "装备 X")
    assert cmd.kind == "command" and cmd.command.cmd == "装备 X"
    assert resolve_topic(doc, "背包").kind in ("group", "command")
    assert resolve_topic(doc, "生活").group.name == "生活职业"
