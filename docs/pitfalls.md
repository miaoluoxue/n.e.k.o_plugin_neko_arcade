# 🐱 适配避坑手册（主插件做桥接，游戏只做玩法）

> **一句话原则：游戏适配插件，不是插件适配游戏。**
> 游戏只负责「玩法逻辑 + 返回结构化结果」，输出、状态、锚点、桥接、防重复、
> 防打断、后台发图……全部由主插件（brain）统一负责。适配新游戏前先读本文，
> 避免重复踩坑。

配合阅读：[rules.md](rules.md)（硬性契约）、[ARCHITECTURE.md](ARCHITECTURE.md)（运行机制）。

---

## 目录

1. [输出契约：游戏不参与推送（双重回复坑）](#1-输出契约游戏不参与推送双重回复坑)
2. [图片可见性：宿主通道的现实](#2-图片可见性宿主通道的现实)
3. [会话被打断：状态锚 + 消息监听](#3-会话被打断状态锚--消息监听)
4. [后台自动发图：活动窗口门控](#4-后台自动发图活动窗口门控)
5. [LLM 工具路由：别硬编码词表](#5-llm-工具路由别硬编码词表)
6. [消息监听：@message 真实存在](#6-消息监听message-真实存在)
7. [适配代码不得引用参考项目名](#7-适配代码不得引用参考项目名)
8. [测试坑：随机数据与固定断言](#8-测试坑随机数据与固定断言)
9. [开发环境坑：沙箱与临时目录](#9-开发环境坑沙箱与临时目录)
10. [适配自查清单](#10-适配自查清单)

---

## 1. 输出契约：游戏不参与推送（双重回复坑）

**坑**：旧架构里游戏自己 `push_text`/`push_text_image` 推送了用户可见内容，
又把同一文本放进返回的 `summary`。宿主把 `summary` 喂给主 LLM，
LLM 复述一遍 → 用户看到两条几乎一样的话（双重回复）。

**解法（已在插件内根治）**：

| 谁 | 做什么 |
|----|--------|
| 游戏 | 只返回 `{facts, outcome, message, images?}`，**绝不调用任何 push 方法** |
| brain | 统一推送：有 images → 推「配文/文本 + 图片」一次；无 images → 推 message 一次（通道1） |
| brain | 统一生成 summary（用情感模板 neko_text，**与用户已见内容不同**）→ 宿主 LLM 自然演绎不复述（通道2） |
| brain | 统一处理高光卡片、状态锚、防打断、发图桥接 |

**两条宿主通道（插件无法关闭，但 brain 统一管理）**：

```
通道1: brain push_message(ai_behavior="blind") → 宿主直接渲染到聊天窗(不进 LLM)
通道2: @plugin_entry 返回 Ok({summary}) → 宿主 task_result → 主 LLM 生成自然回复
```

- summary 一律用 `neko_text`（情感模板），**绝不**用用户已见的 `game_msg` 原文。
- 游戏返回的 `message`/`images` 由 brain 编排进通道1，游戏自身不推送。

**正确输出模式**：

| 场景 | 游戏返回 | brain 推送 |
|------|----------|-----------|
| 纯文本结果 | `{"message": "钓到一条鲲!"}` | 推 message 一次 |
| 带图片卡片 | `{"message": "...", "images": [{text, bytes, mime}]}` | 推图文一次 |
| 发图(neko_photo) | `pick_photo_for_delivery` 取图 → 返回 images | 推图文一次 |

**禁止**：

- ❌ `handle_action` 内调用 `push_text`/`push_text_image`/`push_text_image_url`/`push_help`
  （这些方法仅保留给 on_tick 后台提醒/历史兼容）
- ❌ 返回 `pushed` / `summary` 字段（已废除，brain 统一处理）
- ❌ summary 返回用户已见的原文

---

## 2. 图片可见性：宿主通道的现实

**坑**：想让游戏图片「用户看得见」——但宿主聊天窗口没有「用户可见图片」通道。

**现实（已验证官方插件全部如此）**：

- 所有官方插件发图片都用 `visibility=[], ai_behavior="respond"`（或 `read`）→
  **只有 AI 看得见（喂给 LLM 上下文），用户聊天窗看不见**。
- 用户可见图片只有一种方式：在 `chat + blind` 文本里用 markdown
  `![alt](url)` 引用外链图（如 lifekit 的做法）。
- 宿主没有「推一张图进用户聊天窗」的 API。上游 #2835/#2905 在推进，
  插件侧目前无法解决——这不是插件 bug，不要为它绕路。

**适配规则**：

- 游戏要展示图片：返回 `images: [{text, bytes, mime}]` 或 `{text, url}`，
  brain 会以图文一次推给 AI（vision 可见），这是标准做法。
- 若确需用户可见图片：走 markdown 外链（`chat + blind` + `![alt](url)`），
  且图片必须托管在可访问的 URL（不传 bytes）。
- 不要试图用 push 通道塞 bytes 让用户可见——做不到，且会刷屏。
- **图片中转**：PushSender.save_image 会把 bytes 中转落盘到
  `static/cards/` 并返回可访问 URL——游戏资源目录下的图（如
  `games/tarot/data/`）推送时经此中转，无需自己拼 URL。

## 2.5 音频/视频 parts 宿主不支持（坑）

**坑**：想推语音/音乐/视频给用户——但宿主**丢弃** audio/video parts。

**现实（源码核实）**：`character_runtime.py` 对 `type != "image"` 的 part
直接 warning 后 drop（`stream_audio` 是实时麦克风 PCM 管线，非通用文件
注入器；无 video API）。SDK schema 虽定义 `audio`/`video` part，消费端
未实现。

**适配规则**：

- **不要**推 audio part（`text_with_audio` 已从 PushSender 移除）。
- 语音播报正确姿势：宿主自动把 chat 通道文字 TTS 播放（官方
  short_tts_line 契约）——插件只需保证文本是 TTS-friendly 短句
  （`self.tts_note()` 标记），无需推音频数据。

---

## 3. 会话被打断：状态锚 + 消息监听

**坑**：游戏中宿主插入互动消息（或 LLM 长时间没调工具）后，LLM 会「脱节」——
用户说「继续」，LLM 当成普通聊天回应，而不是调用 `play_game` 传游戏指令，
游戏会话被「软打断」。

**解法（插件内，借鉴 MC 插件的 keep-going 思路）**：

1. **状态锚注入**：游戏进行中，brain 每 `game_anchor_interval`（默认 20s）
   向 LLM 上下文注入一条 read-only 文本（`visibility=[], ai_behavior="read"`）：
   「[游戏状态] 正在玩X，游戏没结束，引擎在等输入，可用指令：…」。
   - **只含当前游戏的指令**（前 6 条），绝不拼接全部游戏（防炸上下文）。
   - 每次 `handle_action`（LLM 调了工具）都会刷新锚节流 → 锚只在脱节时出现。
   - 间隔曾被调到 60s 导致脱节严重——20s 是验证过的平衡点。

2. **@message 即时刷新**：插件注册 `@message(id="chat_activity", source="chat")`，
   每次主人说话 → `brain.on_owner_speak()` → 立即把 `_last_anchor_ts` 归零，
   下一次 tick 立刻注入状态锚，让 LLM 尽快回到游戏。

3. **游戏中禁止 proactive 抢话**：猫娘不主动插话，避免把用户注意力拉走。

---

## 4. 后台自动发图：活动窗口门控

**坑**：后台游戏（如 neko_photo 标记 `background_tick=True`）在无人聊天时
也会持续自动发图 → 刷屏。另外 `background_tick` 的游戏 on_tick 只在
无会话时运行（有会话时当前游戏的 on_tick 已由主 tick 调用）。

**解法**：

- 游戏标记 `background_tick = True`，冷却逻辑在游戏内
  （如 `_next_auto_ts`，默认 60~180 秒一张，blind 推送不触发 AI 轮次）。
- brain 用**活动窗口门控**：`_last_activity_ts`（主人最后说话时间，由
  @message → on_owner_speak 刷新）距现在超过 `background_active_window`
  （默认 900s / 15 分钟）→ 后台自动发图整体暂停。
- 从未聊过天（`_last_activity_ts == 0`）也不发图。

**适配规则**：后台自动行为（发图/提醒）必须走 `on_tick` + `background_tick`，
不要用游戏内定时器，否则退出会话后仍在跑、且无法被统一门控。

---

## 5. LLM 工具路由：别硬编码词表

**坑**：主插件把用户输入交给 LLM 工具（`play_game`）判断，LLM 有裁量权——
所有官方插件都是同一机制，这是设计不是缺陷。曾出现 LLM 说
「要开始玩吗？」而不调 `play_game` 的「命令没有下文」问题。

**解法（不硬编码确认词表）**：

- **游戏侧**：`keywords.json` 写足触发词（如人生重开加「重启人生」）；
  `handle_action` 对确认/催促词（对的喵/对/是/没错/确定/确认）语义要接得住，
  返回对应的 outcome，让 LLM 知道下一步。
- **插件侧**：`play_game` 工具描述写清楚「传用户原话」；返回结果带
  `game_commands`（当前游戏指令）喂回 LLM。
- 交互语义（确认词/催促词）由 LLM 判断，主插件不硬编码词表；
  游戏自身通过 outcome 语义返回待选择提示。

---

## 6. 消息监听：@message 真实存在

**坑**：曾以为宿主没有「收到聊天消息」的监听器，导致插件无法感知主人说话。

**真相**：`@message(id="...", source="chat")` 存在（官方 neko_warthunder 等
插件在用）。本插件已注册：

```python
@message(id="chat_activity", source="chat")
async def on_chat_activity(self, **_):
    if self.rt and self.rt.brain:
        await self.rt.brain.on_owner_speak()
    return Ok({"status": "observed"})
```

**适配规则**：需要感知「主人说话」的游戏/功能，通过 brain 的
`on_owner_speak()` 挂钩（刷新活动窗口、状态锚），不要在游戏内自建监听。

---

## 6.5 推送必须带 target_lanlan（多角色会话，坑）

**坑**：`push_message` 不带 `target_lanlan` 时，宿主 `_get_session_manager("")`
返回 None；**多角色(多猫娘)会话时 fallback 也为空 → 推送被宿主直接丢弃**。
早期版本全部推送都没带 target_lanlan，单角色环境碰巧不丢（fallback 兜住），
多角色环境静默丢消息。

**真相**：官方 lifekit / neko_live 等插件推送一律带 `target_lanlan`（从
`ctx._current_lanlan` / 环境变量解析）。PushSender 已统一解析并带上：

```python
# 解析优先级: ctx._current_lanlan → ctx._host_ctx._current_lanlan →
# NEKO_TARGET_LANLAN / NEKO_LANLAN_NAME / NEKO_HER_NAME 环境变量
target = self._resolve_target_lanlan()
self.plugin.push_message(..., target_lanlan=target or None)
```

**适配规则**：任何走 PushSender / brain 的推送都不用手动传——它已统一处理。
游戏若绕过 PushSender 直接调 `plugin.push_message`，必须自己带 `target_lanlan`。

---

## 6.6 ctx 没有 user_id：不要按用户隔离（坑）

**坑**：SDK 的 `ctx` 没有 `user_id` 属性——`getattr(self.ctx, "user_id", "default")`
永远返回 `"default"`。若代码试图「按用户隔离存档/状态」，实际所有用户共享
同一份数据，且没有任何报错。

**真相**：宿主是单主人(单 master)会话模型，官方插件从不读 `ctx.user_id`。
本插件所有 `user_id` 参数实际恒为 `"default"`——这在当前架构下是正确的
（主人只有一个），不要为了实现「多用户隔离」去猜宿主 API。

**适配规则**：存档键用 `game:{id}:user:{uid}` 即可（uid 恒 default 无妨）；
**不要**依赖任何未在 SDK 文档中声明的 ctx 属性（如 user_id），拿到就当作
「该属性可能不存在」处理。

---

## 7. 适配代码不得引用参考项目名

**坑**：从别处移植/适配的游戏代码注释里写了原项目名
（nonebot-plugin-xxx、zhutianxiuxian 等），违反部署要求。

**规则**：适配的游戏代码（注释、docstring、README）一律**不得出现
参考项目名**。数据来源描述改为「data/ 下自带 JSON」这类中性表述。

---

## 8. 测试坑：随机数据与固定断言

**坑**：`games/remake` 的天赋池每次开局随机抽取，天赋的 `status` 字段会
**加成可分配属性总和**：`total = 20 + Σ(status)`（status ∈ [-20, +8]）。
测试硬编码「5 5 5 5」（和=20）→ total≠20 时随机失败（flaky）。

**解法**：

- 测试从游戏返回的 `facts` 里**动态读真实 total**（`kind == "prop_prompt"`
  的 fact 带 `total`），再构造合法拆分（每项≤10、和为 total）。
- 游戏侧示例提示同样必须跟真实 total 走（`_example_props(total)`），
  否则玩家照示例输入必被拒——这本身也是适配 bug（已修复）。

**通用规则**：凡依赖随机数据（随机天赋/随机牌/随机事件）的测试，
断言不要写死具体数值；要么从返回值动态取值，要么只断言结构/状态机。

---

## 9. 开发环境坑：沙箱与临时目录

- Windows 沙箱下 `tempfile.mkdtemp()` 不可写：构建脚本会 monkeypatch
  `tempfile.mkdtemp` 指向仓库内 `.tmp-build`，测试也把临时目录放到
  仓库内（`.tmp_xxx_test`）。
- pytest 缓存目录在沙箱锁定目录内会破坏收集：用
  `-p no:cacheprovider` + 显式测试文件列表运行。
- 同步到 live 插件目录需 `danger-full-access`，同步后要字节级校验；
  DSH 重启后插件变更才生效。

---

## 10. 适配自查清单

- [ ] 实现 `GameAdapter`，id/name/description/icon 齐全
- [ ] `handle_action` 返回 facts + outcome + message（+ images?），不认识指令返回 `outcome="unknown"`
- [ ] **不调用任何 push 方法**（`push_text`/`push_text_image`/`push_help` 仅 on_tick 保留）
- [ ] summary 用情感模板 neko_text，与用户已见内容不同（无双重回复）
- [ ] 需要展示图片 → 返回 `images`（bytes 或 url）交 brain 统一推
- [ ] 配置/帮助/情感/关键词放 `data/config/{id}/`，图片资源放 `games/{id}/data/`
- [ ] 后台自动行为走 `on_tick` + `background_tick=True`，冷却在游戏内
- [ ] 需要感知主人说话 → 依赖 brain.on_owner_speak()，不自建监听
- [ ] 随机数据的测试/提示动态取值，不写死固定数字
- [ ] 注释/文档不出现任何参考项目名
