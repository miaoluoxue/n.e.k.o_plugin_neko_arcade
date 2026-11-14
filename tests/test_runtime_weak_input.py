"""弱指令路由测试: 无会话时「再来一局/继续玩」等模糊指令回最近游戏。

覆盖 runtime.parse_input 的 4 条路由规则, 重点是规则 4(last_game 弱指令兜底):
用户无会话时说了想接着玩但没点名, LLM 调 play_game 传入模糊原话 → 应路由到
最近玩过的游戏, 并把 cmd 换成该游戏的启动指令(否则游戏收到"再来一局"不认)。
"""
from plugin.plugins.neko_arcade.core.runtime import ArcadeRuntime


class _FakeBrain:
    def __init__(self, current=None, last=None):
        self._current = current
        self._last = last

    @property
    def current_game(self):
        return self._current

    @property
    def last_game(self):
        return self._last


class _FakeGame:
    def __init__(self, gid, keywords):
        self.id = gid
        self.enabled = True
        self._keywords = keywords

    def get_keywords(self):
        return self._keywords


class _FakeRegistry:
    def __init__(self, games):
        self._by_id = {g.id: g for g in games}
        self.games = list(games)

    def get(self, gid):
        return self._by_id.get(gid)


def _make_runtime(brain=None):
    rt = ArcadeRuntime.__new__(ArcadeRuntime)  # 跳过 __init__ 的宿主加载
    rt.brain = brain or _FakeBrain()
    rt.registry = _FakeRegistry([
        _FakeGame("fishing", ["钓鱼", "抛竿"]),
        _FakeGame("remake", ["人生重开", "重启人生"]),
    ])
    return rt


def test_weak_input_routes_to_last_game():
    """无会话 + 「再来一局」→ 回最近游戏, cmd 换成启动指令。"""
    rt = _make_runtime(_FakeBrain(last="fishing"))
    gid, cmd = rt.parse_input("再来一局")
    assert gid == "fishing"
    assert cmd == "钓鱼", "弱指令应换成游戏启动指令, 让游戏能直接执行"


def test_weak_input_other_hints():
    """多个弱指令意图词都路由回最近游戏。"""
    rt = _make_runtime(_FakeBrain(last="remake"))
    for hint in ("继续玩", "接着玩", "玩点什么", "随便玩", "来一把", "再玩一次"):
        gid, cmd = rt.parse_input(hint)
        assert gid == "remake", f"{hint} 应路由到 remake, got {gid}"
        assert cmd == "人生重开"


def test_no_last_game_no_routing():
    """从未玩过 → 弱指令不路由, 返回 None(LLM 收"没有找到匹配的游戏")。"""
    rt = _make_runtime(_FakeBrain(last=None))
    gid, cmd = rt.parse_input("再来一局")
    assert gid is None


def test_weak_input_does_not_shadow_explicit_game():
    """弱指令词与显式游戏名并存 → 显式游戏优先(规则 2)。"""
    rt = _make_runtime(_FakeBrain(last="fishing"))
    gid, cmd = rt.parse_input("再玩一次人生重开")
    assert gid == "remake", "显式游戏名应优先于弱指令兜底"


def test_current_game_takes_priority():
    """有会话时任意输入回当前游戏(规则 3), 不触发弱指令。"""
    rt = _make_runtime(_FakeBrain(current="fishing", last="remake"))
    gid, cmd = rt.parse_input("继续")
    assert gid == "fishing", "当前会话优先于 last_game"


def test_current_game_long_chat_is_not_routed():
    """有会话时，长句仍应留给普通聊天，不能全塞给当前游戏。"""
    rt = _make_runtime(_FakeBrain(current="fishing", last="remake"))
    for text in ("今天天气不错，工作总算忙完了", "我觉得修仙那个话题挺有意思的",
                 "刚才那条鱼看起来真的好大啊"):
        gid, _ = rt.parse_input(text)
        assert gid is None, f"长闲聊不应路由进游戏: {text!r}"


def test_current_game_short_alias_still_routes():
    rt = _make_runtime(_FakeBrain(current="fishing"))
    gid, cmd = rt.parse_input("背包")
    assert (gid, cmd) == ("fishing", "背包")


def test_weak_input_not_triggered_by_plain_chat():
    """普通闲聊不含弱指令词 → 不路由(防误伤)。"""
    rt = _make_runtime(_FakeBrain(last="fishing"))
    gid, cmd = rt.parse_input("今天天气不错")
    assert gid is None


def test_bare_continue_words_do_not_hijack_chat():
    """裸「继续/再来/接着/再开」是日常聊天，不能自动开最近游戏。"""
    rt = _make_runtime(_FakeBrain(last="fishing"))
    for text in ("继续", "再来", "接着", "再开"):
        gid, _ = rt.parse_input(text)
        assert gid is None, f"裸词「{text}」不应被当成游戏启动指令"


def test_toy_is_not_stripped_to_single_character():
    """玩具是普通词，不能被「玩」前缀归一化成「具」。"""
    rt = _make_runtime(_FakeBrain(current=None, last="fishing"))
    gid, cmd = rt.parse_input("玩具")
    assert gid is None
    assert cmd == "玩具"


def test_play_prefix_normalized_to_start_cmd():
    """「玩X/来玩X」口语前缀 → 归一化为游戏启动词(否则游戏不认返回 unknown)。

    parse_input 关键词是子串匹配(「玩塔罗牌」命中「塔罗牌」), 但游戏 handle_action
    多为精确匹配——不归一化时「玩塔罗牌」原样传给游戏会返回 unknown(线上 bug)。
    """
    rt = ArcadeRuntime.__new__(ArcadeRuntime)  # 无会话, 走规则 2
    rt.brain = _FakeBrain()
    rt.registry = _FakeRegistry([
        _FakeGame("tarot", ["占卜", "塔罗牌", "抽牌"]),
        _FakeGame("fishing", ["钓鱼", "抛竿"]),
    ])
    # 纯口语「玩塔罗牌」→ 归一化为 tarot 启动词「占卜」
    gid, cmd = rt.parse_input("玩塔罗牌")
    assert gid == "tarot"
    assert cmd == "占卜", f"纯口语前缀应归一化为启动词, got {cmd!r}"

    # 带具体指令「玩塔罗牌 恋人」→ 保留指令
    gid, cmd = rt.parse_input("玩塔罗牌 恋人")
    assert gid == "tarot"
    assert cmd == "塔罗牌 恋人", f"带具体指令应保留, got {cmd!r}"

    # 「来玩钓鱼」→ 归一化钓鱼启动词
    gid, cmd = rt.parse_input("来玩钓鱼")
    assert gid == "fishing"
    assert cmd == "钓鱼"

    # 直接游戏名「塔罗牌」→ 原样(游戏认识精确词)
    gid, cmd = rt.parse_input("塔罗牌")
    assert gid == "tarot"
    assert cmd == "塔罗牌"


def test_chat_mentions_do_not_start_games_without_intent():
    rt = _make_runtime(_FakeBrain())
    for text in ("我今天路过河边看到有人钓鱼", "钓鱼真有意思",
                 "照片里那棵树很好看", "修仙小说最近很火"):
        gid, _ = rt.parse_input(text)
        assert gid is None, f"闲聊提及不应开游戏: {text!r}"


def test_explicit_intent_and_command_phrases_still_start_games():
    rt = _make_runtime(_FakeBrain())
    for text, expected in (("玩钓鱼", "fishing"), ("开始钓鱼", "fishing"),
                           ("一起玩人生重开", "remake"), ("钓鱼3次", "fishing")):
        gid, _ = rt.parse_input(text)
        assert gid == expected, f"{text!r} 应路由 {expected}, got {gid}"


def test_broad_intent_words_do_not_hijack_related_chat():
    rt = _make_runtime(_FakeBrain())
    for text in (
        "我们一起讨论钓鱼剧情",
        "我想了解修仙小说",
        "开始研究钓鱼技巧",
        "陪我看看这幅钓鱼画",
    ):
        gid, _ = rt.parse_input(text)
        assert gid is None, f"宽泛聊天不应启动游戏: {text!r} -> {gid}"


def test_send_photo_tool_is_explicit_request_only():
    rt = ArcadeRuntime.__new__(ArcadeRuntime)
    rt.registry = _FakeRegistry([_FakeGame("neko_photo", ["喵图相册"])])
    captured = {}

    def register(**kwargs):
        captured.update(kwargs)

    plugin = type("P", (), {"tool_send_photo": object()})()
    rt._register_send_photo_tool(plugin, register, None)
    description = captured["description"]
    assert "仅在用户明确索要图片时调用" in description
    assert "不要改用 play_game" in description
    assert "必须立即调用" not in description
    assert "想给主人一个惊喜" not in description


def test_play_game_route_does_not_select_gallery_without_photo_intent():
    rt = _make_runtime(_FakeBrain())
    rt.registry = _FakeRegistry([
        _FakeGame("neko_photo", ["喵图相册", "发图", "照片", "看图"]),
        _FakeGame("fishing", ["钓鱼", "抛竿"]),
    ])
    gid, cmd = rt.parse_input("钓鱼")
    assert (gid, cmd) == ("fishing", "钓鱼")
