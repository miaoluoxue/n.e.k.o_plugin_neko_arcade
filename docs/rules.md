# 🐱 猫娘小游戏 · 插件规则（接入标准）

> 本插件是**游戏大脑总管家**。所有小游戏必须**适配本插件**，而非插件适配小游戏。
> 游戏提供玩法逻辑（facts + outcome），情感、图片、语音、帮助、配置、开关、输入路由全部由大脑统一负责。

## 1. 小游戏形式

每个小游戏一个文件夹，统一放入 `games/` 目录，由 `registry.discover()` 自动扫描发现：

```
games/
├── fishing/          # 复杂游戏：多个文件
│   ├── __init__.py
│   ├── game.py
│   └── data.py
└── coinflip/         # 简单游戏：两个文件
    ├── __init__.py
    └── game.py
```

`__init__.py` 必须声明 `game_class = "ClassName"`，指向 GameAdapter 子类。

## 2. 硬性契约（必须遵守）

### 2.1 游戏必须实现

```python
async def handle_action(self, user_id: str, cmd: str, args: dict | None = None) -> dict
```

返回值必须含：

| 字段 | 必须 | 说明 |
|------|:----:|------|
| `facts` | ✅ | list[dict]，每条事实必须含 `kind` 字段 |
| `outcome` | ✅ | str，结果类型，大脑据此分类情绪。不认识指令时返回 `"unknown"` |
| `message` | ❌ | str，游戏自身结算文本 |

### 2.2 facts 的 kind 约定

| kind | 含义 | 大脑映射情绪 |
|------|------|--------------|
| `catch` | 捕获/获得 | rarity=legendary/？/epic/rare → 高光 |
| `trash` | 垃圾 | 平淡 |
| `empty` | 空手 | 平淡 |
| `win` / `lose` | 胜负 | win→高光，lose→低谷 |
| `event` | 随机事件 | 平淡 |
| `sell` / `buy` / `equip` / `tank_upgrade` | 交易/装备 | 平淡 |

### 2.3 outcome 约定

| 值 | 含义 |
|----|------|
| `caught_legendary` / `big_win` / `highlight` | 高光 → LLM 渲染 + 图片卡片 |
| `lose` / `lowlight` / `air` | 低谷 → LLM 安慰 |
| `unknown` | 不认识的指令 → 大脑触发邀请流程 |
| 其他 | 日常 → 游戏模板或通用模板 |

### 2.4 禁止事项

- ❌ 游戏内不得自行管理情感话术（"好开心"），只报事实
- ❌ 不得直接读写主项目配置
- ❌ 不得直接调用 `push_message`（应使用 `self.push_text()` 等服务接口）
- ❌ 不得在 `games/__init__.py` 中手动导入游戏（自动发现）
- ✅ 用 `self._config`（大脑注入的配置）、`get_user_data`/`save_user_data`（存档）
- ✅ 可用 `self.push_text()` / `self.render_card()` 等服务接口

### 2.5 ⚠️ 输出契约：禁止「双重回复」（最重要，必须读懂）

**先理解宿主机制（插件无法关闭）：**

`@plugin_entry` 返回的 `Ok({...})` 中带 `summary` 字段时，宿主会把它作为
`task_result` 事件喂给主 LLM，**LLM 一定会生成一条自然语言回复**。这是宿主
行为，插件侧无法关闭——所以 `summary` 是「给 LLM 看的提示」，不是「给用户
看的原文」。

**双重回复是怎么发生的（真实事故）：**

```python
# ❌ 错误示范（history 早期版本的 bug）
async def _h_today(self, user_id, save):
    neko = "哇,15 条呢!主人想听哪个年代的故事?"   # 情感模板
    await self.push_text_image(neko, card)        # ① 游戏自己推送 → 用户看到
    return {"outcome": "today", "message": neko,  # ② message 也带同一文本
            "summary": neko, ...}                 # ③ summary 也带同一文本
```

结果用户看到 **两条几乎一样的话**：
1. 游戏自己 `push_text_image` 推送的 `neko`（聊天窗直接渲染）
2. LLM 收到 `summary=neko` 后**复述了一遍**（宿主 task_result → 主 LLM 回复）

**正确做法（二选一）：**

| 方案 | 适用 | 写法 |
|------|------|------|
| **A. 只返回 message，不自推**（老游戏模式） | 纯文本结果 | `handle_action` 返回 `{"message": "钓到一条鲲!"}`，不自推；brain 统一推送一次，LLM 收到 summary 后自然回应（不会复述，因为 LLM 会把结果当成事实来演绎） |
| **B. 自推图文，summary 给 LLM 提示**（带卡片/图片） | 需要图片卡片的游戏 | 自己 `push_text_image(text, img)` 推送用户可见内容，**返回的 `message` 置空或只给"给 LLM 的指令性提示"**，让 LLM 知道发生了什么、该怎么说，而不是复述用户已看到的原文 |

**具体规则：**

1. **不要**在 `handle_action` 里 `push_text` 之后又把**同一文本**放进 `message`/`summary` 返回。
2. 如果游戏**自推了用户可见内容**（文字/图片），返回的 `message` 应为：
   - 空字符串 `""`（最安全，brain 不重复推，LLM 从 facts 推断）
   - 或**给 LLM 的第三人称描述**，如 `"已展示今天的15条历史大事卡片，等待主人选年份"`——这是给 LLM 的上下文，不是给用户的重复文本
3. 如果游戏**不自推**（纯文本结果），只返回 `message`，让 brain 统一推一次，LLM 自然回应——这是最不容易出错的默认方案。
4. **`pushed` 标记**：仅在游戏确实自行推送过用户可见内容时才返回 `"pushed": True`，告诉 brain「我已推送过，别再推 message」。返回 `pushed` 时 `message` 仍要遵守规则 2（不能是用户已见原文），否则 LLM 仍会复述。

**判断口诀：** 一条游戏动作，用户最多看到「游戏输出 + 猫娘自然回应」两条。
如果看到两条**内容几乎一样**的话，就是双重回复——检查是不是自推后又把同一
文本放进了 message/summary。

## 3. 配置统一管理

配置文件在 `data/config/{game_id}/`：

```
data/config/
├── fishing/
│   ├── config.json       # 游戏参数
│   ├── help.json         # 帮助数据
│   ├── emotion.json      # 情感模板（可选）
│   └── keywords.json     # 触发关键词（可选）
└── coinflip/
    ├── config.json
    ├── help.json
    ├── emotion.json
    └── keywords.json
```

### config.json

```json
{
  "max_plays_per_day": 20
}
```

游戏内读取（启动时大脑自动注入）：

```python
self._config.get("max_plays_per_day", 20)
```

### help.json（帮助文档）

```json
{
  "commands": [
    ["猜硬币 正", "猜硬币正面"]
  ],
  "text": "🪙 猜硬币：和猫娘比手气！"
}
```

玩家说「帮助」时，大脑读取 help.json → 渲染成**带猫娘表情的帮助图**推送。

### emotion.json（情感模板，可选）

```json
{
  "win": ["赢啦！运气站在我这边喵！", "嘿嘿，猜对了！"],
  "lose": ["唔…输了喵", "运气不好，再来一次！"]
}
```

大脑 `EmotionRenderer` 优先使用游戏模板，无匹配时使用通用兜底模板。

### keywords.json（触发关键词，可选）

```json
["钓鱼", "钓", "鱼缸", "鱼市", "售鱼", "鱼竿", "鱼饵", "换竿", "换饵", "商店", "购买", "升级鱼缸"]
```

`parse_input()` 遍历游戏关键词匹配用户输入。无此文件时默认使用 `[name, id]`。

## 4. 存档

```python
data = await self.get_user_data(uid, {})    # 读
await self.save_user_data(uid, data)         # 写
```

存档自动按 `game:{id}:user:{uid}` 隔离，跨重启保留。

## 5. 生命周期（可选）

```python
async def on_register(self): ...   # 注册时
async def on_unload(self): ...     # 卸载时
async def on_start(self, uid): ... # 会话开始
async def on_stop(self, uid): ...  # 会话结束
async def get_status(self, uid): ... # 面板状态
```

## 6. 大脑提供的公共能力

### 配置与数据

| 能力 | 用法 |
|------|------|
| 配置 | `self._config` |
| 帮助数据 | `self._help` |
| 存档 | `self.get_user_data` / `self.save_user_data` |
| 元数据 | `self.id` / `self.name` / `self.icon` |

### 服务接口（由 `bind_services()` 注入）

| 能力 | 用法 |
|------|------|
| 推送文字 | `await self.push_text("文字")` |
| 推送图片 | `await self.push_text_image("文字", image_bytes)` |
| 推送帮助 | `await self.push_help("标题", image_bytes, "文字")` |
| 渲染卡片 | `await self.render_card("游戏名", "标题", lines, mood)` |
| 渲染帮助图 | `await self.render_help_img("游戏名", commands)` |
| 渲染头像 | `await self.render_avatar("excitement", 128)` |
| TTS 标记 | `self.tts_note("文字")` |
| 调用 LLM | `await self.call_llm("prompt")` |

### 行为定制（可选覆写）

| 能力 | 用法 |
|------|------|
| 触发关键词 | `def get_keywords(self) -> list` |
| 情感模板 | `def get_emotion_templates(self) -> dict` |
| 事件分类 | `def classify_event(self, outcome, facts) -> str` |
| 里程碑处理 | `async def on_milestone(self, outcome, facts, memory)` |
| 卡片格式化 | `def format_fact_for_card(self, fact) -> tuple` |
| 卡片开关 | `def wants_card(self, outcome, facts) -> bool` |

游戏**不需要**（也不允许）自己实现：开关管理、情感渲染、输入路由、邀请流程、帮助渲染、记忆。

## 7. 接入检查清单

- [ ] 实现 `GameAdapter`，id/name/description/icon 齐全
- [ ] `handle_action` 返回 facts + outcome，不认识指令返回 `outcome="unknown"`
- [ ] 不自行管理情感话术
- [ ] 配置和帮助放 `data/config/{id}/`
- [ ] 可选：`emotion.json` 放情感模板，`keywords.json` 放触发关键词
- [ ] `games/{id}/__init__.py` 声明 `game_class = "ClassName"`
- [ ] 不在 `games/__init__.py` 中手动导入
- [ ] **双重回复检查**（见 2.5）：若在 `handle_action` 里 `push_text`/`push_text_image` 自推了用户可见内容，确认返回的 `message`/`summary` **不是同一文本**；不自推时只返回 `message` 即可

## 8. 与主项目通信

大脑通过 `adapters/` 层统一完成：

| 渠道 | 适配器 |
|------|--------|
| 文字推送 | PushSender.text |
| 图片推送（卡片/帮助图） | PushSender.text_with_image |
| 语音推送 | PushSender.text_with_audio（TTS 预留） |
| LLM 渲染 | LLMProvider（宿主注入优先） |
| 猫娘表情图 | ImageRenderer.render_neko_avatar |

游戏可通过 `self.push_text()` 等服务接口调用，或只返回 facts + outcome 由大脑统一输出。

## 9. 输入路由与邀请机制

### 输入匹配

```
用户说"钓鱼"   → parse_input("钓鱼")   → 遍历游戏关键词 → 匹配 fishing → cmd="钓鱼"
用户说"我想钓鱼" → parse_input("我想钓鱼") → 匹配 fishing → cmd="我想钓鱼"
```

### 邀请流程

```
用户说"我想钓鱼" → game.handle_action() 不认识 → 返回 outcome="unknown"
    → brain 检测到 unknown → 调用 _invite_game()
    → 推送邀请到聊天框（含游戏指令列表）
    → 返回 invitation + game_commands 给 LLM
```

## 10. 简单游戏模板

`games/my_game/__init__.py`：

```python
"""我的游戏包。"""

game_class = "MyGame"

from .game import MyGame

__all__ = ["MyGame"]
```

`games/my_game/game.py`：

```python
"""我的游戏。"""

from neko_arcade.core.contracts import GameAdapter, build_fact


class MyGame(GameAdapter):
    id = "my_game"
    name = "我的游戏"
    description = "好玩的小游戏"
    icon = "🎲"

    async def handle_action(self, user_id, cmd, args=None):
        if cmd == "开始":
            # ✅ 方案A(默认, 最简单): 只返回 message, 不自推。
            # brain 统一推送一次, LLM 收到 summary 后自然回应, 不会双重回复。
            return {"facts": [build_fact("win")], "outcome": "win",
                    "message": "你赢了！"}
        return {"facts": [], "outcome": "unknown", "message": ""}
```

**需要推送图片/卡片的游戏（方案B，避免双重回复）：**

```python
async def handle_action(self, user_id, cmd, args=None):
    if cmd == "开始":
        img = await self.render_card("我的游戏", "开局", [("赢啦", "win")])
        if img:
            # ① 自推用户可见内容(图文)
            await self.push_text_image("你赢了!", img)
            # ② message 给 LLM 指令性提示(不是用户已见原文!)
            return {"facts": [build_fact("win")], "outcome": "win",
                    "message": "已展示胜利卡片, 恭喜主人", "pushed": True}
        return {"facts": [build_fact("win")], "outcome": "win",
                "message": "你赢了！"}  # 无图时退回方案A
    return {"facts": [], "outcome": "unknown", "message": ""}
```

> 关键：`pushed=True` 只告诉 brain「别重复推 message」；`message` 仍必须是
> 给 LLM 的提示而非用户已见原文，否则 LLM 复述 summary 造成双重回复。