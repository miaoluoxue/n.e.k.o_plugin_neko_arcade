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

### 2.5 ⚠️ 输出契约：游戏适配插件，不参与推送（最重要，必须读懂）

**核心原则：游戏只返回结构化结果，一切推送由主插件（brain）统一编排。**

`handle_action` 的返回契约：

```python
return {
    "facts": [...],          # ✅ 必须
    "outcome": "win",        # ✅ 必须
    "message": "结算文本",    # 用户可见文本(由 brain 统一推送)
    "images": [              # 可选: 需要展示的图片数据(由 brain 统一推送)
        {"text": "配文", "bytes": img_bytes, "mime": "image/png"},
        # 或 {"text": "配文", "url": "http://..."}
    ],
}
```

**为什么（双重回复的教训）：**

游戏若自己 `push_text`/`push_text_image` 推送（旧架构），又返回 `message`/`summary`，
宿主会把 summary 喂给主 LLM，LLM 复述一遍 → 用户看到两条几乎一样的话。

**新架构彻底解决：**

| 谁 | 做什么 |
|----|--------|
| 游戏 | 只返回 `{facts, outcome, message, images?}`，**不调用任何 push 方法** |
| brain | 统一推送：有 images → 推「配文/文本 + 图片」一次；无 images → 推 message 一次 |
| brain | 统一生成 summary（用情感模板 neko_text，与用户已见内容不同），宿主 LLM 自然演绎不复述 |
| brain | 统一处理高光卡片、状态锚、防打断、发图桥接 |

**游戏可用的桥接（通过 GameAdapter）：**

| 能力 | 用法 |
|------|------|
| 取图(不推) | `await self.pick_photo_for_delivery(category=...)` → 返回 images 数据交 brain 推 |
| 发图(后台/工具) | `await self.send_photo(...)`（仅 on_tick/LLM 工具等非 handle_action 路径） |
| 渲染卡片 | `await self.render_card(...)` → 生成 bytes 放进 images |
| 构造图片数据 | `self.build_image(text, bytes, mime)` |

**禁止：**

- ❌ `handle_action` 内调用 `push_text`/`push_text_image`/`push_text_image_url`/`push_help`
  （这些方法仅保留给 on_tick 后台提醒/历史兼容，新游戏 handle_action 不得使用）
- ❌ 返回 `pushed` / `summary` 字段（已废除，brain 统一处理）

**判断口诀：** 一条游戏动作，用户最多看到「游戏输出 + 猫娘自然回应」两条。
游戏自身不推送任何内容——所有用户可见输出都经 brain 一条通道发出。

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
| 渲染卡片 | `await self.render_card("游戏名", "标题", lines, mood)` → 生成 bytes 放进 images |
| 渲染帮助图 | `await self.render_help_img("游戏名", commands)` |
| 渲染头像 | `await self.render_avatar("excitement", 128)` |
| 取图(交brain推) | `await self.pick_photo_for_delivery("可爱")` → 返回 images 数据 |
| 构造图片数据 | `self.build_image("配文", img_bytes, "image/png")` |
| TTS 标记 | `self.tts_note("文字")` |
| 调用 LLM | `await self.call_llm("prompt")` |
| ~~推送文字~~ | ~~`push_text`~~（已废弃，仅 on_tick 后台提醒/历史兼容；handle_action 用 message 返回） |
| ~~推送图片~~ | ~~`push_text_image`~~（已废弃，handle_action 用 images 返回） |

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
            # ✅ 游戏只返回结构化结果, brain 统一推送(游戏不参与推送)
            return {"facts": [build_fact("win")], "outcome": "win",
                    "message": "你赢了！"}
        return {"facts": [], "outcome": "unknown", "message": ""}
```

**需要推送图片/卡片的游戏（返回 images 数据）：**

```python
async def handle_action(self, user_id, cmd, args=None):
    if cmd == "开始":
        img = await self.render_card("我的游戏", "开局", [("赢啦", "win")])
        images = []
        if img:
            # 构造 images 数据(brain 统一推送), 游戏不 push
            images.append(self.build_image("你赢了!", img, "image/png"))
        return {"facts": [build_fact("win")], "outcome": "win",
                "message": "你赢了！", "images": images}
    return {"facts": [], "outcome": "unknown", "message": ""}
```

> 关键：游戏**绝不调用** `push_text`/`push_text_image`；所有用户可见输出
> 都通过返回 `message` + `images` 交给 brain 统一编排，从架构上杜绝双重回复。