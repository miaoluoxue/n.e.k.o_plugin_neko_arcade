"""配置统一管理：每个小游戏的配置和帮助数据集中存放于 data/。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONFIG_DIR = os.path.join(DATA_DIR, "config")


class GameConfig:
    """一个游戏的配置 + 帮助数据 + 情感模板 + 关键词。"""

    def __init__(self, game_id: str, config: Dict[str, Any],
                 help_data: Optional[Dict[str, Any]] = None,
                 emotion_templates: Optional[Dict[str, Any]] = None,
                 keywords: Optional[List[str]] = None) -> None:
        self.game_id = game_id
        self.config = config
        self.help = help_data or {}
        self.emotion_templates = emotion_templates or {}
        self.keywords = keywords or []

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def get_help_commands(self) -> list:
        """帮助指令列表 [(指令, 说明)]。"""
        return self.help.get("commands", []) or []

    def get_help_text(self) -> str:
        return self.help.get("text", "") or ""

    def get_emotion(self, key: str, default: Any = None) -> Any:
        """获取情感模板。"""
        return self.emotion_templates.get(key, default)

    def get_keywords(self) -> List[str]:
        """获取关键词列表。"""
        return self.keywords


class ConfigManager:
    """管理所有游戏的配置。"""

    def __init__(self, data_dir: str = DATA_DIR) -> None:
        self.data_dir = data_dir
        self._cache: Dict[str, GameConfig] = {}

    def _game_dir(self, game_id: str) -> str:
        return os.path.join(CONFIG_DIR, game_id)

    def load(self, game_id: str) -> GameConfig:
        """加载（或创建默认）一个游戏的配置。"""
        if game_id in self._cache:
            return self._cache[game_id]
        gdir = self._game_dir(game_id)
        config = self._read_json(os.path.join(gdir, "config.json"), {})
        help_data = self._read_json(os.path.join(gdir, "help.json"), {})
        emotion_templates = self._read_json(os.path.join(gdir, "emotion.json"), {})
        keywords = self._read_json(os.path.join(gdir, "keywords.json"), [])
        gc = GameConfig(game_id, config, help_data, emotion_templates, keywords)
        self._cache[game_id] = gc
        return gc

    def save(self, game_id: str, config: Dict[str, Any]) -> None:
        """保存游戏配置。"""
        gdir = self._game_dir(game_id)
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        if game_id in self._cache:
            self._cache[game_id].config = config

    def save_help(self, game_id: str, help_data: Dict[str, Any]) -> None:
        """保存游戏帮助数据。"""
        gdir = self._game_dir(game_id)
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "help.json"), "w", encoding="utf-8") as f:
            json.dump(help_data, f, ensure_ascii=False, indent=2)
        if game_id in self._cache:
            self._cache[game_id].help = help_data

    def get_game_ids(self) -> list:
        if not os.path.isdir(CONFIG_DIR):
            return []
        return [d for d in os.listdir(CONFIG_DIR) if os.path.isdir(os.path.join(CONFIG_DIR, d))]

    def clear(self) -> None:
        self._cache.clear()

    @staticmethod
    def _read_json(path: str, default: Any) -> Any:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default
