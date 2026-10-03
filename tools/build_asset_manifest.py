"""压缩塔罗资源 → 生成可直接上传 Gitee 的目录 + manifest.json。

源：neko_arcade/games/tarot/data/<theme>/<sub>/<name>.png（251 MB PNG）
出：E:\\pythonxx\\tkry\\tarot-assets\\tarot\\<theme>\\<sub>\\<name>.webp + manifest.json
    同时给出 948px（原尺寸）与 720px 两个方案，默认导出 948px。
"""
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image

SRC = Path(r"E:\pythonxx\tkry\git同步\neko_arcade\games\tarot\data")
DST = Path(r"E:\pythonxx\tkry\tarot-assets")
QUALITY = 82
MAX_SIDE = int(sys.argv[1]) if len(sys.argv) > 1 else 0   # 0 = 保持原尺寸

DST.mkdir(parents=True, exist_ok=True)
files = sorted(p for p in SRC.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
entries, total_src, total_out = [], 0, 0

for p in files:
    rel = p.relative_to(SRC).as_posix()
    out = DST / "tarot" / Path(rel).with_suffix(".webp")
    out.parent.mkdir(parents=True, exist_ok=True)
    total_src += p.stat().st_size
    with Image.open(p) as im:
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        if MAX_SIDE and max(im.size) > MAX_SIDE:
            ratio = MAX_SIDE / max(im.size)
            im = im.resize((max(1, int(im.width * ratio)), max(1, int(im.height * ratio))),
                           Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=QUALITY, method=6)
    data = buf.getvalue()
    out.write_bytes(data)
    total_out += len(data)
    entries.append({"path": f"tarot/{Path(rel).with_suffix('.webp').as_posix()}",
                    "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})

manifest = {
    "version": "1",
    "note": "塔罗牌面资源：插件首次运行按 path 下载到用户缓存目录并校验 sha256，缺图回退内置占位图。",
    "count": len(entries),
    "total_size": total_out,
    "files": entries,
}
(DST / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
mb = lambda n: round(n / 1024 / 1024, 1)  # noqa: E731
print(f"导出 {len(entries)} 张  (最长边 {'原尺寸' if not MAX_SIDE else str(MAX_SIDE) + 'px'}, WebP q{QUALITY})")
print(f"原图 {mb(total_src)} MB  →  导出 {mb(total_out)} MB  (省 {100 - total_out * 100 // total_src}%)")
print("目录:", DST)
print("样例:", entries[0]["path"], entries[0]["sha256"][:12], f"{entries[0]['size'] // 1024} KB")
