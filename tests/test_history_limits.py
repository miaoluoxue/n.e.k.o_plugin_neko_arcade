from __future__ import annotations

from plugin.plugins.neko_arcade.games.history import game as history_game


class _OversizedResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit=-1):
        return b"x" * limit


def test_history_response_is_size_limited(monkeypatch):
    monkeypatch.setattr(
        history_game.urllib.request, "urlopen",
        lambda *_a, **_k: _OversizedResponse(),
    )
    assert history_game._fetch_month("01", retry=1) is None
