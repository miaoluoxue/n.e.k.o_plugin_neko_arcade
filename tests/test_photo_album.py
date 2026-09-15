# -*- coding: utf-8 -*-
"""喵图相册测试: 图库落私有存储(不进包) + 空库也能出图 + 发图频率闸门。

覆盖用户报的三个问题:
1. 指令发不出图 → 空图库时必须有兜底图(插件自带)
2. 无法自动发图 → 同上, 且频率交给 LLM, 插件只封顶
3. 上传会进包 → 图库根目录必须在宿主私有存储, 仓库/包里默认为空

注: 本环境禁止系统临时目录(pytest tmp_path 会 PermissionError),
    因此用仓库内的工作目录并在每个用例开始时重建。
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Dict

import pytest
from plugin.plugins.neko_arcade.adapters.photo_bridge import PhotoBridge

REPO = Path(__file__).resolve().parents[1]
WORK = Path(__file__).resolve().parent / "_photo_tmp"


class StubPlugin:
    """模拟宿主: config_dir=代码目录(只读), data_path=私有存储(可写)。"""

    def __init__(self, storage: Path):
        self.config_dir = str(REPO)
        self._storage = storage

    def data_path(self, *parts: str) -> Path:
        p = self._storage.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


class StubPush:
    def __init__(self):
        self.calls = []

    async def text_with_image(self, text: str, image_bytes: bytes,
                              mime: str = "image/png") -> None:
        self.calls.append({"text": text, "bytes": len(image_bytes), "mime": mime})

    async def text(self, msg: str, **_: Any) -> None:
        self.calls.append({"text": msg})


def _fresh() -> Path:
    """干净的仓库内工作目录。"""
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    return WORK


def _storage(tmp: Path) -> Path:
    d = tmp / "storage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bridge(tmp: Path, push=None) -> PhotoBridge:
    return PhotoBridge(StubPlugin(_storage(tmp)), push=push or StubPush(), img=None)


# ── 图库位置: 私有存储, 不进包 ─────────────────────────
def test_gallery_root_is_storage_not_code_dir() -> None:
    tmp = _fresh()
    bridge = _bridge(tmp)
    root = bridge._img_root()
    assert str(root).startswith(str(_storage(tmp)))
    # 关键: 不落在打包树 games/ 里(否则上传会被打进插件包)
    assert not str(root).startswith(str(REPO / "games")), "图库不能落在 games/ 打包树里"


def test_upload_lands_in_storage() -> None:
    tmp = _fresh()
    bridge = _bridge(tmp)
    data = b"\x89PNG" + b"0" * 64
    r = asyncio.run(bridge.upload_photo("u1", "cat.png", data_bytes=data,
                                        category="我的猫图"))
    assert r["ok"], r
    saved = _storage(tmp) / "neko_photo" / "我的猫图" / "cat.png"
    assert saved.is_file() and saved.read_bytes() == data
    assert bridge.get_categories() == ["我的猫图"]


def test_package_ships_empty_gallery() -> None:
    """包里不得带相册图库(打包发出去默认空图库)。

    注: games/<game>/data 下的**游戏素材**(如塔罗 88 张牌面)不属此列, 只守相册图库。
    """
    gallery = REPO / "games" / "neko_photo" / "data"
    offenders = []
    if gallery.is_dir():
        for f in gallery.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg",
                                                    ".webp", ".gif"):
                offenders.append(str(f.relative_to(REPO)))
    assert not offenders, f"包内出现相册图库图片: {offenders}"


def test_legacy_code_dir_gallery_is_migrated() -> None:
    """老版本把图库放在代码目录: 首次使用迁移到私有存储, 代码目录不再是工作库。"""
    tmp = _fresh()
    legacy = REPO / "games" / "neko_photo" / "data" / "_migration_probe"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "old.png").write_bytes(b"\x89PNG" + b"1" * 32)
    try:
        bridge = _bridge(tmp)
        assert asyncio.run(bridge.send_photo("u1"))["ok"]
        moved = _storage(tmp) / "neko_photo" / "_migration_probe" / "old.png"
        assert moved.is_file(), "老图库应被迁移到私有存储"
    finally:
        shutil.rmtree(REPO / "games" / "neko_photo" / "data", ignore_errors=True)


# ── 空图库也必须能出图 ─────────────────────────────────
def test_empty_gallery_still_delivers_default_photo() -> None:
    """空图库(默认状态)下「发图」类指令必须仍有图可发——用插件自带图兜底。"""
    tmp = _fresh()
    bridge = _bridge(tmp)
    assert bridge.scan_images() == []
    r = asyncio.run(bridge.pick_for_delivery())
    assert r["ok"], r
    assert r["image"]["bytes"], "兜底图不能是空的"
    assert r["image"]["mime"].startswith("image/")


def test_empty_gallery_auto_send_works() -> None:
    """无指令的自动发图同样不能因为空库而失败。"""
    tmp = _fresh()
    push = StubPush()
    bridge = _bridge(tmp, push=push)
    r = asyncio.run(bridge.send_auto("u1"))
    assert r["ok"], r
    assert push.calls and push.calls[0]["bytes"] > 0


def test_category_without_images_reports_empty() -> None:
    """指定分类为空时如实报错(不拿默认图冒充), 但整库为空仍能兜底出图。"""
    tmp = _fresh()
    bridge = _bridge(tmp)
    r = asyncio.run(bridge.send_photo("u1", category="不存在的分类"))
    assert not r["ok"] and r.get("error") == "category_empty"
    # 全库为空(不指定分类) → 兜底出图
    assert asyncio.run(bridge.send_photo("u1"))["ok"]


# ── 发图频率闸门(LLM 想发就发, 插件封顶) ────────────────
class _Store:
    def __init__(self):
        self.data: Dict[str, Any] = {}

    async def store_get_user(self, gid, uid, default=None):
        return self.data.get(uid, default)

    async def store_save_user(self, gid, uid, data):
        self.data[uid] = data


class _GamePlugin(StubPlugin):
    def __init__(self, storage: Path):
        super().__init__(storage)
        self.store = _Store()

    async def store_get_user(self, gid, uid, default=None):
        return await self.store.store_get_user(gid, uid, default)

    async def store_save_user(self, gid, uid, data):
        await self.store.store_save_user(gid, uid, data)


def _photo_game(tmp: Path, config: Dict[str, Any] | None = None):
    from plugin.plugins.neko_arcade.games.neko_photo.game import NekoPhotoGame
    plugin = _GamePlugin(_storage(tmp))
    game = NekoPhotoGame(plugin)
    game._config = config or {"send_min_interval": 90, "send_max_per_hour": 6,
                              "max_photos_per_day": 5}
    game._help, game._keywords, game._emotion_templates = {}, [], {}
    game.bind_services(push=StubPush(), photo=PhotoBridge(plugin, push=StubPush()))
    return game


def test_send_gate_blocks_too_frequent() -> None:
    game = _photo_game(_fresh())

    async def run():
        first = await game._check_send_gate("u1")
        assert first["ok"]
        await game._mark_sent("u1")
        return await game._check_send_gate("u1")

    second = asyncio.run(run())
    assert not second["ok"], "刚发过就该被拦"
    assert second["retry_after"] > 0 and "秒后" in second["summary"]


def test_send_gate_hourly_cap() -> None:
    game = _photo_game(_fresh(), {"send_min_interval": 0, "send_max_per_hour": 2,
                                  "max_photos_per_day": 50})

    async def run():
        for _ in range(2):
            assert (await game._check_send_gate("u1"))["ok"]
            await game._mark_sent("u1")
        return await game._check_send_gate("u1")

    third = asyncio.run(run())
    assert not third["ok"] and "小时" in third["summary"]


def test_send_gate_daily_cap() -> None:
    game = _photo_game(_fresh(), {"send_min_interval": 0, "send_max_per_hour": 99,
                                  "max_photos_per_day": 1})

    async def run():
        import time as _t
        data = game._new_data()
        data["day"] = _t.strftime("%Y-%m-%d")
        data["today_count"] = 1
        data["last_send_ts"] = 0
        await game.save_user_data("u1", data)
        return await game._check_send_gate("u1")

    capped = asyncio.run(run())
    assert not capped["ok"] and "今天" in capped["summary"]


def test_default_config_is_llm_driven() -> None:
    """默认不开定时刷图(频率交给 LLM), 但闸门参数要有默认值。"""
    cfg = json.loads((REPO / "data" / "config" / "neko_photo" / "config.json")
                     .read_text(encoding="utf-8"))
    assert cfg.get("auto_send_enabled") is False
    assert cfg.get("send_max_per_hour", 0) > 0
    assert cfg.get("send_min_interval", 0) > 0


@pytest.mark.parametrize("bad", ["../evil", "a/b", "a\\b", ".."])
def test_category_is_sanitized(bad: str) -> None:
    tmp = _fresh()
    bridge = _bridge(tmp)
    r = asyncio.run(bridge.upload_photo("u1", "x.png", data_bytes=b"\x89PNG" + b"0" * 8,
                                        category=bad))
    assert r["ok"], r
    saved = list((_storage(tmp) / "neko_photo").rglob("x.png"))
    assert saved and _storage(tmp) in saved[0].parents
    assert ".." not in str(saved[0])
