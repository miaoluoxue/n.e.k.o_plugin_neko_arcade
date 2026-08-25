"""图片桥接服务：插件主体提供的通用「自主发图」能力。

任何小游戏想要「发图给主人」时, 直接走这个桥接, 无需自己实现图库扫描、
分类管理、图片挑选、推送通道。用法(在 GameAdapter 子类里):

    await self.send_photo(user_id)                    # 随机发一张(优先本地图库)
    await self.send_photo(user_id, category="可爱")    # 指定分类
    await self.send_photo(user_id, auto=True)          # 后台自动发图(纯本地图库)

图片存储约定(与 neko_photo 一致):
    static/img/neko/<分类>/xxx.png|jpg|jpeg|webp|gif
每个子文件夹 = 一个分类。图片在 static 下, 宿主静态服务可直接 URL 访问,
推送走 static + markdown URL 通道(当前宿主唯一可靠的游戏图片显示通道)。
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
IMG_REL_ROOT = "img/neko"

# 动态渲染兜底: 与 ImageRenderer.render_neko_avatar 支持的心情一致
MOODS: List[str] = ["excitement", "curiosity", "proud", "upset", "sleepy", "calm"]
MOOD_LABELS: Dict[str, str] = {
    "excitement": "兴奋", "curiosity": "好奇", "proud": "得意",
    "upset": "委屈", "sleepy": "犯困", "calm": "淡定",
}

# 自动发图配文(后台/自主调用时用)
AUTO_LINES = [
    "喵~给你看看我现在的样子！",
    "刚拍了一张, 快看快看！",
    "这张怎么样？可爱吗喵？",
    "哼哼, 偷拍了一张自己~",
    "主人, 看镜头！咔嚓~",
    "发一张美美的自拍给你喵！",
    "今天心情不错, 送你一张照片~",
]


def _img_ext(path: str) -> str:
    """根据扩展名返回 mime。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    return "image/png"


class PhotoBridge:
    """图片桥接: 图库扫描 / 分类 / 挑图 / 推送 / 上传, 供所有游戏复用。

    生命周期由 ArcadeRuntime 管理; 通过 GameAdapter.bind_services(photo=...)
    注入到每个游戏, 游戏用 self.send_photo(...) 走桥接自主发图。
    """

    def __init__(self, plugin: Any, push: Any = None, img: Any = None) -> None:
        self.plugin = plugin
        self._push = push
        self._img = img
        self._img_cache: List[Dict[str, Any]] = []
        self._img_scan_ts = 0.0

    def bind(self, push: Any = None, img: Any = None) -> None:
        """绑定推送/渲染服务(由 runtime 注入)。"""
        if push is not None:
            self._push = push
        if img is not None:
            self._img = img

    # ── 图库根目录 ─────────────────────────

    def _img_root(self) -> Path:
        """图库根目录: static/img/neko/ (测试可注入 _local_scan_dir)。"""
        override = getattr(self, "_local_scan_dir", None)
        if override:
            return Path(override)
        return Path(self.plugin.config_dir) / "static" / "img" / "neko"

    def _static_rel(self, cat: str, fname: str) -> str:
        """构造 static 相对路径: img/neko/<分类>/<文件>。"""
        return f"{IMG_REL_ROOT}/{cat}/{fname}"

    # ── 图库扫描 / 分类 ─────────────────────

    def get_categories(self) -> List[str]:
        """返回图库分类名列表(子文件夹名)。"""
        root = self._img_root()
        if not root.is_dir():
            return []
        return sorted(d.name for d in root.iterdir() if d.is_dir())

    def scan_images(self) -> List[Dict[str, Any]]:
        """递归扫描 static/img/neko/ 下所有分类的图片(带 10s 缓存)。

        每张图带 category(分类) + url(可直接 markdown 引用的静态 URL)。
        """
        now = time.time()
        if self._img_cache and now - self._img_scan_ts < 10.0:
            return self._img_cache
        self._img_cache = []
        self._img_scan_ts = now
        root = self._img_root()
        if not root.is_dir():
            return self._img_cache
        for cat_dir in sorted(root.iterdir()):
            if not cat_dir.is_dir():
                continue
            cat = cat_dir.name
            for fname in sorted(os.listdir(str(cat_dir))):
                ext = os.path.splitext(fname)[1].lower()
                if ext not in IMG_EXTS:
                    continue
                path = cat_dir / fname
                try:
                    rel = self._static_rel(cat, fname)
                    url = self._push.static_url(rel) if self._push else ""
                    self._img_cache.append({
                        "bytes": path.read_bytes(),
                        "mime": _img_ext(str(path)),
                        "style": os.path.splitext(fname)[0],
                        "rarity": "rare",
                        "source": "local",
                        "category": cat,
                        "url": url,
                    })
                except OSError:
                    continue
        return self._img_cache

    # ── 挑图 ──────────────────────────────

    async def pick_photo(self, category: Optional[str] = None,
                         auto: bool = False) -> Optional[Dict[str, Any]]:
        """随机挑一张图。

        category 指定时只在该分类里挑(无图返回 None);
        auto=True(后台自动发图)时优先本地图库, 动态表情仅兜底;
        否则全库图片与动态渲染表情混合。
        """
        local = self.scan_images()
        if category:
            in_cat = [i for i in local if i.get("category") == category]
            return random.choice(in_cat) if in_cat else None
        if local:
            if auto:
                return random.choice(local)
            if random.random() < 0.5:
                return random.choice(local)
        avatar = await self._render_avatar_photo()
        if avatar:
            return avatar
        if local:
            return random.choice(local)
        return None

    async def _render_avatar_photo(self) -> Optional[Dict[str, Any]]:
        """用 PIL 渲染一张随机心情的猫娘头像(永不依赖外部资源)。"""
        if not self._img:
            return None
        mood = random.choice(MOODS)
        size = random.choice([160, 192, 224])
        data = self._img.render_neko_avatar(mood, size)
        if not data:
            return None
        return {"bytes": data, "mime": "image/png", "style": mood,
                "rarity": "common", "source": "avatar", "category": ""}

    # ── 发图(核心桥接) ─────────────────────

    async def send_photo(self, user_id: str, category: Optional[str] = None,
                         caption: str = "", auto: bool = False) -> Dict[str, Any]:
        """自主发一张图到聊天框(插件主体统一桥接)。

        返回结构化结果给游戏/调用方:
            ok: bool            是否成功
            style/category/rarity: 发的图信息(游戏可记图鉴)
            summary: 给 LLM 看的简短描述
            caption: 实际配文
        """
        photo = await self.pick_photo(category=category, auto=auto)
        if not photo:
            if category:
                return {"ok": False, "summary": f"分类「{category}」里还没有图喵",
                        "error": "category_empty"}
            return {"ok": False, "summary": "呜……图片都跑丢了, 稍后再试试喵",
                    "error": "no_photo"}

        text = caption or self._caption_for(photo)
        url = photo.get("url", "")
        if url and self._push:
            await self._push.text_with_image_url(text, url)
        elif self._push:
            await self._push.text_with_image(text, photo["bytes"],
                                             photo.get("mime", "image/png"))

        style = photo.get("style", "")
        label = MOOD_LABELS.get(style, style)
        cat = photo.get("category", "")
        rarity = photo.get("rarity", "common")
        if rarity == "rare":
            summary = f"给主人发了一张珍藏照片({label})喵~"
        elif cat:
            summary = f"给主人发了张「{cat}」分类的照片喵~"
        else:
            summary = f"给主人发了张{label}的照片喵~"
        return {"ok": True, "summary": summary, "style": style,
                "category": cat, "rarity": rarity, "caption": text,
                "photo": photo}

    async def send_auto(self, user_id: str) -> Dict[str, Any]:
        """后台自动发图: 只发本地图库(有真实照片感), 配文随机。"""
        return await self.send_photo(user_id, auto=True,
                                     caption=random.choice(AUTO_LINES))

    async def pick_for_delivery(self, category: Optional[str] = None,
                                caption: str = "") -> Dict[str, Any]:
        """取一张图(不推送), 供游戏 handle_action 返回 images 数据交 brain 推送。

        这是"游戏适配插件"的输出契约: 游戏不直接 push, 只通过桥接取图,
        brain 统一编排推送。返回 {ok, image: {text, bytes, mime, url}, style, ...}。
        """
        photo = await self.pick_photo(category=category)
        if not photo:
            if category:
                return {"ok": False, "error": "category_empty",
                        "summary": f"分类「{category}」里还没有图喵"}
            return {"ok": False, "error": "no_photo",
                    "summary": "呜……图片都跑丢了, 稍后再试试喵"}
        text = caption or self._caption_for(photo)
        image: Dict[str, Any] = {"text": text}
        if photo.get("url"):
            image["url"] = photo["url"]
        else:
            image["bytes"] = photo["bytes"]
            image["mime"] = photo.get("mime", "image/png")
        return {"ok": True, "image": image,
                "style": photo.get("style", ""),
                "category": photo.get("category", ""),
                "rarity": photo.get("rarity", "common"),
                "summary": self._delivery_summary(photo)}

    def _delivery_summary(self, photo: Dict[str, Any]) -> str:
        """取图后给 LLM 的简短描述(与用户已见配文不同, 避免复述)。"""
        label = MOOD_LABELS.get(photo.get("style", ""), photo.get("style", "照片"))
        cat = photo.get("category", "")
        rarity = photo.get("rarity", "common")
        if rarity == "rare":
            return f"给主人发了一张珍藏照片({label})喵~"
        if cat:
            return f"给主人发了张「{cat}」分类的照片喵~"
        return f"给主人发了张{label}的照片喵~"

    def _caption_for(self, photo: Dict[str, Any]) -> str:
        """生成发图配文。"""
        style = photo.get("style", "")
        label = MOOD_LABELS.get(style, style)
        cat = photo.get("category", "")
        if photo.get("rarity") == "rare":
            return f"✨ 珍藏款喵！这张 {label} 照片可是稀有图鉴哦~"
        if photo.get("source") == "local":
            if cat:
                return f"看~「{cat}」分类里的这张 {label} 可爱吗喵？"
            return f"看~这张 {label} 照片可爱吗喵？"
        return f"喵呜~今天的心情是「{label}」, 给你看看！"

    # ── 用户上传 ──────────────────────────

    async def upload_photo(self, user_id: str, name: str,
                           data_b64: str = "", data_bytes: bytes = b"",
                           category: str = "默认") -> Dict[str, Any]:
        """保存用户上传的图片到 static/img/neko/<分类>/。

        支持 base64 字符串或原始 bytes。分类不存在会自动创建。
        保存后立即刷新扫描缓存。
        """
        import base64

        raw = data_bytes
        if not raw and data_b64:
            try:
                raw = base64.b64decode(data_b64)
            except Exception:
                return {"ok": False, "message": "图片数据解码失败喵"}
        if not raw:
            return {"ok": False, "message": "没有收到图片数据喵"}

        ext = os.path.splitext(name or "")[1].lower()
        if ext not in IMG_EXTS:
            return {"ok": False, "message": "只支持 png/jpg/webp/gif 图片喵"}
        if len(raw) > 8 * 1024 * 1024:
            return {"ok": False, "message": "图片超过 8MB 了喵"}

        cat = self._sanitize_category(category) or "默认"
        base = self._img_root() / cat
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "message": f"无法创建分类目录: {exc}"}

        stem = os.path.splitext(os.path.basename(name or "photo"))[0] or "photo"
        fname = f"{int(time.time() * 1000)}_{stem}{ext}"
        path = base / fname
        try:
            path.write_bytes(raw)
        except OSError as exc:
            return {"ok": False, "message": f"保存失败: {exc}"}

        # 立即刷新缓存
        self._img_cache = []
        self._img_scan_ts = 0.0
        return {"ok": True, "message": f"图片已存入「{cat}」分类喵({fname})",
                "filename": fname, "category": cat,
                "count": len(self.scan_images())}

    @staticmethod
    def _sanitize_category(category: str) -> str:
        """清洗分类名: 去掉路径分隔符等危险字符。"""
        cat = (category or "").strip().replace("/", "").replace("\\", "").replace("..", "")
        return cat[:20]
