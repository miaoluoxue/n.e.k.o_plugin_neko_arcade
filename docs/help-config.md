# 统一帮助系统 · 游戏侧配置契约

插件提供**一套**帮助系统：渲染、配色、排版、图标全部由插件端解决，
**游戏只需要提供数据**（`data/config/<game>/help.json`）。

- 视觉：官方 UI Kit（token 与组件类名逐字取自
  `frontend/plugin-manager/src/components/plugin/hosted/ui-kit/styles.css`）
- 出图：复用**宿主内置浏览器**（宿主自己的 Playwright + Launcher 注入的
  `PLAYWRIGHT_BROWSERS_PATH`；失败才退到显式路径与系统浏览器）
- 分层：`游戏 → 分组(group) → 指令(command)`，三层都能被直接寻址
- 兼容：不写 `groups` 的游戏行为与升级前完全一致（老的扁平单页帮助）

## 1. 最小可用（零改动）

```jsonc
// data/config/<game>/help.json
{
  "text": "🎣 钓鱼：每日抛竿，收藏稀有鱼获，鱼市换鱼蛋！",
  "commands": [["钓鱼", "抛竿钓鱼"], ["鱼缸", "查看收藏的鱼"]]
}
```

这就是全部要求：老格式照旧出图（扁平单页，自动分页）。**10 个既有游戏不动也能用。**

## 2. 升级为功能分组（推荐）

```jsonc
{
  "text": "...", "commands": [["仍然保留", "面板 get_game_config 会读它"]],
  "title": "诸天修仙",                       // 可省, 默认用游戏名
  "subtitle": "猫娘陪你修仙",
  "theme": "inherit",                        // inherit(跟随插件主题) | light | dark
  "banner": "auto",                          // auto | none | 图片相对路径
  "groups": [
    {
      "id": "bag",                           // 稳定 id（缓存键/日志用）
      "name": "装备与道具",                   // 显示名
      "icon": "bag",                         // 语义图标名（见第 4 节）
      "aliases": ["纳戒", "背包", "储物"],     // 用户可能怎么称呼它
      "blocks": [                            // 可选：分组页顶部的可视化块
        {"type": "slots", "title": "装备栏", "items": [
          {"icon": "sword", "name": "武器", "sub": "剑 / 刀 / 枪"}]},
        {"type": "bag", "title": "纳戒背包", "cols": 8, "cells": 16,
         "filled": [0, 3], "icons": ["pill", "", "", "gem"]}
      ],
      "commands": [
        ["我的纳戒", "查看背包"],                        // 老写法
        {"cmd": "装备 X", "desc": "穿戴装备",            // 对象写法(可选字段)
         "aliases": ["穿戴", "穿上"], "kind": "action",
         "params": ["装备名"], "related": ["卸下 X", "我的装备"]}
      ]
    }
  ]
}
```

## 3. 用户在聊天里怎么说

| 用户输入 | 命中 | 渲染 |
|---|---|---|
| 修仙帮助 / 怎么玩 | 游戏 | **目录页**（分组卡片 + 条数 + 代表指令） |
| 修仙帮助 纳戒 / 储物 | 分组（含别名） | **分组页**（版式块 + 指令表） |
| 修仙帮助 装备 X | 指令 | **指令页**（用法 / 参数 / 同组其他指令） |
| 修仙帮助 怎么装备 | 包含匹配 → 装备 X | 指令页 |
| 都没命中 | — | 目录页 + "你是想看 XX 吗？" |

寻址优先级：**精确指令 > 精确分组 > 分组名整词包含 > 指令包含 > 分组包含**。
帮助意图由插件统一拦截（`帮助/攻略/玩法/怎么玩/说明/指令表/help`），**游戏无需实现**。

## 4. 语义图标名

游戏写**语义**，主题决定画成什么（emoji / 主题图标集）：

```
bag sword sect pet furnace coin meditate daily heart star flame
weapon armor trinket herb pill book gem misc help
```

未收录的名字会退化成 🎯，不影响出图。

## 5. 版式块

| type | 用途 | 关键字段 |
|---|---|---|
| `chips` / `list` | 名称胶囊（默认） | `items`（字符串或 `{name}`）, `limit` |
| `table` | 表格（冷却/条件/兑换） | `title`, `rows: [[左, 右], ...]` |
| `steps` / `flow` | 阶梯 / 流程链（→） | `title`, `items: [{icon, name, sub}]` |
| `slots` | 槽位示意（装备栏） | `title`, `items: [{icon, name, sub}]` |
| `bag` | 格子（背包/仓库） | `title`, `cols`, `cells`, `filled: [下标]`, `icons: [语义名]` |
| `text` | 说明段 | `text` |

## 6. 指令对象字段

| 字段 | 说明 |
|---|---|
| `cmd` | 指令原文（含 `X`/`N` 占位符照旧） |
| `desc` | 一句话说明 |
| `aliases` | 别名（用户可能怎么打） |
| `kind` | `view`（只读面板类）/ `action`（带参数动作）；省略则按有无占位符自动判定 |
| `params` | 参数名列表（指令页「用法/参数」显示） |
| `related` | 关联指令（指令页「同一分类的其他指令」） |

## 7. 不想手写分组？

`"auto_groups": true` 时插件按命名/关键词把指令表自动聚成若干功能组
（装备/战斗/宗门/仙宠/生活/市集/修行/日常/猫娘/诸天/魔道 + 其他），
拿到可用目录后再按需改名与补别名。

## 8. 主题

- 默认跟随插件主配置：`data/main/config.json` → `{"help": {"theme": "light" | "dark"}}`
- 单游戏可覆盖：`help.json` 的 `theme` 字段
- 素材（猫娘头像 / `yui竖` 背景 / 立绘 / 徽章）全部来自插件自身 `assets/`、
  `static/img/`，以 data URI 内联进图，**离线可用且随插件目录迁移不丢图**
- 渲染结果按 HTML 哈希缓存在插件私有缓存目录，同内容不重复起浏览器
