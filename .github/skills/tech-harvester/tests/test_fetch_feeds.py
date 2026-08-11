"""fetch_feeds.py のフィード解決のユニットテスト。

レジストリを agent home 直下へ移した際に「新しい agent home ではフィード 0 件」になり、
`KeyError: 'skill_configs'` で落ちていた。同梱の既定フィードで初回だけ埋める挙動を固定する。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_feeds  # noqa: E402


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "skill-registry.json"
    monkeypatch.setattr(fetch_feeds, "REGISTRY_PATH", path)
    return path


def test_seeds_defaults_into_empty_registry(registry):
    registry.write_text(json.dumps({"version": 7}), encoding="utf-8")
    feeds = fetch_feeds.load_registry(None, None)
    assert feeds, "既定フィードで埋める"
    saved = json.loads(registry.read_text(encoding="utf-8"))
    assert saved["skill_configs"]["tech-harvester"]["feeds"] == feeds
    assert saved["version"] == 7, "既存のキーを壊さない"


def test_keeps_configured_feeds(registry):
    mine = [{"name": "Only Mine", "url": "https://example.com/rss", "lang": "ja", "tags": ["x"]}]
    registry.write_text(
        json.dumps({"skill_configs": {"tech-harvester": {"feeds": mine}}}), encoding="utf-8")
    assert fetch_feeds.load_registry(None, None) == mine


def test_without_registry_uses_defaults_without_writing(registry):
    assert fetch_feeds.load_registry(None, None)
    assert not registry.exists(), "未インストール実行でレジストリを作らない"


def test_filters_by_lang_and_tags():
    feeds = json.loads(Path(fetch_feeds.DEFAULT_FEEDS_PATH).read_text(encoding="utf-8"))["feeds"]
    assert all(f["lang"] == "ja" for f in fetch_feeds._filter_feeds(feeds, None, "ja"))
    tagged = fetch_feeds._filter_feeds(feeds, ["oss"], None)
    assert tagged and all("oss" in f["tags"] for f in tagged)
