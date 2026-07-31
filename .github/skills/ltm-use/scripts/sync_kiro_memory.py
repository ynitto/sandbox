#!/usr/bin/env python3
from __future__ import annotations
"""
sync_kiro_memory.py - Kiro IDE / CLI の記憶情報を ltm-use にインポートするスクリプト

取り込み対象:
  1. Kiro グローバルステアリングファイル（~/.kiro/steering/*.md）
     全ワークスペースに適用される永続的なナレッジ（conventions, standards 等）
  2. Kiro IDE globalStorage（%%APPDATA%%\\Kiro\\User\\globalStorage\\ 相当）
     VSCode フォークのメモリ拡張機能エントリ（存在する場合）

WSL サポート:
  Linux/WSL 環境では、Kiro IDE の Windows globalStorage を /mnt/c/ 経由で
  自動検出する。Kiro CLI のステアリングは ~/.kiro/ を直接参照する。

使用例:
  # 何が見つかるか確認するだけ（ファイルを作成しない）
  python sync_kiro_memory.py --dry-run

  # home スコープに取り込む（デフォルト）
  python sync_kiro_memory.py --force

  # ソースを絞って実行
  python sync_kiro_memory.py --source steering      # グローバルステアリングのみ
  python sync_kiro_memory.py --source ide           # Kiro IDE globalStorage のみ
  python sync_kiro_memory.py --source all           # 両方（デフォルト）

  # パスを明示指定
  python sync_kiro_memory.py --kiro-home /custom/path/.kiro
  python sync_kiro_memory.py --ide-storage "/mnt/c/Users/you/AppData/Roaming/Kiro/User/globalStorage"
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

# scripts/ ディレクトリを sys.path に追加して memory_utils / save_memory をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_utils
from save_memory import save_memory as _ltm_save


IMPORT_LOG_FILENAME = ".kiro-import-log.json"

# Kiro IDE の拡張機能 ID パターン（将来的なメモリ拡張に備えて汎用的に持つ）
KIRO_EXT_IDS = [
    "kiro.kiro-chat",
    "Kiro.kiro-chat",
    "amazon.kiro",
]

# state.vscdb のメモリ関連キーパターン（sync_copilot_memory.py と同様）
MEMORY_KEY_PATTERNS = [
    "memor",
    "nes.",
    "knowledge",
    "notes",
    "instruction",
    "persist",
    "remember",
    "context.bank",
    "user.pref",
    "steering",
]


# ── パス取得 ──────────────────────────────────────────────────────────────────

def get_kiro_home() -> Path:
    """~/.kiro/ パスを返す（CLI / グローバルステアリングの格納場所）。"""
    return Path.home() / ".kiro"


def get_kiro_ide_global_storage() -> "Path | None":
    """Kiro IDE の globalStorage パスを OS に応じて返す。見つからない場合は None。"""
    if sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "Kiro" / "User" / "globalStorage"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        p = Path(appdata) / "Kiro" / "User" / "globalStorage"
    else:
        # Linux: ネイティブパスを先に試行
        linux_p = Path.home() / ".config" / "Kiro" / "User" / "globalStorage"
        if linux_p.exists():
            return linux_p
        # WSL: Windows の %APPDATA%\Kiro\ を /mnt/c/ 経由で探索
        wsl_p = _find_wsl_kiro_global_storage()
        return wsl_p  # None の場合もあり
    return p if p.exists() else None


def _find_wsl_kiro_global_storage() -> "Path | None":
    """WSL 環境で Windows の Kiro IDE globalStorage パスを解決する。"""
    try:
        result = subprocess.run(
            ["wslpath", "-u", r"C:\Users"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        win_users = Path(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    try:
        for user_dir in sorted(win_users.iterdir()):
            candidate = user_dir / "AppData" / "Roaming" / "Kiro" / "User" / "globalStorage"
            if candidate.exists():
                return candidate
    except (PermissionError, OSError):
        pass
    return None


# ── Kiro グローバルステアリングのインポート ───────────────────────────────────

def collect_steering_entries(kiro_home: Path) -> list[dict]:
    """~/.kiro/steering/ 内の全 Markdown ファイルをエントリとして返す。"""
    steering_dir = kiro_home / "steering"
    if not steering_dir.is_dir():
        return []

    entries = []
    for md_file in sorted(steering_dir.rglob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8").strip()
        except IOError:
            continue
        if len(text) < 10:
            continue

        # YAML フロントマターを除去してコンテンツのみ取得
        body, frontmatter = _strip_frontmatter(text)

        title = (
            frontmatter.get("name")
            or frontmatter.get("title")
            or md_file.stem.replace("-", " ").replace("_", " ")
        )

        tags = ["kiro-steering", "imported"]
        inclusion = frontmatter.get("inclusion", "always")
        if inclusion:
            tags.append(f"kiro-inclusion:{inclusion}")

        content_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        entries.append({
            "id": f"kiro-steering:{_slug(md_file.stem)}-{content_hash}",
            "title": str(title)[:60],
            "text": body or text,
            "raw_text": text,
            "tags": tags,
            "source_file": str(md_file),
            "source_type": "steering",
        })
    return entries


def _strip_frontmatter(text: str) -> "tuple[str, dict]":
    """YAML フロントマター（---...---）を除去し（本文, frontmatter dict）を返す。"""
    fm: dict = {}
    if not text.startswith("---"):
        return text, fm

    lines = text.split("\n")
    end = -1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end < 0:
        return text, fm

    # フロントマター部分を簡易パース
    for line in lines[1:end]:
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()

    body = "\n".join(lines[end + 1:]).strip()
    return body, fm


# ── Kiro IDE globalStorage のインポート ───────────────────────────────────────

def collect_ide_entries(global_storage: Path) -> list[dict]:
    """Kiro IDE globalStorage 内のメモリエントリを収集する。"""
    ext_dirs = _find_kiro_ext_dirs(global_storage)
    if not ext_dirs:
        return []

    all_entries: list[dict] = []
    for ext_dir in ext_dirs:
        # memory-tool/memories/ ディレクトリ（Copilot 互換）
        mt_entries = _scan_memory_tool_dirs(ext_dir)
        all_entries.extend(mt_entries)

        # state.vscdb から抽出
        db_path = ext_dir / "state.vscdb"
        sqlite_entries = _query_sqlite_memories(db_path)
        all_entries.extend(sqlite_entries)

        # JSON / Markdown ファイルをスキャン
        all_entries.extend(_scan_json_files(ext_dir))
        all_entries.extend(_scan_markdown_files(ext_dir))

    # source_type を設定
    for e in all_entries:
        e.setdefault("source_type", "ide")
        e.setdefault("tags", [])
        if "kiro-memory" not in e["tags"]:
            e["tags"].append("kiro-memory")
        if "imported" not in e["tags"]:
            e["tags"].append("imported")

    return all_entries


def _find_kiro_ext_dirs(global_storage: Path) -> list[Path]:
    """globalStorage 内の Kiro 拡張機能ディレクトリを返す。"""
    if not global_storage.is_dir():
        return []
    found = []
    for ext_id in KIRO_EXT_IDS:
        candidate = global_storage / ext_id
        if candidate.is_dir() and candidate not in found:
            found.append(candidate)
    for d in sorted(global_storage.iterdir()):
        if d.is_dir() and "kiro" in d.name.lower() and d not in found:
            found.append(d)
    return found


def _scan_memory_tool_dirs(ext_dir: Path) -> list[dict]:
    memories_dir = ext_dir / "memory-tool" / "memories"
    if not memories_dir.is_dir():
        return []
    results = []
    for md_file in sorted(memories_dir.rglob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8").strip()
        except IOError:
            continue
        if len(text) < 5:
            continue
        title_hint = md_file.stem.replace("-", " ").replace("_", " ")
        content_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        results.append({
            "id": f"kiro:mt:{_slug(md_file.stem)}-{content_hash}",
            "title": title_hint,
            "text": text,
            "tags": [],
            "source_file": str(md_file),
        })
    return results


def _query_sqlite_memories(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    results = []
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM ItemTable")
        rows = cursor.fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    for key, value in rows:
        if not any(pat.lower() in key.lower() for pat in MEMORY_KEY_PATTERNS):
            continue
        src = {"source_key": key, "source_db": str(db_path)}
        try:
            data = json.loads(value)
            for e in _extract_entries_from_json(key, data):
                e.update(src)
                results.append(e)
        except (json.JSONDecodeError, TypeError):
            text = str(value).strip()
            if len(text) > 10:
                results.append({"id": f"key:{_slug(key)}", "title": "", "text": text, "tags": [], **src})
    return results


def _extract_entries_from_json(key: str, data) -> list[dict]:
    entries = []
    if isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict):
                e = _normalize_entry(item)
                if e:
                    entries.append(e)
            elif isinstance(item, str) and len(item.strip()) > 5:
                entries.append({"id": f"{_slug(key)}-{i}", "title": "", "text": item.strip(), "tags": []})
    elif isinstance(data, dict):
        direct = _normalize_entry(data)
        if direct:
            return [direct]
        for lk in ("memories", "entries", "items", "notes", "instructions", "contexts"):
            if lk in data and isinstance(data[lk], list):
                for i, item in enumerate(data[lk]):
                    if isinstance(item, dict):
                        e = _normalize_entry(item)
                        if e:
                            entries.append(e)
                    elif isinstance(item, str) and len(item.strip()) > 5:
                        entries.append({"id": f"{lk}-{i}", "title": "", "text": item.strip(), "tags": []})
                if entries:
                    return entries
        for k, v in data.items():
            if isinstance(v, str) and len(v.strip()) > 10 and not k.startswith("_"):
                entries.append({"id": _slug(k), "title": "", "text": v.strip(), "tags": []})
    elif isinstance(data, str) and len(data.strip()) > 10:
        entries.append({"id": _slug(key), "title": "", "text": data.strip(), "tags": []})
    return entries


def _normalize_entry(item: dict) -> "dict | None":
    text = (
        item.get("text") or item.get("content") or item.get("value")
        or item.get("note") or item.get("memory") or item.get("instruction")
        or item.get("description") or item.get("body") or item.get("message") or ""
    )
    if isinstance(text, list):
        text = " ".join(str(t) for t in text)
    text = str(text).strip()
    if len(text) < 5:
        return None
    entry_id = str(item.get("id") or item.get("uuid") or item.get("memoryId") or "")
    title = str(item.get("title") or item.get("name") or item.get("summary") or "")
    tags_raw = item.get("tags") or item.get("labels") or []
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    elif isinstance(tags_raw, list):
        tags = [str(t) for t in tags_raw if t]
    else:
        tags = []
    return {"id": entry_id, "title": title, "text": text, "tags": tags}


def _scan_json_files(ext_dir: Path) -> list[dict]:
    results = []
    patterns = ["*memor*.json", "*notes*.json", "*instruction*.json",
                "*persist*.json", "*knowledge*.json", "*steering*.json"]
    found: set[Path] = set()
    for p in patterns:
        found.update(ext_dir.rglob(p))
    for f in sorted(found):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for e in _extract_entries_from_json(f.stem, data):
                e["source_file"] = str(f)
                results.append(e)
        except (json.JSONDecodeError, IOError):
            pass
    return results


def _scan_markdown_files(ext_dir: Path) -> list[dict]:
    results = []
    patterns = ["*memor*.md", "*notes*.md", "*instruction*.md", "*persist*.md"]
    found: set[Path] = set()
    for p in patterns:
        found.update(ext_dir.rglob(p))
    for f in sorted(found):
        try:
            text = f.read_text(encoding="utf-8").strip()
            if len(text) < 5:
                continue
            content_hash = hashlib.md5(text.encode()).hexdigest()[:12]
            results.append({
                "id": f"kiro:md:{_slug(f.stem)}-{content_hash}",
                "title": f.stem.replace("-", " ").replace("_", " "),
                "text": text,
                "tags": [],
                "source_file": str(f),
            })
        except IOError:
            pass
    return results


# ── 重複管理 ──────────────────────────────────────────────────────────────────

def _make_stable_id(entry: dict) -> str:
    raw_id = entry.get("id", "")
    if raw_id:
        return f"kiro:{raw_id}"
    text_key = entry.get("text", "")[:64]
    return f"kiro:text:{hash(text_key) & 0xFFFFFFFF:08x}"


def _make_legacy_stable_id(entry: dict) -> str | None:
    """コンテンツハッシュ追加前の旧ID形式を返す（後方互換チェック用）。

    `_make_stable_id` がハッシュ付きID（例: kiro:kiro:mt:foo-10240cc2c05b）を返す場合に、
    ハッシュ部分を除いた旧形式ID（例: kiro:kiro:mt:foo）もチェックして重複インポートを防ぐ。
    """
    import re as _re
    current = _make_stable_id(entry)
    legacy = _re.sub(r"-[0-9a-f]{12}$", "", current)
    return legacy if legacy != current else None


def load_import_log(category_dir: Path) -> set[str]:
    log_path = category_dir / IMPORT_LOG_FILENAME
    if log_path.exists():
        try:
            with open(log_path, encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("imported_ids", []))
        except (json.JSONDecodeError, IOError):
            pass
    return set()


def save_import_log(category_dir: Path, new_ids: set[str]) -> None:
    log_path = category_dir / IMPORT_LOG_FILENAME
    existing = load_import_log(category_dir)
    all_ids = sorted(existing | new_ids)
    existing_data: dict = {}
    if log_path.exists():
        try:
            with open(log_path, encoding="utf-8") as f:
                existing_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    os.makedirs(category_dir, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                **existing_data,
                "imported_ids": all_ids,
                "last_run": datetime.datetime.now().isoformat(timespec="seconds"),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def should_run(category_dir: Path, interval_hours: int) -> "tuple[bool, str]":
    log_path = category_dir / IMPORT_LOG_FILENAME
    if not log_path.exists():
        return True, "初回実行"
    try:
        with open(log_path, encoding="utf-8") as f:
            data = json.load(f)
        last_run_str = data.get("last_run", "")
        if not last_run_str:
            return True, "last_run 未記録"
        last_dt = datetime.datetime.fromisoformat(last_run_str)
        elapsed_hours = (datetime.datetime.now() - last_dt).total_seconds() / 3600
        if elapsed_hours < interval_hours:
            remaining = interval_hours - elapsed_hours
            return False, (
                f"前回実行から {elapsed_hours:.1f}時間（インターバル {interval_hours}h、"
                f"次回まで約 {remaining:.1f}時間）"
            )
        return True, f"前回実行から {elapsed_hours:.1f}時間経過"
    except (json.JSONDecodeError, ValueError, OSError):
        return True, "ログ読み込みエラー（実行を続行）"


# ── ltm-use への変換・保存 ────────────────────────────────────────────────────

def _slug(text: str) -> str:
    text = str(text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text[:40] or "item"


def _make_title(entry: dict) -> str:
    title = entry.get("title", "").strip()
    if title and len(title) <= 60:
        return title
    text = entry.get("text", "")
    first_line = text.split("\n")[0].strip()
    if len(first_line) <= 60:
        return first_line or "Kiro Memory"
    return first_line[:57] + "..."


def _make_summary(entry: dict) -> str:
    text = entry.get("text", "").strip()
    sentences = re.split(r"(?<=[。\.！!？?\n])\s*", text)
    summary = " ".join(s.strip() for s in sentences[:2] if s.strip())[:200]
    return summary or text[:200]


def _make_category(entry: dict) -> str:
    source_type = entry.get("source_type", "")
    if source_type == "steering":
        return "kiro-steering"
    return "kiro-memory"


def import_entry(entry: dict, scope: str, dry_run: bool) -> bool:
    text = entry.get("text", "").strip()
    if not text:
        return False

    title = _make_title(entry)
    summary = _make_summary(entry)
    category = _make_category(entry)
    tags = list(entry.get("tags", [])) + ["imported"]
    if "kiro-steering" not in tags and entry.get("source_type") == "steering":
        tags.append("kiro-steering")

    source_label = (
        entry.get("source_file")
        or entry.get("source_key")
        or f"Kiro {entry.get('source_type', 'memory')}"
    )
    context = f"Kiro からインポート（ソース: {source_label}）"

    if dry_run:
        print(f"  [DRY-RUN] タイトル : {title}")
        print(f"            カテゴリ : {category}")
        print(f"            要約    : {summary[:80]}{'...' if len(summary) > 80 else ''}")
        print(f"            タグ    : {', '.join(tags)}")
        print()
        return True

    filepath = _ltm_save(
        category=category,
        title=title,
        summary=summary,
        content=text,
        tags=tags,
        scope=scope,
        context=context,
        conclusion="（インポート時は未評価。内容を確認後 rate_memory.py で評価してください）",
    )
    print(f"  保存: {filepath}")
    return True


# ── メイン ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kiro IDE / CLI の記憶情報を ltm-use にインポートする"
    )
    parser.add_argument(
        "--source", choices=["steering", "ide", "all"], default="all",
        help=(
            "取り込みソース: steering（~/.kiro/steering/ のみ）"
            "/ ide（Kiro IDE globalStorage のみ）/ all（両方、デフォルト）"
        ),
    )
    parser.add_argument(
        "--kiro-home",
        help="~/.kiro/ の代替パスを指定（Kiro CLI / ステアリングの場所）",
    )
    parser.add_argument(
        "--ide-storage",
        help="Kiro IDE globalStorage の代替パスを指定",
    )
    parser.add_argument(
        "--scope", default="home", choices=["home"],
        help="インポート先スコープ: home（デフォルト・プロジェクト横断）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="何が見つかるか確認するだけ（ファイルを作成しない）",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="インポート済みIDを無視して再インポートする",
    )
    parser.add_argument(
        "--interval-hours", type=int, default=72,
        help="前回実行からこの時間未満の場合はスキップ（デフォルト: 72時間 = 3日）",
    )
    args = parser.parse_args()

    # インターバルチェック
    if not args.force and not args.dry_run:
        memory_dir = Path(memory_utils.get_memory_dir(args.scope))
        check_dir = memory_dir / "kiro-memory"
        run_ok, reason = should_run(check_dir, args.interval_hours)
        if not run_ok:
            print(f"[SKIP] Kiro メモリインポートをスキップ: {reason}")
            sys.exit(0)
        print(f"[INFO] {reason} → インポートを実行します")

    kiro_home = Path(args.kiro_home) if args.kiro_home else get_kiro_home()
    all_entries: list[dict] = []

    # ── ステアリングファイルを収集 ──
    if args.source in ("steering", "all"):
        steering_entries = collect_steering_entries(kiro_home)
        if steering_entries:
            print(f"[Kiro steering] {len(steering_entries)} ファイルを検出: {kiro_home / 'steering'}")
        else:
            print(f"[INFO] Kiro ステアリングファイルが見つかりません: {kiro_home / 'steering'}")
        all_entries.extend(steering_entries)

    # ── Kiro IDE globalStorage を収集 ──
    if args.source in ("ide", "all"):
        ide_path: "Path | None"
        if args.ide_storage:
            ide_path = Path(args.ide_storage)
        else:
            ide_path = get_kiro_ide_global_storage()

        if ide_path is None or not ide_path.exists():
            msg = str(ide_path) if ide_path else "（自動検出できませんでした）"
            if args.source == "ide":
                print(f"[ERROR] Kiro IDE globalStorage が見つかりません: {msg}", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"[INFO] Kiro IDE globalStorage が見つかりません（スキップ）: {msg}")
        else:
            ide_entries = collect_ide_entries(ide_path)
            if ide_entries:
                print(f"[Kiro IDE]      {len(ide_entries)} エントリを検出: {ide_path}")
            else:
                print(f"[INFO] Kiro IDE にメモリエントリが見つかりません: {ide_path}")
            all_entries.extend(ide_entries)

    if not all_entries:
        print("\n[INFO] インポート対象のエントリが見つかりませんでした。")
        print("  ヒント: ~/.kiro/steering/ にステアリングファイルを作成すると取り込まれます。")
        return

    print(f"\n合計 {len(all_entries)} エントリを検出しました。\n")

    # インポート済みチェック
    memory_dir = Path(memory_utils.get_memory_dir(args.scope))
    category_dir = memory_dir / "kiro-memory"
    imported_ids = set() if args.force else load_import_log(category_dir)

    skipped = 0
    imported = 0
    new_ids: set[str] = set()

    for entry in all_entries:
        stable_id = _make_stable_id(entry)
        legacy_id = _make_legacy_stable_id(entry)
        if (stable_id in imported_ids
                or stable_id in new_ids
                or (legacy_id and legacy_id in imported_ids)):
            skipped += 1
            continue
        success = import_entry(entry, args.scope, args.dry_run)
        if success:
            imported += 1
            if not args.dry_run:
                new_ids.add(stable_id)

    if not args.dry_run and new_ids:
        save_import_log(category_dir, new_ids)

    print(f"\n=== 完了 ===")
    if args.dry_run:
        print(f"  インポート対象: {imported} エントリ（dry-run のため実際には保存されていません）")
    else:
        print(f"  インポート完了: {imported} エントリ → {args.scope} スコープ")
    if skipped:
        print(f"  スキップ（重複）: {skipped} エントリ")
    if not args.dry_run and imported:
        print(f"\n  次のステップ:")
        print(f"    python recall_memory.py 'kiro' --scope {args.scope}  # インポート結果を確認")
        print(f"    python list_memories.py --scope {args.scope}          # 一覧を表示")


if __name__ == "__main__":
    main()
