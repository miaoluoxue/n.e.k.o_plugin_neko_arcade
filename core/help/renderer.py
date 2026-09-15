# -*- coding: utf-8 -*-
"""统一帮助渲染器: 帮助文档 + 主题 → HTML → PNG(官方 UI Kit 视觉)。

用法(由 core/brain.py 调用)::

    doc = normalize_help(raw, game_id, game_name)
    page = resolve_topic(doc, topic)
    pages = await renderer.render(doc, page)      # List[bytes]

- 视觉**不在这里发明**: token 与类名全部取自官方 UI Kit(themes.py), 换主题只换 token。
- 浏览器渲染复用 ``ImageRenderer.render_html``(Playwright + 插件内置 Chromium);
  没有浏览器时返回 ``[]``, 调用方继续降级(PIL / 纯文本)。
- 结果按 HTML 哈希缓存, 同内容不重复起浏览器。
"""
from __future__ import annotations

import hashlib
import html as _html
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from .contract import Group, HelpDoc, Page
from .themes import AssetResolver, icon_glyph, theme_tokens, theme_veil

log = logging.getLogger(__name__)

WIDTH = 740
CATALOG_PER_PAGE = 12          # 目录页每页分组数(12 组 = 6 行两列, 一页放得下)
GROUP_CMD_PER_PAGE = 22        # 分组页每页指令数
FLAT_CMD_PER_PAGE = 26         # 扁平(老格式)帮助每页指令数

BASE_CSS = """
* { box-sizing:border-box; margin:0; padding:0; }
body { width:%(width)dpx; position:relative; color:var(--text); background:var(--bg);
  font-family:Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI",
              "Microsoft YaHei", sans-serif; }
.bgart { position:absolute; inset:0; z-index:0;
  background-size:cover; background-position:76%% 16%%; }
.veil { position:absolute; inset:0; z-index:1; background:%(veil)s; }
.page { position:relative; z-index:2; padding:18px; display:grid; gap:12px; }
.head { display:flex; align-items:center; gap:12px; }
.ava { width:58px; height:58px; border-radius:50%%; overflow:hidden; flex:0 0 58px;
  border:2px solid rgba(64,158,255,0.55); box-shadow:var(--shadow-soft); background:#fff; }
.ava img { width:100%%; height:100%%; object-fit:cover; object-position:center 16%%;
  display:block; }
.page-title { font-size:22px; font-weight:760; letter-spacing:0.2px; }
.page-sub { margin-top:4px; color:var(--muted); font-size:13px; line-height:1.55; }
.roleTag { display:inline-flex; align-items:center; gap:5px; margin-top:6px;
  border:1px solid rgba(20,184,166,0.3); background:rgba(20,184,166,0.1); color:#0f766e;
  border-radius:999px; padding:2px 9px; font-size:11.5px; font-weight:650; }
.stats { display:flex; gap:12px; }
.stat { flex:1; padding:9px 12px; border:1px solid var(--border);
  border-radius:var(--radius-lg); background:var(--surface); display:grid; gap:3px; }
.stat .l { color:var(--muted); font-size:12px; font-weight:650; }
.stat .v { color:var(--text); font-size:18px; font-weight:780; line-height:1.1; }
.card { border:1px solid var(--border); border-radius:var(--radius-md);
  background:var(--surface); overflow:hidden; }
.card-hd { padding:11px 13px 0; display:flex; align-items:center; gap:8px; }
.card-tt { font-size:15px; font-weight:720; }
.badge { display:inline-flex; align-items:center; gap:6px; width:fit-content; margin-left:auto;
  padding:3px 9px; border-radius:999px; border:1px solid var(--border);
  background:var(--surface-strong); color:var(--muted); font-size:12px; font-weight:650; }
.badge::before { content:''; width:7px; height:7px; border-radius:999px;
  background:var(--primary); }
.badge[data-tone="info"]::before { background:var(--info); }
.card-bd { padding:10px 13px 13px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; }
.chip { border:1px solid var(--border); border-radius:999px; padding:3px 9px; font-size:12px;
  background:var(--surface-strong); }
.chip.more { color:var(--muted); }
.tip { border:1px solid rgba(230,162,60,0.28); border-radius:14px; padding:11px 12px;
  background:rgba(230,162,60,0.1); line-height:1.65; font-size:12.5px; display:flex;
  gap:9px; align-items:flex-start; }
.tip img { width:30px; height:30px; border-radius:50%%; flex:0 0 30px; object-fit:cover;
  object-position:center 16%%; border:1px solid rgba(230,162,60,0.45); }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.table { width:100%%; border-collapse:separate; border-spacing:0; overflow:hidden;
  border:1px solid var(--border); border-radius:var(--radius-lg); }
.table th, .table td { padding:10px 12px; text-align:left; font-size:13px;
  border-bottom:1px solid var(--border); }
.table th { color:var(--muted); font-size:12px; font-weight:650;
  background:rgba(148,163,184,0.08); }
.table tr:last-child td { border-bottom:none; }
.table td.k { color:var(--primary); font-weight:650; white-space:nowrap; width:36%%; }
.table td.d { color:var(--text); }
.divider { height:1px; background:var(--border); }
.foot { display:flex; align-items:center; justify-content:center; gap:8px;
  color:var(--muted); font-size:12px; }
.foot img { width:16px; height:16px; border-radius:4px; }
.emoji { font-family:'Segoe UI Emoji','Apple Color Emoji',sans-serif; font-size:15px; }
.mascot { position:absolute; right:8px; bottom:2px; width:112px; z-index:3;
  filter:drop-shadow(0 6px 16px rgba(15,23,42,0.28)); }
/* 版式块 */
.steps { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
.step { border:1px solid var(--border); border-radius:var(--radius-md); padding:8px 10px;
  background:var(--surface-strong); text-align:center; min-width:92px; }
.step .s1 { display:block; font-size:12.5px; font-weight:650; margin-top:4px; }
.step .s2 { font-size:10.5px; color:var(--muted); }
.arrow { color:var(--muted); font-size:15px; }
.slots { display:flex; gap:10px; }
.slot { flex:1; border:1px dashed var(--border); border-radius:var(--radius-md);
  padding:10px 8px; text-align:center; background:var(--surface-strong); }
.slot .sn { display:block; font-size:12.5px; font-weight:650; margin-top:5px; }
.slot .sd { font-size:10.5px; color:var(--muted); }
.bag { display:grid; gap:6px; }
.cell { aspect-ratio:1/1; border:1px solid var(--border); border-radius:var(--radius-sm);
  background:var(--surface-strong); display:flex; align-items:center;
  justify-content:center; font-size:14px; }
.cell.on { border-color:var(--primary); background:rgba(64,158,255,0.10); }
.block-tt { font-size:12.5px; font-weight:650; color:var(--muted); margin-bottom:8px; }
"""


def _esc(text: Any) -> str:
    return _html.escape(str(text if text is not None else ""), quote=True)


class HelpRenderer:
    """帮助图渲染器(统一入口)。"""

    def __init__(self, img_renderer: Any, code_dir: str = "", cache_dir: str = ""):
        self.img = img_renderer
        self.assets = AssetResolver(code_dir, cache_dir)
        self.cache_dir = cache_dir or ""
        self._icon_override: Dict[str, str] = {}

    # ── 对外主入口 ────────────────────────────────────────
    async def render(self, doc: HelpDoc, page: Page, theme: str = "light",
                     use_cache: bool = True) -> List[bytes]:
        """渲染一个寻址结果(可能多页)。无浏览器/失败时返回空列表。"""
        theme = self._pick_theme(doc, theme)
        payloads = self._build_pages(doc, page)
        out: List[bytes] = []
        for html in payloads:
            png = await self._render_one(html, theme, use_cache)
            if not png:
                return []            # 任一处失败即整体降级, 由调用方兜底
            out.append(png)
        return out

    async def render_custom(self, spec: Dict[str, Any], theme: str = "",
                            use_cache: bool = True) -> List[bytes]:
        """渲染一个"自定义页面"(游戏只给数据: 标题/副标题/版式块/指令表)。

        这是渲染桥接的底座: 游戏永远不写 HTML/CSS, 只描述要展示什么。
        """
        theme_name = self._pick_theme(HelpDoc(theme=str(spec.get("theme") or "")), theme)
        png = await self._render_one(self._spec_page(spec), theme_name, use_cache)
        return [png] if png else []

    def build_html(self, doc: HelpDoc, page: Page, theme: str = "light",
                   page_index: int = 0) -> str:
        """公开给测试/预览用: 直接拿到某一页的 HTML。"""
        payloads = self._build_pages(doc, page)
        if not payloads:
            return ""
        idx = max(0, min(page_index, len(payloads) - 1))
        return self._wrap(payloads[idx], self._pick_theme(doc, theme))

    # ── 内部: 主题与渲染 ──────────────────────────────────
    @staticmethod
    def _pick_theme(doc: HelpDoc, theme: str) -> str:
        name = (theme or "").strip().lower()
        if name in ("light", "dark"):
            return name
        doc_theme = (getattr(doc, "theme", "") or "").strip().lower()
        return doc_theme if doc_theme in ("light", "dark") else "light"

    async def _render_one(self, body: str, theme: str, use_cache: bool) -> Optional[bytes]:
        html = self._wrap(body, theme)
        if not html:
            return None
        key = hashlib.md5(html.encode("utf-8")).hexdigest()[:20]
        cache_path = os.path.join(self.cache_dir, f"help-{key}.png") if self.cache_dir else ""
        if use_cache and cache_path and os.path.isfile(cache_path):
            try:
                with open(cache_path, "rb") as fh:
                    data = fh.read()
                if data:
                    return data
            except OSError:
                pass
        render_html = getattr(self.img, "render_html", None)
        if not callable(render_html):
            return None
        png = await render_html(html, "", WIDTH, 800, selector="body")
        if not png:
            return None
        if cache_path:
            try:
                with open(cache_path, "wb") as fh:
                    fh.write(png)
            except OSError:
                pass
        return png

    # ── 内部: 页面组装 ────────────────────────────────────
    def _wrap(self, body: str, theme: str) -> str:
        css = BASE_CSS % {"width": WIDTH, "veil": theme_veil(theme)}
        bg = self.assets.uri("bg")
        mascot = self.assets.uri("mascot")
        bg_layer = (f'<div class="bgart" style="background-image:url({bg})"></div>'
                    if bg else "")
        mascot_tag = f'<img class="mascot" src="{mascot}" alt="">' if mascot else ""
        return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                f'<style>:root {{{theme_tokens(theme)}}}\n{css}</style></head><body>'
                f'{bg_layer}<div class="veil"></div><div class="page">{body}</div>'
                f'{mascot_tag}</body></html>')

    def _icon(self, name: str) -> str:
        return _esc(icon_glyph(name, self._icon_override))

    def _head(self, title: str, subtitle: str = "", role: str = "") -> str:
        avatar = self.assets.uri("avatar")
        ava = f'<div class="ava"><img src="{avatar}" alt=""></div>' if avatar else ""
        role_tag = f'<div class="roleTag">🐾 {_esc(role)}</div>' if role else ""
        sub = f'<div class="page-sub">{_esc(subtitle)}</div>' if subtitle else ""
        return (f'<div class="head">{ava}<div><div class="page-title">{_esc(title)}</div>'
                f'{sub}{role_tag}</div></div>')

    def _foot(self) -> str:
        brand = self.assets.uri("brand")
        img = f'<img src="{brand}" alt="">' if brand else ""
        return (f'<div class="divider"></div><div class="foot">{img}'
                f'N.E.K.O 猫娘小游戏 · 帮助</div>')

    def _tip(self, body: str) -> str:
        avatar = self.assets.uri("avatar")
        img = f'<img src="{avatar}" alt="">' if avatar else ""
        return f'<div class="tip">{img}<span>{body}</span></div>'

    def _chips(self, items: Sequence[str], limit: int = 3, total: int = 0) -> str:
        shown = list(items[:limit])
        html = "".join(f'<span class="chip">{_esc(i)}</span>' for i in shown)
        extra = (total or len(items)) - len(shown)
        if extra > 0:
            html += f'<span class="chip more">+{extra}</span>'
        return f'<div class="chips">{html}</div>'

    def _table(self, rows: Sequence[Sequence[str]]) -> str:
        body = "".join(
            f'<tr><td class="k">{_esc(r[0])}</td><td class="d">{_esc(r[1])}</td></tr>'
            for r in rows if len(r) >= 2)
        return (f'<table class="table"><tr><th>指令</th><th>说明</th></tr>{body}</table>')

    def _block(self, block: Dict[str, Any]) -> str:
        """版式块: chips / table / steps / flow / slots / bag / text。"""
        kind = str(block.get("type") or "text").lower()
        title = str(block.get("title") or "")
        head = f'<div class="block-tt">{_esc(title)}</div>' if title else ""
        items = block.get("items") or []
        if kind in ("chips", "list"):
            names = [str(i.get("name") if isinstance(i, dict) else i) for i in items]
            return head + self._chips(names, limit=block.get("limit") or 12)
        if kind == "table":
            return head + self._table(block.get("rows") or [])
        if kind in ("steps", "flow"):
            parts = []
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                if i:
                    parts.append('<span class="arrow">→</span>')
                parts.append(
                    '<div class="step"><span class="emoji">%s</span>'
                    '<span class="s1">%s</span><span class="s2">%s</span></div>'
                    % (self._icon(str(it.get("icon") or "")), _esc(it.get("name") or ""),
                       _esc(it.get("sub") or "")))
            return head + f'<div class="steps">{"".join(parts)}</div>'
        if kind == "slots":
            parts = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                parts.append(
                    '<div class="slot"><span class="emoji">%s</span>'
                    '<span class="sn">%s</span><span class="sd">%s</span></div>'
                    % (self._icon(str(it.get("icon") or "")), _esc(it.get("name") or ""),
                       _esc(it.get("sub") or "")))
            return head + f'<div class="slots">{"".join(parts)}</div>'
        if kind == "bag":
            cols = int(block.get("cols") or 8)
            filled = set(block.get("filled") or [])
            icons = block.get("icons") or []
            cells = []
            for i in range(int(block.get("cells") or (cols * 2))):
                glyph = ""
                if i < len(icons) and icons[i]:
                    glyph = f'<span class="emoji">{self._icon(str(icons[i]))}</span>'
                cells.append('<div class="cell%s">%s</div>'
                             % (" on" if i in filled else "", glyph))
            return (head + f'<div class="bag" style="grid-template-columns:repeat({cols},1fr)">'
                    f'{"".join(cells)}</div>')
        return head + f'<div class="page-sub">{_esc(block.get("text") or "")}</div>'

    # ── 页面类型 ─────────────────────────────────────────
    def _group_card(self, group: Group, index: int) -> str:
        names = [c.cmd for c in group.commands]
        badge_tone = ' data-tone="info"' if index % 2 else ""
        return (f'<div class="card"><div class="card-hd"><span class="emoji">'
                f'{self._icon(group.icon)}</span><span class="card-tt">{_esc(group.name)}'
                f'</span><span class="badge"{badge_tone}>{len(names)} 条</span></div>'
                f'<div class="card-bd">{self._chips(names, limit=3)}</div></div>')

    def _catalog(self, doc: HelpDoc, page: Page) -> List[str]:
        groups = doc.groups
        chunks = [groups[i:i + CATALOG_PER_PAGE]
                  for i in range(0, len(groups), CATALOG_PER_PAGE)] or [groups]
        out: List[str] = []
        for pi, chunk in enumerate(chunks):
            cards = [self._group_card(g, i) for i, g in enumerate(chunk)]
            pairs = "".join(f'<div class="grid2">{"".join(cards[k:k + 2])}</div>'
                            for k in range(0, len(cards), 2))
            subtitle = doc.subtitle or (doc.text and doc.text[:60]) or ""
            if len(chunks) > 1:
                subtitle = f"{subtitle} （{pi + 1}/{len(chunks)}）".strip()
            tip_body = ('想看某类指令的详细用法 → 发「<b>帮助 功能名</b>」，'
                        '例如「修仙帮助 战斗」；直接发指令名也可以。')
            if page.miss and pi == 0:
                guess = "、".join(page.suggestions) if page.suggestions else "上面的功能分组"
                tip_body = f'没找到「{_esc(getattr(page, "topic", "") or "")}」这个分类喵，' \
                           f'你是想看 <b>{_esc(guess)}</b> 吗？'
            body = (self._head(doc.title or doc.game_name, subtitle, "喵喵陪你玩")
                    + f'<div class="stats"><div class="stat"><div class="l">功能分组</div>'
                      f'<div class="v">{len(groups)}</div></div>'
                      f'<div class="stat"><div class="l">指令总数</div>'
                      f'<div class="v">{doc.command_count}</div></div></div>'
                    + self._tip(tip_body) + pairs + self._foot())
            out.append(body)
        return out

    def _group_page(self, doc: HelpDoc, page: Page) -> List[str]:
        group = page.group
        if group is None:
            return self._catalog(doc, page)
        blocks = "".join(f'<div class="card"><div class="card-bd">{self._block(b)}</div></div>'
                         for b in group.blocks)
        cmds = group.commands
        chunks = [cmds[i:i + GROUP_CMD_PER_PAGE]
                  for i in range(0, len(cmds), GROUP_CMD_PER_PAGE)] or [cmds]
        out: List[str] = []
        for pi, chunk in enumerate(chunks):
            subtitle = doc.title or doc.game_name
            if len(chunks) > 1:
                subtitle += f" · {pi + 1}/{len(chunks)}"
            rows = []
            for c in chunk:
                desc = c.desc or ""
                if c.aliases:
                    desc += f"（别名: {'/'.join(c.aliases[:3])}）"
                rows.append([c.cmd, desc])
            others = [g.name for g in doc.groups if g.id != group.id][:6]
            tail = (self._tip("其他功能：" + "、".join(f"<b>{_esc(o)}</b>" for o in others)
                              + "　→　发「帮助 功能名」") if others else "")
            head = (self._head(group.name, subtitle, "喵喵陪你玩") if pi == 0
                    else self._head(f"{group.name}（续）", subtitle))
            body = (head + (blocks if pi == 0 else "")
                    + f'<div class="card"><div class="card-bd">{self._table(rows)}</div></div>'
                    + tail + self._foot())
            out.append(body)
        return out

    def _command_page(self, doc: HelpDoc, page: Page) -> List[str]:
        cmd = page.command
        if cmd is None:
            return self._catalog(doc, page)
        group = page.group
        usage = cmd.cmd + (" " + " ".join(cmd.params) if cmd.params else "")
        rows: List[Sequence[str]] = [["用法", usage]]
        if cmd.desc:
            rows.append(["说明", cmd.desc])
        if cmd.params:
            rows.append(["参数", " / ".join(cmd.params)])
        if group is not None:
            rows.append(["所属", group.name])
        related: List[str] = []
        if group is not None:
            related = [c.cmd for c in group.commands if c.cmd != cmd.cmd]
        related = list(dict.fromkeys([*cmd.related, *related]))
        tip = (f'直接发「<b>{_esc(cmd.cmd)}</b>」就能用喵'
               + ("；换装备名即可，例如「装备 青锋剑」" if cmd.params else ""))
        body = (self._head(cmd.cmd, (group.name if group else "") or (doc.title or ""),
                           "喵喵陪你玩")
                + f'<div class="card"><div class="card-bd">{self._table(rows)}</div></div>'
                + (f'<div class="card"><div class="card-bd">'
                   f'<div class="block-tt">同一分类的其他指令</div>'
                   f'{self._chips(related, limit=8)}</div></div>' if related else "")
                + self._tip(tip) + self._foot())
        return [body]

    def _flat(self, doc: HelpDoc, page: Page) -> List[str]:
        cmds = doc.flat
        chunks = [cmds[i:i + FLAT_CMD_PER_PAGE]
                  for i in range(0, len(cmds), FLAT_CMD_PER_PAGE)] or [cmds]
        out: List[str] = []
        for pi, chunk in enumerate(chunks):
            subtitle = (doc.text or "")[:78]
            if len(chunks) > 1:
                subtitle = f"{subtitle} （{pi + 1}/{len(chunks)}）".strip()
            body = (self._head(doc.title or doc.game_name, subtitle, "喵喵陪你玩")
                    + f'<div class="card"><div class="card-bd">'
                      f'{self._table([[c.cmd, c.desc] for c in chunk])}</div></div>'
                    + self._foot())
            out.append(body)
        return out

    def _build_pages(self, doc: HelpDoc, page: Page) -> List[str]:
        if doc.groups:
            if page.kind == "group":
                return self._group_page(doc, page)
            if page.kind == "command":
                return self._command_page(doc, page)
            return self._catalog(doc, page)
        return self._flat(doc, page)

    def _spec_page(self, spec: Dict[str, Any]) -> str:
        """自定义页面骨架: 标题 + 版式块 + 指令表(全部由游戏给的数据驱动)。"""
        blocks = "".join(
            f'<div class="card"><div class="card-bd">{self._block(b)}</div></div>'
            for b in (spec.get("blocks") or []) if isinstance(b, dict))
        rows = [r for r in (spec.get("rows") or []) if isinstance(r, (list, tuple))
                and len(r) >= 2]
        parsed = [c for c in (_as_command(x) for x in (spec.get("commands") or []))
                  if c is not None]
        cmds = (f'<div class="card"><div class="card-bd">'
                f'{self._table([[c.cmd, c.desc] for c in parsed])}</div></div>'
                if parsed else "")
        table = (f'<div class="card"><div class="card-bd">{self._table(rows)}</div></div>'
                 if rows else "")
        chips = spec.get("chips")
        chip_card = (f'<div class="card"><div class="card-bd">'
                     f'{self._chips([str(c) for c in chips], limit=len(chips))}</div></div>'
                     if chips else "")
        tip = self._tip(str(spec["tip"])) if spec.get("tip") else ""
        return (self._head(str(spec.get("title") or ""), str(spec.get("subtitle") or ""),
                           str(spec.get("role") or ""))
                + blocks + table + cmds + chip_card + tip + self._foot())


def _as_command(raw: Any) -> Optional[Any]:
    """把桥接传入的指令数据(二元组或对象)转成内部 Command。"""
    from .contract import _parse_command  # 局部导入避免循环依赖
    return _parse_command(raw)
