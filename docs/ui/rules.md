# 📜 接入规则（简版）

> 硬性契约的**精简版**，供面板快速查阅。
> 完整规则与踩坑手册在 GitHub 仓库：
> **`docs/rules.md`**（插件规则）与 **`docs/pitfalls.md`**（避坑手册）。

## 核心契约

| 规则 | 说明 |
|------|------|
| 输出 | 游戏只返回 `{facts, outcome, message, images?}`，**不自行推送** |
| 推送 | 一切用户可见输出由 brain 统一编排（防双重回复） |
| 渲染 | **游戏不参与渲染**：不 import PIL / 不起浏览器 / 不写 HTML；出图走桥接（`render_help` / `render_page` / `send_page`），只给数据 |
| 帮助 | `help.json` 自己声明结构（`groups` 分组 / 平铺 / `auto_groups:true` 请插件代劳），别名别和子分组抢词 |
| 命名 | 命令带游戏专属前缀，keywords.json 不得跨游戏重复/子串截胡 |
| 配置 | 配置/帮助/情感/关键词放 `data/config/{id}/` |
| 存档 | 用 `get_user_data` / `save_user_data`，不自建存储 |
| 图库 | 用户上传的图放 `games/neko_photo/data/`（**不进 git**，打包默认空） |
| 发现 | 游戏包放 `games/` 自动发现，零登记 |

> 适配前必读完整版 `docs/rules.md` + `docs/pitfalls.md`——踩过的坑都在里面。
