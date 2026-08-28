# 🛠️ 开发文档（简版）

> 本页是**开发者接入指引**的简版，供面板快速查阅。
> 完整文档（契约 / 接口 / 配置规范 / 示例）在 GitHub 仓库：
> **`docs/development.md`**（开发文档）与 **`docs/rules.md`**（接入规则）。

## 三步接入一个小游戏

```bash
games/
└── my_game/
    ├── __init__.py      # 声明: game_class = "MyGame"
    └── game.py          # 实现 GameAdapter
```

1. **建包**：`games/my_game/` 放 `__init__.py` + `game.py`
2. **实现**：继承 `GameAdapter`，写 `handle_action(user_id, cmd, args)` 返回
   `{facts, outcome, message, images?}`
3. **配置**：`data/config/my_game/` 放 `config.json` / `help.json` / `keywords.json`

> ✅ **零登记**：插件自动发现游戏包，无需改主插件或文档，面板自动出现。
> ⚠️ 命令必须**游戏专属前缀**（防跨游戏串台），详见完整版文档。
