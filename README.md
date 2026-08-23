<p align="center">
  <h1 align="center">🎮 猫娘小游戏</h1>
  <p align="center"><b>neko_arcade</b> · N.E.K.O 插件 · v0.3.0</p>
  <p align="center">让猫娘陪你玩各种小游戏——钓鱼、海龟汤、猜硬币、修仙……一个插件，无限可能。</p>
</p>

---

## ✨ 核心特性

| 维度 | 能力 |
|------|------|
| 🧩 **插件化游戏** | `games/` 目录放一个小游戏包 = 完成接入，零改动主插件 |
| 🔍 **自动发现** | 启动时自动扫描注册所有游戏，加载失败不影响其他游戏 |
| 🎯 **统一接口** | `GameAdapter` 基类 —— 指令处理 / 玩家存档 / 状态上报 / 聊天推送 |
| 🖥️ **前端面板** | 玻璃拟态面板：游戏卡片 + 开始/停止 + 配置抽屉 + 指令台 |
| 💬 **自然语言** | 聊天/语音里对 AI 说「钓鱼」「猜硬币」自动路由（LLM 工具） |
| 💾 **完整存档** | 每个玩家的数据按游戏隔离存于 store，跨重启保留 |

---

## 📦 内置游戏

| 游戏 | 说明 |
|------|------|
| 🎣 钓鱼 | 每日抛竿钓鱼，收藏稀有鱼获，鱼市换鱼蛋，鱼竿鱼饵养成 |
| 🍲 海龟汤 | 猫娘当裁判出题，主人猜真相，猜对出卡片 |
| 🪙 猜硬币 | 和猫娘玩猜硬币正反面，每日次数限制 |
| 🧘 修仙 | 诸天修仙：境界/炼体、突破渡劫、采集炼丹炼器、拍卖行、仙宠、宗门、道侣师徒，猫娘全程陪玩 |

---

## 🚀 使用

1. N.E.K.O → 插件管理 → 启用「猫娘小游戏」
2. 控制面板选游戏 → 开始，或直接在聊天里对 AI 说「钓鱼」「猜硬币」
3. 面板每 3s 刷新游戏状态；点 ⚙️ 打开中文配置抽屉

---

## 🧱 架构

```
neko_arcade/
├── plugin.toml            # 插件元数据
├── __init__.py            # 主插件：生命周期 / AI 入口 / LLM 工具 / 存档
├── core/                  # 框架：契约 / 注册表 / 大脑 / 情绪 / 配置
├── adapters/              # 渲染桥 / LLM 客户端 / TTS / 推送
├── games/                 # 小游戏包（每个 = 一个小游戏）
│   ├── fishing/           # 钓鱼
│   ├── soupbubble/        # 海龟汤
│   ├── coinflip/          # 猜硬币
│   └── xiuxian/           # 诸天修仙（猫娘全程陪玩）
├── static/index.html      # 前端面板
├── i18n/                  # 语言包（zh-CN / en）
└── docs/                  # 使用 + 接入指南
```

### 入口点

| 类型 | id | 说明 |
|------|----|------|
| `@lifecycle` | startup / shutdown | 发现游戏 / 卸载游戏 |
| `@plugin_entry` | list_games | 游戏列表 |
| `@plugin_entry` | play_game | 玩指定游戏（AI 路由） |
| `@plugin_entry` | start_game / stop_game | 面板启动/停止会话 |
| `@plugin_entry` | game_status / game_help | 状态 / 帮助查询 |
| `@plugin_entry` | get_game_config / save_game_config | 配置读写（深度合并） |
| `@plugin_entry` | set_game_enabled | 启用/停用游戏 |
| `@plugin_entry` | get_arcade_state | 前端面板轮询 |
| `@llm_tool` | play_game | 聊天中自动路由指令 |

---

## 🛠️ 接入新游戏

见 [docs/guide.md](docs/guide.md) —— 3 步完成：
1. `games/my_game/` 建包
2. 实现 `GameAdapter`（可覆写 `support_panel()` 提供中文配置面板）
3. `__init__.py` 声明 `game_class`

---

## ⚙️ 配置

每个游戏自带中文配置面板（`support_panel()` schema 驱动）：字段、类型、说明都由游戏声明，面板自动渲染。玩家存档自动存于插件 store。

---

## Development

This repository is meant to live at:

```text
N.E.K.O/plugin/plugins/neko_arcade
```

When publishing to the plugin market, use this GitHub repository name:

```text
n.e.k.o_plugin_neko_arcade
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From the N.E.K.O repository root:

```bash
uv run --with pip python -m plugin.neko_plugin_cli.cli sync neko_arcade --clean
uv run python -m plugin.neko_plugin_cli.cli check neko_arcade
uv run python -m plugin.neko_plugin_cli.cli check -r neko_arcade
```

Python 运行时依赖惰性导入（httpx / Pillow / Playwright 缺失时优雅降级），
`pyproject.toml` 无强制依赖，无需 vendor。

## Market release

Push a tag matching `plugin.toml` version to create a GitHub Release asset:

```bash
git tag v0.3.0
git push origin v0.3.0
```

`.github/workflows/release.yml` 会构建并上传 `neko_arcade.neko-plugin`，
用该 GitHub Release URL 在插件市场发布对应版本。

---

## 📜 版本历史

| 版本 | 日期 | 变更 |
|:----:|:----:|:-----|
| **v0.3.0** | 2026-08 | 新增诸天修仙小游戏（境界/炼体、突破渡劫、采集炼丹炼器、拍卖行、仙宠、宗门、道侣师徒，猫娘全程陪玩）、runtime/LLM 优化、ruff 检查修复 |
| **v0.2.0** | 2026-08 | 玻璃拟态面板（开始/停止/状态标签）、schema 驱动中文配置抽屉、海龟汤、猜硬币、渲染桥、品牌页脚、i18n |
| **v0.1.0** | 2026-07 | 初版：聚合框架（自动发现/注册/分发/存档）+ 钓鱼示例游戏 |

---

<p align="center">
  🐱 <b>猫娘小游戏</b> — 一个插件，无限可能<br>
  📘 <a href="https://project-neko.online/plugins/">N.E.K.O 文档</a> · 🛠️ <a href="https://project-neko.online/plugins/quick-start">插件开发指南</a>
</p>
