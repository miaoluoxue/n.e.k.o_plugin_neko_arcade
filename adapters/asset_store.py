"""外部资源库：首次运行从 Git 源拉取大素材（塔罗牌面），落本地缓存并校验 sha256。

**为什么要有它**（2026-10 决定）：
塔罗牌面原本 251 MB PNG 全打进插件包 —— 包体 252 MB，而插件代码本体只有 ~1 MB
（同期别的插件 0.4–6.7 MB）。现在包里只留代码 + manifest（几十 KB），
素材**首次运行按需下载**到 `plugin.cache_path("assets")`，之后一直走本地缓存。

设计要点：
- **manifest 双份**：随插件发布的 `assets.manifest.json` 是信任根，固定每个文件
  的 sha256/大小；只有没有内置清单时才读取远端清单兜底。
- **多源回退**：`base_urls` 依次尝试；每个源失败自动换下一个。
- **完整性**：下载后核对 `sha256` + 字节数，不匹配就丢弃重试（绝不用坏图）。
- **不阻塞宿主**：下载在线程池里跑（`asyncio.to_thread`），并发上限默认 3；
  失败只记日志 + 返回 None，绝不抛异常。
- **幂等/可续**：已有文件（大小一致）直接跳过，重启不会重下。
- 路径里的中文要 URL 编码（`urllib.parse.quote`）——GitHub/Gitee 都吃编码后的路径。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

#: 缺省资源源（按顺序尝试；第一个成功即用）
DEFAULT_BASE_URLS: Sequence[str] = (
    # 主源：Gitee（国内直连快，2026-10 实测可拉；131 张 WebP / 23.8 MB）
    "https://gitee.com/maoyvna/taluopai/raw/master/",
    # 兜底：GitHub 插件仓库 assets 分支（同一份素材，已实测可拉）
    "https://raw.githubusercontent.com/miaoluoxue/n.e.k.o_plugin_neko_arcade/assets/",
)

MANIFEST_NAME = "manifest.json"
DEFAULT_TIMEOUT = 30.0
DEFAULT_CONCURRENCY = 3
DEFAULT_RETRIES = 2
DEFAULT_MAX_ASSET_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
DEFAULT_CACHE_LIMIT_MB = 128.0


class _HttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    """只允许 HTTPS 重定向，并在跟随前验证目标地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise urllib.error.HTTPError(
                target, code, "redirect target is not HTTPS", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, target)

#: 分类 → 插件代码目录里的对应子目录。
#: 用途：**本地优先** —— 素材若仍随包（图标/面板图这类小件），就不必再下载；
#: 只有真正的"大块头"（塔罗牌面）才会走网络。新增素材分类时在这里登记
#: （工具侧对应 `tools/build_asset_manifest.py --category 分类=目录`）。
DEFAULT_LOCAL_MAP: Dict[str, str] = {
    "tarot": "games/tarot/data",
    "ui": "assets",
    "panel": "static/img",
}


class AssetStore:
    """外部素材的"下载一次、之后走缓存"管理器。"""

    def __init__(self, plugin: Any, *, cache_dir: Optional[str] = None,
                 base_urls: Optional[Sequence[str]] = None,
                 bundled_manifest: Optional[str] = None,
                 local_map: Optional[Dict[str, str]] = None,
                 enabled: bool = True, logger: Any = None,
                 cache_limit_mb: float = DEFAULT_CACHE_LIMIT_MB) -> None:
        self.plugin = plugin
        self.log = logger
        self.enabled = bool(enabled)
        configured: List[str] = []
        for raw in base_urls or ():
            base = self._normalize_base_url(str(raw or ""))
            if base and base not in configured:
                configured.append(base)
        if configured:
            self.base_urls = configured
        else:
            self.base_urls = [
                base for raw in DEFAULT_BASE_URLS
                if (base := self._normalize_base_url(raw))
            ]
        self._cache_root = Path(cache_dir) if cache_dir else self._default_cache_dir()
        try:
            cache_mb = float(cache_limit_mb)
        except (TypeError, ValueError):
            cache_mb = DEFAULT_CACHE_LIMIT_MB
        self.cache_limit_bytes = (
            int(max(0.0, min(cache_mb, 4096.0)) * 1024 * 1024)
        )
        self._bundled_manifest = Path(bundled_manifest) if bundled_manifest else None
        self._local_map: Dict[str, str] = dict(local_map or DEFAULT_LOCAL_MAP)
        self._manifest: Optional[Dict[str, Any]] = None
        self._index: Dict[str, Dict[str, Any]] = {}
        # path -> (size, mtime_ns)：首次/变更后完整校验，命中时只做廉价 stat。
        self._verified: Dict[Path, tuple[int, int]] = {}
        self._sem = asyncio.Semaphore(DEFAULT_CONCURRENCY)
        self._prefetch_task: Optional[asyncio.Task] = None
        self.stats: Dict[str, int] = {"hit": 0, "download": 0, "fail": 0, "skip": 0,
                                      "local": 0}

    # ── 路径 / 缓存 ────────────────────────────────────

    def _default_cache_dir(self) -> Path:
        """缓存目录：优先宿主给的 cache_path，其次代码目录旁 cache/。"""
        cache_path = getattr(self.plugin, "cache_path", None)
        if callable(cache_path):
            try:
                return Path(str(cache_path("assets")))
            except Exception as exc:  # noqa: BLE001 - 老宿主没有 cache_path
                self._log("debug", "cache_path 不可用，退化为代码目录旁 cache/: %s", exc)
        code_dir = getattr(self.plugin, "plugin_dir", None) or "."
        return Path(str(code_dir)) / "cache" / "assets"

    @property
    def cache_root(self) -> Path:
        return self._cache_root

    @staticmethod
    def _normalize_base_url(raw: str) -> str:
        """素材源只接受无凭据、无查询参数的 HTTPS 根地址。"""
        parsed = urllib.parse.urlsplit(str(raw or "").strip())
        if (parsed.scheme.lower() != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            return ""
        path = parsed.path.rstrip("/") + "/"
        return urllib.parse.urlunsplit(("https", parsed.netloc, path, "", ""))

    @staticmethod
    def _safe_rel(rel: str) -> str:
        """规范化清单/缓存相对路径，拒绝绝对路径和任何 .. 穿越。"""
        raw = str(rel or "").replace("\\", "/").strip()
        if not raw or raw.startswith("/"):
            raise ValueError("asset path must be relative")
        parts = raw.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("asset path contains an unsafe segment")
        if any(re.search(r'[<>:"|?*\x00-\x1f]', part)
               or part.endswith((".", " ")) for part in parts):
            raise ValueError("asset path contains Windows-reserved characters")
        normalized = "/".join(parts)
        if len(normalized) > 500 or "\x00" in normalized:
            raise ValueError("asset path is invalid")
        return normalized

    def local_path(self, rel: str) -> Path:
        """某资源在本地缓存里的路径（不保证已存在）。"""
        return self._cache_root.joinpath(*self._safe_rel(rel).split("/"))

    def local_mirror(self, rel: str) -> Optional[Path]:
        """同一份素材若仍随插件包（代码目录），返回那个本地文件路径。

        "本地优先"：图标/面板图这类小件继续随包 → 不下载、离线可用；
        只有代码目录里没有的大块头（塔罗牌面）才走网络。
        扩展名会尝试 .png/.jpg/.webp（本地可能是 png，素材源统一 webp）。
        """
        try:
            rel = self._safe_rel(rel)
        except ValueError:
            return None
        category, _, tail = rel.partition("/")
        sub = self._local_map.get(category)
        code_dir = getattr(self.plugin, "plugin_dir", None)
        if not sub or not tail or not code_dir:
            return None
        base = Path(str(code_dir)) / sub / tail
        code_root = Path(str(code_dir)).resolve()
        for cand in (base, base.with_suffix(".png"), base.with_suffix(".jpg"),
                     base.with_suffix(".jpeg")):
            try:
                safe = cand.resolve().is_relative_to(code_root)
            except OSError:
                safe = False
            if safe and cand.is_file():
                return cand
        return None

    # ── manifest ──────────────────────────────────────

    async def manifest(self) -> Dict[str, Any]:
        """取 manifest：内置清单是信任根，远端只作无内置时的兜底。"""
        if self._manifest is not None:
            return self._manifest
        data = self._load_bundled_manifest()
        if not data:
            data = await asyncio.to_thread(self._load_remote_manifest)
        self._manifest = data or {}
        index: Dict[str, Dict[str, Any]] = {}
        for item in self._manifest.get("files", []):
            if not isinstance(item, dict):
                continue
            try:
                rel = self._safe_rel(str(item.get("path") or ""))
                size = int(item.get("size", -1))
                digest = str(item.get("sha256") or "").lower()
            except (TypeError, ValueError):
                self._log("warning", "忽略非法素材清单项: %r", item)
                continue
            if (not 0 < size <= DEFAULT_MAX_ASSET_BYTES
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                self._log("warning", "忽略缺少大小/哈希的素材清单项: %s", rel)
                continue
            index[rel] = {**item, "path": rel, "size": size, "sha256": digest}
        self._index = index
        if self._index:
            self._log("info", "资源清单就绪: %d 项, 共 %.1f MB (源: %s)",
                      len(self._index), self._manifest.get("total_size", 0) / 1024 / 1024,
                      self._manifest.get("_source", "remote/bundled"))
        return self._manifest

    def _load_remote_manifest(self) -> Optional[Dict[str, Any]]:
        for base in self.base_urls:
            blob = self._fetch(base + MANIFEST_NAME,
                               max_bytes=DEFAULT_MAX_MANIFEST_BYTES)
            if not blob:
                continue
            try:
                data = json.loads(blob.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._log("debug", "manifest 解析失败(%s): %s", base, exc)
                continue
            if isinstance(data, dict) and data.get("files"):
                data["_source"] = base
                return data
        return None

    def _load_bundled_manifest(self) -> Optional[Dict[str, Any]]:
        if not self._bundled_manifest or not self._bundled_manifest.is_file():
            self._log("debug", "没有内置 manifest 兜底")
            return None
        try:
            data = json.loads(self._bundled_manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["_source"] = "bundled"
                return data
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "内置 manifest 读取失败: %s", exc)
        return None

    # ── 取资源 ────────────────────────────────────────

    async def ensure(self, rel: str) -> Optional[Path]:
        """确保某个资源在本地（已有直接用，没有就下载）。

        返回本地路径；不可用（未启用/下载失败/清单里没有）返回 None，
        调用方应回退占位图，不要因为缺图报错。
        """
        if not self.enabled or not rel:
            return None
        try:
            rel = self._safe_rel(rel)
        except ValueError as exc:
            self._log("warning", "拒绝不安全素材路径 %r: %s", rel, exc)
            self.stats["fail"] += 1
            return None
        # ① 随包的本地素材优先（小件不下载、离线可用）
        local = self.local_mirror(rel)
        if local is not None:
            self.stats["local"] += 1
            return local
        target = self.local_path(rel)
        entry = (await self.manifest()).get("files") and self._index.get(rel)
        if entry and await asyncio.to_thread(self._file_matches, target, entry):
            self.stats["hit"] += 1
            return target
        if not entry:
            self._log("warning", "资源不在有效清单里: %s", rel)
            self.stats["fail"] += 1
            return None
        async with self._sem:
            ok = await asyncio.to_thread(self._download_one, rel, entry)
        if ok:
            self.stats["download"] += 1
            return target
        self.stats["fail"] += 1
        return None

    def _download_one(self, rel: str, entry: Dict[str, Any]) -> bool:
        """下载单个资源并校验（同步，跑在线程池里）。"""
        want_size = int(entry.get("size", -1))
        want_sha = str(entry.get("sha256") or "").lower()
        if not 0 < want_size <= DEFAULT_MAX_ASSET_BYTES:
            self._log("warning", "资源大小无效，拒绝下载: %s", rel)
            return False
        if re.fullmatch(r"[0-9a-f]{64}", want_sha) is None:
            self._log("warning", "资源缺少有效 sha256，拒绝下载: %s", rel)
            return False
        quoted = urllib.parse.quote(rel)
        for attempt in range(DEFAULT_RETRIES + 1):
            for base in self.base_urls:
                blob = self._fetch(base + quoted, timeout=DEFAULT_TIMEOUT,
                                   max_bytes=want_size)
                if not blob:
                    continue
                if len(blob) != want_size:
                    self._log("warning", "资源大小不符 %s: %d != %d", rel, len(blob), want_size)
                    continue
                if hashlib.sha256(blob).hexdigest() != want_sha:
                    self._log("warning", "资源 sha256 不符，丢弃: %s", rel)
                    continue
                try:
                    target = self.local_path(rel)
                    self._enforce_cache_limit(target, want_size)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(target.suffix + ".part")
                    tmp.write_bytes(blob)
                    os.replace(tmp, target)   # 原子替换，避免半截文件被当成缓存
                    self._record_verified(target)
                    return True
                except Exception as exc:  # noqa: BLE001
                    self._log("warning", "资源落盘失败 %s: %s", rel, exc)
                    return False
            if attempt < DEFAULT_RETRIES:
                time.sleep(0.6 * (attempt + 1))
        self._log("warning", "资源下载失败: %s", rel)
        return False

    def _enforce_cache_limit(self, protected: Path, incoming_size: int) -> None:
        """下载前按 LRU 清理旧缓存；单个素材本身超限时仍允许写入。"""
        limit = self.cache_limit_bytes
        if limit <= 0 or not self._cache_root.is_dir():
            return
        root = self._cache_root.resolve()
        protected_resolved = protected.resolve()
        files: List[tuple[float, int, Path]] = []
        for path in self._cache_root.rglob("*"):
            if not path.is_file() or path.suffix == ".part":
                continue
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or resolved == protected_resolved:
                    continue
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _, size, _ in files)
        for _, size, path in sorted(files):
            if total + incoming_size <= limit:
                break
            try:
                path.unlink()
                self._verified.pop(path, None)
                total -= size
            except OSError as exc:
                self._log("debug", "清理素材缓存失败 %s: %s", path, exc)
        if incoming_size > limit:
            self._log("warning", "单素材超过缓存上限 %d，仍保留当前文件: %s",
                      limit, protected)

    def _record_verified(self, path: Path) -> None:
        try:
            stat = path.stat()
            self._verified[path] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            self._verified.pop(path, None)

    def _file_matches(self, path: Path, entry: Dict[str, Any]) -> bool:
        """先比 size+mtime，变化时才重算 SHA-256。"""
        try:
            stat = path.stat()
            if stat.st_size != int(entry.get("size", -1)):
                return False
            fingerprint = (stat.st_size, stat.st_mtime_ns)
            if self._verified.get(path) == fingerprint:
                return True
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != str(entry.get("sha256") or "").lower():
                self._verified.pop(path, None)
                return False
            self._verified[path] = fingerprint
            return True
        except OSError:
            self._verified.pop(path, None)
            return False

    def _fetch(self, url: str, timeout: float = DEFAULT_TIMEOUT,
               max_bytes: int = DEFAULT_MAX_ASSET_BYTES) -> Optional[bytes]:
        """GET 一个 URL（尊重 HTTPS_PROXY 环境变量，失败返回 None）。"""
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            self._log("warning", "拒绝非 HTTPS 素材地址: %s", url)
            return None
        if max_bytes <= 0:
            return None
        try:
            opener = urllib.request.build_opener(
                _HttpsRedirectHandler(),
                urllib.request.ProxyHandler({
                    "https": os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "",
                    "http": os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or "",
                }))
            req = urllib.request.Request(url, headers={"User-Agent": "neko-arcade/1.0"})
            with opener.open(req, timeout=timeout) as resp:
                final = urllib.parse.urlsplit(resp.geturl())
                if final.scheme.lower() != "https" or not final.hostname:
                    self._log("warning", "素材下载重定向到了非 HTTPS 地址: %s", resp.geturl())
                    return None
                data = bytearray()
                while True:
                    chunk = resp.read(min(1024 * 1024, max_bytes + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        self._log("warning", "素材超过大小上限 %d: %s", max_bytes, url)
                        return None
                return bytes(data)
        except urllib.error.HTTPError as exc:
            self._log("debug", "HTTP %s: %s", exc.code, url)
        except Exception as exc:  # noqa: BLE001 - 断网/超时/DNS 都当"这个源不可用"
            self._log("debug", "拉取失败 %s: %s", url, exc)
        return None

    # ── 首次运行预热 ──────────────────────────────────

    async def prefetch_all(self, *, limit: int = 0) -> int:
        """把清单里的资源全量拉到本地（后台跑；返回成功下载数）。"""
        files = list((await self.manifest()).get("files", []))
        if limit:
            files = files[:limit]
        done = 0
        for item in files:
            rel = str(item.get("path") or "")
            if not rel:
                continue
            if await self.ensure(rel) is not None:
                done += 1
        self._log("info", "资源预热完成: %d/%d (命中 %d, 下载 %d, 失败 %d)",
                  done, len(files), self.stats["hit"], self.stats["download"], self.stats["fail"])
        return done

    def start_prefetch(self) -> None:
        """首次运行后台预热：不阻塞插件启动，可被 shutdown 取消。"""
        if not self.enabled:
            return
        if self._prefetch_task and not self._prefetch_task.done():
            return
        try:
            self._prefetch_task = asyncio.get_event_loop().create_task(self.prefetch_all())
        except Exception as exc:  # noqa: BLE001 - 没有事件循环就算了，按需下载兜住
            self._log("debug", "预热启动失败(按需下载兜底): %s", exc)

    async def stop(self) -> None:
        task = self._prefetch_task
        self._prefetch_task = None
        if task and not task.done():
            task.cancel()

    def snapshot(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "cache": str(self._cache_root),
                "sources": list(self.base_urls), "files": len(self._index),
                "cache_limit_mb": round(self.cache_limit_bytes / 1024 / 1024, 2),
                **self.stats}

    # ── 日志 ─────────────────────────────────────────

    def _log(self, level: str, msg: str, *args: Any) -> None:
        logger = self.log or getattr(self.plugin, "logger", None)
        if logger is None:
            return
        try:
            getattr(logger, level)(msg, *args)
        except Exception:  # noqa: BLE001 - 日志绝不能影响主流程
            pass


def build_store_from_cfg(plugin: Any, cfg: Dict[str, Any], *,
                         bundled_manifest: Optional[str] = None,
                         logger: Any = None) -> AssetStore:
    """按主配置构造 AssetStore（runtime 启动时调用）。"""
    base = str(cfg.get("asset_base_url") or "").strip()
    mirrors = cfg.get("asset_mirrors") or []
    if isinstance(mirrors, str):
        mirrors = [mirrors]
    urls: List[str] = [u for u in [base, *mirrors] if str(u).strip()]
    enabled = cfg.get("asset_auto_download", True)
    store = AssetStore(plugin, base_urls=urls or None, bundled_manifest=bundled_manifest,
                       enabled=bool(enabled), logger=logger,
                       cache_limit_mb=cfg.get("asset_cache_mb", DEFAULT_CACHE_LIMIT_MB))
    set_active_store(store)
    return store


#: 进程内"当前素材库"。渲染器/发图桥等功能模块按需取用（插件是单实例，
#: 省掉一层构造参数传递；没有 store 时它们照旧只读本地文件）。
_ACTIVE_STORE: Optional["AssetStore"] = None


def set_active_store(store: Optional["AssetStore"]) -> None:
    global _ACTIVE_STORE
    _ACTIVE_STORE = store


def active_store() -> Optional["AssetStore"]:
    return _ACTIVE_STORE
