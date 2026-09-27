# 📜 版本历史（开发者）

> 完整迭代记录，供开发者查阅。插件市场门面 README.md 只保留最新版本。

## v0.5.7 (2026-09)

**一句话主题**：新增棋类对弈（六种棋），并把「陪伴 / LLM / 渲染」三条通道彻底收归插件本体。

### 新增

- **棋类对弈（`boardgame`）** — 一个游戏入口挂六种棋：五子棋(15×15)、黑白棋(8×8)、
  中国象棋、四子棋(7×6)、围棋(9×9)、国际象棋。规则各自独立在 `boards/<棋种>.py`，
  `game.py` 只做「模式路由 + 状态搬运 + 调本体陪伴」。支持三档难度（随手/正常/认真）、
  悔棋（默认 3 次）、认输、换棋种、棋盘重发；**落子后由渲染桥的 `board` 版式块出棋盘图**
  （交叉点 / 格心两种摆法 + 坐标标签 + 最后一手标记 + 星位）。六种棋的落子报法按棋种区分
  （坐标 / 起点终点 / 列号）。
- **统一陪伴层（`core/companion.py`）** — 台词、心情、闲聊回应、局中主动搭话全部归本体：
  台词三级降级（LRU 缓存 → LLM → 游戏 `emotion.json` 模板）；心情由事件键驱动
  （highlight/lose/chat…）；`should_nudge()` 把静默阈值、次数上限、节流策略收在一处，
  游戏只报"我在等他动作 + 上次活动时间"。台词模型可用句尾 JSON（`{"mood":…}`）控制情绪延续。
- **LLM 网关（`core/llm_gateway.py`）** — 游戏要"生成内容"（出题 / 剧情 / 文案）时只声明
  场景名，网关统一做缓存（场景 + 内容哈希 + TTL）、失败兜底与按场景用量统计；
  调用失败不抛异常，退回 fallback。
- **人格热更新（`core/persona.py`）** — 猫娘人设从宿主角色配置读取，带文件签名
  （mtime/size）+ TTL 缓存：宿主换角色后台词立即跟着换；新增 `apply_persona()`
  供热重载，换角色时清空台词缓存，不再挂着上一位的口吻。
- **出厂内容随升级刷新（`core/config_manager.py`）** — `help / keywords / emotion`
  属于出厂内容，升级时按字节比对同步进用户数据目录；`config.json` 是用户设置，
  永不覆盖。修掉旧实现"用户目录只要存在过 `config/`，升级带来的新 help/keywords
  就永远不补 → 帮助图只剩一句文本"的问题。
- **契约测试** — `tests/test_adapter_contract.py` 静态扫描 `games/**`：出现
  `self.push_text*` / `self.send_page(` / `self.render_card(` / `self._llm` / `_QUIPS =`
  即判违规（白名单只留图库本体 neko_photo）；`tests/test_keyword_style.py` 审计
  keywords 跨游戏重复/子串截胡与单字词；另新增 `test_all_games_smoke` /
  `test_game_switch` / `test_persona_refresh` / `test_llm_gateway` / `test_boardgame`。

### 修复

- **陪伴层心情映射写错 id（静默失效）** — `MOOD_BY_EVENT` 原写成 `excited/curious`，
  而 `persona.EMOTION_POOL` 的规范 id 是 `excitement/curiosity`；`Mood.trigger`
  对未知 id 静默忽略，表现为"赢了猫娘也不兴奋、提示词里的心情永远是平静"。
  现改为规范 id + 别名归一（`excited` / `兴奋` → `excitement`），`apply_control`
  同时加异常护栏；新增 `tests/test_companion.py`（14 项）覆盖映射、三级降级与搭话策略。
- **推送被宿主拒绝却当成成功** — `push_message` 对超限内联图**不抛异常**，只回一条
  `rejected: reason=payload_too_large` 回执；`push_sender` 现在把 rejected 回执当失败，
  继续走下一档通道（原生 → 上传 → markdown），不再出现"以为发了其实没图"。
- **棋类关键词覆盖不全** — 补齐 `西洋棋 / 翻转棋 / 奥赛罗 / 五连 / 四连棋 / 对弈` 等别名。
- **棋类帮助内容不全** — `help.json` 的 `text` 原只写 4 种棋，现补齐六种；
  分组由"两种棋硬凑一对"改为 `选棋种 / 六种棋 / 对局操作`，并补 `title` / `subtitle`。

### 变更 / 升级注意

- 版本号 0.5.6 → **0.5.7**；`plugin.toml` 描述与关键词补齐棋类（现共 11 款游戏）。
- 游戏侧**不得**自绘图片、不得直接摸 LLM provider（`self._llm`）、不得内嵌台词表
  `_QUIPS`：统一走渲染桥 / LLM 网关 / `emotion.json`（契约测试会拦）。
- 老用户升级后 `help / keywords / emotion` 会自动刷新，个人 `config.json` 不受影响。
- 塔罗牌面图仍为原图（包体约 252 MB），压缩优化留待后续版本。

## v0.5.6 (2026-08)

**统一帮助系统**：帮助图的渲染、配色、排版、图标全部收归插件端，游戏只写
`help.json`（写 `groups` 分组 / 只写 `commands` 平铺 / `auto_groups:true` 请插件代劳）。
- 三层寻址：游戏 → 分组（可两级）→ 指令，每层都能用别名直达；
  未命中回目录页并给近似建议
- 指令专属图：`「我的纳戒」`这类查看指令挂 `blocks` 即出自己那张图（背包格/装备栏）
- 版式块：`chips` / `table`(可配表头) / `cards` / `stats`(进度条) / `steps`·`flow` /
  `slots` / `bag` / `text`
- 视觉 = 官方 UI Kit（token 与类名逐字取自 `hosted/ui-kit/styles.css`），
  素材（猫娘图标/竖版立绘/徽章）内联进图，离线可用；目录页按内容估高自适应分页
- 砍掉两张游戏侧自绘：`remake` 的 400 行 PIL 人生总结图迁移为
  `cards + stats + table` 数据（删除 drawer.py、图片素材、像素字体）

**渲染桥接（游戏不参与渲染）**：新增 `self.render_help / render_page / send_page`，
游戏只给数据；`render_html` 标记废弃。新增 CI 守卫测试，游戏侧出现 PIL / 浏览器 /
HTML 模板即失败。

**出图走宿主内置浏览器**：优先宿主自己的浏览器查找函数与
`PLAYWRIGHT_BROWSERS_PATH`，启动链先"不指定 exe"，失败才退显式路径；全链失败再降级。

**喵图相册修复**：
- 图库定位回到 `games/neko_photo/data/`（用户可直接丢图），该目录**不进 git**
  （打包默认空图库）；只读安装位置自动退化到宿主私有存储
- 空图库也必有一张图可发：兜底链 = 本地图库 → 动态猫娘表情 → 插件自带 `assets/icon.png`
  （修掉"发指令不出图 / 无法自动发图"）
- 发图频率改由 LLM 决定，插件封顶：最短间隔 / 每小时 / 每日三道闸门；
  被拦返回 `limited:true + retry_after`；定时刷图默认关闭
- 修掉 `0` 被 `cfg or default` 吞掉的配置 bug

**其它**：聊天图片优先内联原始字节（帮助图不再被 JPEG 重编码降质）；9 个游戏补齐
各自的功能分组声明；11 个 md 文档同步更新。

## v0.5.5 (2026-08)

修复旧宿主（未合并 #2835，如 0.9.1）图片显示成代码：旧宿主前端
ReactMarkdown 只开 remark-gfm/rehype-katex（无 rehype-raw），`<img>` HTML
不渲染。PushSender 兜底通道改为标准 markdown 图片语法 `![alt](url)`；
落盘前大图缩放 ≤720px 转 JPEG，防窄窗溢出。新增缩放回归测试。

## v0.5.4 (2026-08)

文档体系重构：README 改为插件市场门面（普通用户视角，架构/入口表/版本表移出）；
版本历史移入 CHANGELOG.md；面板 UI 只展示 docs/ui/ 简版文档（使用指南改猫娘语气，
纯用户视角）；完整开发者文档（rules/pitfalls/ARCHITECTURE/development）保留 GitHub
不进发布包；UI 素材图（logo/yui）从图库目录归位 static/img/，防发图串台；
删除废弃 docs/guide.md。

## v0.5.3 (2026-08)

命令命名规范化：每个游戏指令带专属前缀（猫猫状态/修仙状态/鱼店/海龟汤状态/市场…），
消除跨游戏裸词串台；新增串台回归测试（tests/test_command_naming.py）。

## v0.5.2 (2026-08)

帮助图/图片推送走原生图片通道(#2835)：聊天窗原生图片气泡适配窗口，旧宿主自动回退
markdown 防溢出。

## v0.5.1 (2026-08)

修复游戏指令路由瘫痪（宿主路由选错入口，只留 play_game 可见）；play_game 改用
@llm_tool 静态注册（MC 模式）；文档纠正 LLM 路由结论。

## v0.5.0 (2026-08)

新增 塔罗牌（78张+9牌阵,3主题）、历史上的今天、猫猫进化路、喵图相册（聊天随机发图）
四个小游戏；消息监听+状态锚（LLM不脱节）、后台发图活动窗口门控、弱指令路由
（再来一局回最近游戏）、推送带 target_lanlan（多角色不丢消息）、避坑文档 pitfalls.md。

## v0.4.0 (2026-08)

新增 俄罗斯轮盘（猫粮赌局）、人生重开（天赋/属性重开）两个小游戏；帮助图/卡片精美化
（渐变+圆角,去猫娘头像遮挡）；游戏状态锚（LLM 脱节时自动注入当前游戏指令）；
README 适配插件市场样式。

## v0.3.0 (2026-08)

新增诸天修仙小游戏（境界/炼体、突破渡劫、采集炼丹炼器、拍卖行、仙宠、宗门、
道侣师徒，猫娘全程陪玩）；runtime/LLM 优化；ruff 检查修复。

## v0.2.0 (2026-08)

玻璃拟态面板（开始/停止/状态标签）；schema 驱动中文配置抽屉；海龟汤、猜硬币；
渲染桥；品牌页脚；i18n。

## v0.1.0 (2026-07)

初版：聚合框架（自动发现/注册/分发/存档）+ 钓鱼示例游戏。
