"""测试引导: 统一建立可导入路径, 供 E2E 测试使用。

CI(verify)里插件被 mount 到 N.E.K.O 树的 plugin/plugins/neko_arcade,
``plugin.plugins.neko_arcade.*`` 可直接导入; 本地独立跑时需手动建包链。
本模块在顶层执行路径引导, 测试文件从这里导入游戏类, 避免 E402。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 已安装为 plugin.plugins.neko_arcade 时直接用完整路径(CI 场景)
try:
    _installed = importlib.util.find_spec("plugin.plugins.neko_arcade") is not None
except (ImportError, ModuleNotFoundError):
    _installed = False

if not _installed:
    # 本地独立跑: 手动建 plugins.neko_arcade 包链
    pkg_root = types.ModuleType("plugins")
    pkg_root.__path__ = []
    sys.modules["plugins"] = pkg_root

    pkg_arcade = types.ModuleType("plugins.neko_arcade")
    pkg_arcade.__path__ = [str(ROOT)]
    sys.modules["plugins.neko_arcade"] = pkg_arcade

    games_pkg = types.ModuleType("plugins.neko_arcade.games")
    games_pkg.__path__ = [str(ROOT / "games")]
    sys.modules["plugins.neko_arcade.games"] = games_pkg

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
