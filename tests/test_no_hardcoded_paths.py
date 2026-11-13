"""运行时代码不得写死机器盘符、用户主目录或绝对安装路径。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_ABSOLUTE_PATH = re.compile(
    r"""(?ix)
    (?:(?<![a-z])[a-z]:[\\/])             # Windows drive path
    |(?:(?:^|[\s"'=])/(?:home|users|applications)/)  # fixed filesystem roots
    |(?:\\\\[a-z0-9_.-]+\\share)          # UNC path
    """
)


def test_runtime_sources_have_no_machine_specific_paths():
    sources = [
        ROOT / "__init__.py",
        ROOT / "plugin.toml",
        *sorted((ROOT / "core").rglob("*.py")),
        *sorted((ROOT / "adapters").rglob("*.py")),
        *sorted((ROOT / "games").rglob("*.py")),
        *sorted((ROOT / "tools").rglob("*.py")),
    ]
    bad = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        if _ABSOLUTE_PATH.search(text):
            bad.append(str(path.relative_to(ROOT)))
    assert not bad, f"发现硬编码机器路径: {bad}"
