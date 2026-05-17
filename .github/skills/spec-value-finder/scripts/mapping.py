"""マッピングファイル（mapping.yaml）のスキーマ・読込・検証・ドラフト生成。

マッピングファイルは「記入先(仕様書B)の項目」と「元仕様書(仕様書A)での探し方」を
人が明示的に結びつける辞書。GraphRAG の自動マッチングを置き換える中核で、
keywords 列が語彙ギャップ（表記揺れ）対策の要になる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

VALID_TYPES = {"number", "text", "enum", "date", ""}

SCHEMA_HELP = """\
# spec-value-finder マッピングファイル
version: 1
source:
  folder: ./specs          # 元仕様書フォルダ（再帰走査）
  name_match: "HW仕様"      # ファイル名の部分一致パターン（空=全件）
items:
  - target: "MTU上限"                # 必須: 記入先の項目名
    keywords: ["MTU", "Maximum Transmission Unit", "最大転送単位"]  # 必須: 表記揺れを列挙
    section_hint: "ネットワーク"      # 任意: 元仕様書の章/シートのヒント
    unit: "bytes"                     # 任意: 期待単位
    type: number                      # 任意: number|text|enum|date
    note: "レイヤ2 のフレーム長"       # 任意: 補足
"""


def load_mapping(path: Path | str) -> dict:
    import yaml

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"マッピングファイルが見つかりません: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("マッピングファイルのトップレベルはマップである必要があります。")
    return data


def validate_mapping(data: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) を返す。errors が空なら有効。"""
    errors: list[str] = []
    warnings: list[str] = []

    if data.get("version") != 1:
        warnings.append("version は 1 を推奨します。")

    source = data.get("source") or {}
    if not isinstance(source, dict):
        errors.append("source はマップである必要があります。")
        source = {}
    if not source.get("folder"):
        warnings.append("source.folder が未設定です（find 時に --folder 指定が必要）。")

    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items は 1 件以上のリストである必要があります。")
        return errors, warnings

    seen: set[str] = set()
    for i, item in enumerate(items):
        loc = f"items[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{loc}: マップである必要があります。")
            continue
        target = item.get("target")
        if not target or not str(target).strip():
            errors.append(f"{loc}: target（記入先の項目名）が必須です。")
        else:
            if target in seen:
                errors.append(f"{loc}: target '{target}' が重複しています。")
            seen.add(target)
        kws = item.get("keywords")
        if not isinstance(kws, list) or not [k for k in (kws or []) if str(k).strip()]:
            errors.append(f"{loc} ('{target}'): keywords に 1 個以上の語を指定してください。")
        t = item.get("type", "")
        if t not in VALID_TYPES:
            warnings.append(f"{loc} ('{target}'): type '{t}' は未知です（{sorted(VALID_TYPES - {''})} のいずれか）。")
        if not item.get("section_hint"):
            warnings.append(f"{loc} ('{target}'): section_hint があると探索精度が上がります。")

    return errors, warnings


def items_of(data: dict) -> list[dict[str, Any]]:
    return list(data.get("items") or [])


def write_draft(out_path: Path | str, source_text: str = "") -> Path:
    """map-draft 用のスカフォールド mapping.yaml を書き出す。

    自然文の対応記述から人/Claude が items を埋める前提のひな形。
    """
    out_path = Path(out_path)
    body = SCHEMA_HELP.replace("items:\n", "items: []\n", 1).split("items: []")[0]
    lines = [
        "# === map-draft が生成したドラフト ===",
        "# 下記スキーマに従い items を埋め、`validate` で検証してください。",
        "# 元記述（自然文マッピング）は別途 stdout に出力されています。",
        "",
        "version: 1",
        "source:",
        '  folder: ""',
        '  name_match: ""',
        "items: []",
        "",
        "# --- スキーマ参考 ---",
    ]
    lines += ["# " + ln for ln in SCHEMA_HELP.splitlines()]
    if source_text:
        lines += ["", "# --- 元記述（抜粋） ---"]
        lines += ["# " + ln for ln in source_text.splitlines()[:200]]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
