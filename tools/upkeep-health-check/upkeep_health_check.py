"""保全点検の結果を version 1 の JSON で出力する。

結果は version、source（upkeep-health-check）、checked_at（UTC の ISO 日時）、
summary（ok / stale / unknown の件数）、items を持つ。各項目は name、kind、
status、detail（日本語）、value、threshold を持つ。未取得・未設定の値は null。
鮮度の value と threshold は時間、ストアの指標は元レポートと同じ単位。
"""

import argparse
import json
import math
import os
import stat
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


LAYER_METRICS = {
    "ltm": ("dormant_oldest_days", "index_drift"),
    "wiki": ("index_drift",),
    "persona": ("pending_oldest_days",),
    "moltbook": ("outbox_pending", "outbox_oldest_days"),
}
METRICS = {metric for metrics in LAYER_METRICS.values() for metric in metrics}


def number(value):
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value))


def object_value(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} は JSON オブジェクトで指定してください")
    return value


def validate_limit(value, label):
    if not number(value) or value < 0:
        raise ValueError(f"{label} は有限の非負数で指定してください")


def read_config(path):
    config = object_value(json.loads(path.read_text(encoding="utf-8")), "設定")
    exports = config.get("kiro_exports", [])
    if not isinstance(exports, list):
        raise ValueError("kiro_exports は配列で指定してください")
    knowledge = config.get("knowledge")
    if "knowledge" in config:
        object_value(knowledge, "knowledge")
    if not exports and knowledge is None:
        raise ValueError("点検対象を設定してください")

    def resolve(entry, key):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValueError(f"{key} にはパスを指定してください")
        if "max_age_hours" in entry:
            validate_limit(entry["max_age_hours"], "max_age_hours")
        return path.parent / Path(value).expanduser()

    for entry in exports:
        object_value(entry, "kiro_exports の要素")
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError("kiro_exports の要素には名前を指定してください")
        entry["dir"] = resolve(entry, "dir")
    if knowledge is not None:
        knowledge["path"] = resolve(knowledge, "path")
        thresholds = object_value(knowledge.get("thresholds", {}), "thresholds")
        for metric, limit in thresholds.items():
            if metric not in METRICS:
                raise ValueError(f"未対応のしきい値です（{metric}）")
            validate_limit(limit, metric)
    return exports, knowledge


def item(name, kind, status, detail, value=None, threshold=None):
    return dict(name=name, kind=kind, status=status, detail=detail,
                value=value, threshold=threshold)


def compare(name, kind, label, value, threshold):
    if not number(value):
        return item(name, kind, "unknown", f"{label}を確認できません", threshold=threshold)
    stale = threshold is not None and value > threshold
    if threshold is None:
        detail = f"{label}を確認しました（上限未設定）"
    else:
        detail = f"{label}が上限を超えています" if stale else f"{label}は上限以内です"
    return item(name, kind, "stale" if stale else "ok", detail, value, threshold)


def file_mtime(info):
    if not stat.S_ISREG(info.st_mode):
        raise OSError("通常のファイルではありません")
    return info.st_mtime


def check_freshness(name, kind, label, mtime, now, limit):
    if mtime > now:
        return item(name, kind, "unknown", f"{label}を判定できません（時刻逆転: mtime が判定時刻より未来）",
                    threshold=limit)
    return compare(name, kind, label, (now - mtime) / 3600, limit)


def check_export(entry, now):
    name, folder = entry["name"], entry["dir"]
    kind = "kiro_export"
    limit = entry.get("max_age_hours")
    try:
        mtime = file_mtime((folder / ".kiro_export_state.json").stat())
    except OSError:
        return item(name, kind, "unknown", "出力先の状態ファイルを確認できません",
                    threshold=limit)
    return check_freshness(name, kind, "状態ファイルの経過時間", mtime, now, limit)


def check_layers(layers, thresholds):
    for layer, metrics in LAYER_METRICS.items():
        entry = layers.get(layer)
        if isinstance(entry, dict) and entry.get("configured") is False:
            yield item(layer, "knowledge_layer", "unknown", "未収集（ストア未設定）")
            continue
        stores = entry.get("stores") if isinstance(entry, dict) else None
        if not isinstance(stores, list) or not stores:
            yield item(layer, "knowledge_layer", "unknown", "ストア情報を確認できません")
            continue
        for index, store in enumerate(stores, 1):
            name = f"{layer} / {index}"
            if not isinstance(store, dict):
                yield item(name, "knowledge_layer", "unknown", "ストアを確認できません")
                continue
            name = f"{layer} / {store.get('path') or index}"
            for metric in metrics:
                if metric in thresholds:
                    yield compare(f"{name} / {metric}", "knowledge_metric", "指標の値",
                                  store.get(metric), thresholds[metric])


def check_knowledge(config, now):
    name, kind = "knowledge", "knowledge_report"
    path, limit = config["path"], config.get("max_age_hours")
    try:
        with path.open(encoding="utf-8") as report:
            mtime = file_mtime(os.fstat(report.fileno()))
            layers = object_value(json.load(report), "レポート").get("layers")
            object_value(layers, "layers")
    except (OSError, ValueError, RecursionError):
        yield item(name, kind, "unknown", "knowledge レポートを読み取れません",
                   threshold=limit)
        return
    yield check_freshness(name, kind, "レポートの経過時間", mtime, now, limit)
    yield from check_layers(layers, config.get("thresholds", {}))


def write_result(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kiro エクスポートと記憶の保全点検")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", help="判定時刻（ISO 日時。オフセット省略時は UTC）")
    args = parser.parse_args(argv)
    try:
        args.output.unlink(missing_ok=True)
        now = datetime.now(timezone.utc)
        if args.now is not None:
            iso_time = args.now[:-1] + "+00:00" if args.now.endswith("Z") else args.now
            now = datetime.fromisoformat(iso_time)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            now = now.astimezone(timezone.utc)
        exports, knowledge = read_config(args.config)
        items = [check_export(entry, now.timestamp()) for entry in exports]
        if knowledge is not None:
            items.extend(check_knowledge(knowledge, now.timestamp()))
        counts = Counter(entry["status"] for entry in items)
        write_result(args.output, {
            "version": 1,
            "source": "upkeep-health-check",
            "checked_at": now.isoformat(),
            "summary": {status: counts[status] for status in ("ok", "stale", "unknown")},
            "items": items,
        })
    except (OSError, ValueError, RuntimeError, OverflowError) as exc:
        print(f"保全点検を実行できません（{exc}）", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
