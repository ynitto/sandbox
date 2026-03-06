#!/usr/bin/env python3
"""
sync_copilot_memory.py - VSCode Copilot Memory を ltm-use にインポートするスクリプト

VSCode の globalStorage/github.copilot-chat/ に保存された Copilot Memory を読み込み、
ltm-use が管理する記憶ファイル（Markdown）へ変換してインポートする。

新規エントリのみを取り込み、重複インポートを避けるため
{MEMORY_DIR}/copilot-memory/.copilot-import-log.json にインポート済みIDを記録する。

使用例:
  # 何が見つかるか確認するだけ（ファイルを作成しない）
  python sync_copilot_memory.py --dry-run

  # home スコープに取り込む（デフォルト）
  python sync_copilot_memory.py

  # workspace スコープに取り込む
  python sync_copilot_memory.py --scope workspace

  # globalStorage のパスを明示指定（VSCode Insiders / Cursor 等）
  python sync_copilot_memory.py --storage "/path/to/globalStorage"

  # Copilot 拡張フォルダ内の全キーを一覧表示（フォーマット調査用）
  python sync_copilot_memory.py --list-keys
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# scripts/ ディレクトリを sys.path に追加して memory_utils / save_memory をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_utils
from save_memory import save_memory as _ltm_save


# ── 対象拡張機能ID ──────────────────────────────────────────────────────────────

COPILOT_EXT_IDS = [
    "github.copilot-chat",
    "GitHub.copilot-chat",
    "github.copilot",
    "GitHub.copilot",
]

# state.vscdb の ItemTable でメモリ関連と判断するキー名パターン（部分一致・大小文字無視）
MEMORY_KEY_PATTERNS = [
    "memor",
    "nes.",          # NES (Natural Experience Storage)
    "knowledge",
    "notes",
    "instruction",
    "persist",
    "remember",
    "context.bank",
    "user.pref",
]

IMPORT_LOG_FILENAME = ".copilot-import-log.json"


# ── globalStorage パス取得 ──────────────────────────────────────────────────────

def get_global_storage_path() -> Path:
    """OSに応じた VSCode globalStorage のデフォルトパスを返す。"""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Code" / "User" / "globalStorage"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / "globalStorage"
    else:  # Linux / その他
        return Path.home() / ".config" / "Code" / "User" / "globalStorage"


def find_copilot_dirs(global_storage: Path) -> list[Path]:
    """globalStorage 内の Copilot 拡張機能ディレクトリを返す。"""
    found = []
    if not global_storage.is_dir():
        return found
    for ext_id in COPILOT_EXT_IDS:
        candidate = global_storage / ext_id
        if candidate.is_dir() and candidate not in found:
            found.append(candidate)
    # 上記に含まれなかった "copilot" を含むディレクトリも収集
    for d in sorted(global_storage.iterdir()):
        if d.is_dir() and "copilot" in d.name.lower() and d not in found:
            found.append(d)
    return found


# ── SQLite からメモリエントリを抽出 ────────────────────────────────────────────

def list_all_keys(db_path: Path) -> list[tuple[str, str]]:
    """state.vscdb の全キーと値の先頭100文字を返す（調査用）。"""
    rows = []
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM ItemTable ORDER BY key")
        for key, value in cursor.fetchall():
            preview = str(value)[:100].replace("\n", " ")
            rows.append((key, preview))
        conn.close()
    except sqlite3.Error as e:
        print(f"  [SQLite Error] {db_path}: {e}", file=sys.stderr)
    return rows


def query_sqlite_memories(db_path: Path) -> list[dict]:
    """state.vscdb の ItemTable からメモリ関連エントリを抽出する。"""
    if not db_path.exists():
        return []

    results = []
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM ItemTable")
        rows = cursor.fetchall()
        conn.close()
    except sqlite3.Error as e:
        print(f"  [SQLite Error] {db_path}: {e}", file=sys.stderr)
        return []

    for key, value in rows:
        key_lower = key.lower()
        if not any(pat.lower() in key_lower for pat in MEMORY_KEY_PATTERNS):
            continue

        source_info = {"source_key": key, "source_db": str(db_path)}

        # JSON として解析を試みる
        try:
            data = json.loads(value)
            entries = _extract_entries_from_json(key, data)
            for e in entries:
                e.update(source_info)
            results.extend(entries)
        except (json.JSONDecodeError, TypeError):
            # テキスト値はそのまま1エントリとして扱う
            text = str(value).strip()
            if len(text) > 10:
                results.append({
                    "id": f"key:{_slug(key)}",
                    "text": text,
                    **source_info,
                })

    return results


def _extract_entries_from_json(key: str, data) -> list[dict]:
    """JSON データ（dict / list / string）からメモリエントリのリストを抽出する。"""
    entries = []

    if isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict):
                e = _normalize_entry(item)
                if e:
                    entries.append(e)
            elif isinstance(item, str) and len(item.strip()) > 5:
                entries.append({"id": f"{_slug(key)}-{i}", "text": item.strip()})

    elif isinstance(data, dict):
        # 直接エントリ形式か確認
        direct = _normalize_entry(data)
        if direct:
            entries.append(direct)
            return entries

        # ネストされたリスト形式を探す
        list_keys = ("memories", "entries", "items", "notes", "instructions",
                     "contexts", "bank", "threads", "records")
        for lk in list_keys:
            if lk in data and isinstance(data[lk], list):
                for i, item in enumerate(data[lk]):
                    if isinstance(item, dict):
                        e = _normalize_entry(item)
                        if e:
                            entries.append(e)
                    elif isinstance(item, str) and len(item.strip()) > 5:
                        entries.append({"id": f"{lk}-{i}", "text": item.strip()})
                if entries:
                    return entries

        # フラットなキー:テキスト形式
        for k, v in data.items():
            if isinstance(v, str) and len(v.strip()) > 10 and not k.startswith("_"):
                entries.append({"id": _slug(k), "text": v.strip()})

    elif isinstance(data, str) and len(data.strip()) > 10:
        entries.append({"id": _slug(key), "text": data.strip()})

    return entries


def _normalize_entry(item: dict) -> dict | None:
    """多様なフォーマットの dict を統一エントリ形式に変換する。"""
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

    entry_id = str(
        item.get("id") or item.get("uuid") or item.get("memoryId")
        or item.get("noteId") or item.get("threadId") or ""
    )

    created = str(
        item.get("createdAt") or item.get("created_at") or item.get("timestamp")
        or item.get("date") or item.get("created") or ""
    )

    tags_raw = item.get("tags") or item.get("labels") or item.get("categories") or []
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    elif isinstance(tags_raw, list):
        tags = [str(t) for t in tags_raw if t]
    else:
        tags = []

    title = str(item.get("title") or item.get("name") or item.get("summary") or "")

    return {
        "id": entry_id,
        "text": text,
        "title": title,
        "created": created,
        "tags": tags,
    }


# ── JSON ファイルをスキャン ────────────────────────────────────────────────────

def scan_json_files(ext_dir: Path) -> list[dict]:
    """拡張機能ディレクトリ内のメモリ関連 JSON ファイルをスキャンする。"""
    results = []
    name_patterns = [
        "*memor*.json", "NES*.json", "*notes*.json",
        "*instruction*.json", "*persist*.json", "*knowledge*.json",
        "*remember*.json",
    ]

    found_files: set[Path] = set()
    for pattern in name_patterns:
        for f in ext_dir.rglob(pattern):
            found_files.add(f)

    for json_file in sorted(found_files):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
            entries = _extract_entries_from_json(json_file.stem, data)
            for e in entries:
                e["source_file"] = str(json_file)
            results.extend(entries)
        except (json.JSONDecodeError, IOError):
            pass

    return results


# ── 重複管理 ──────────────────────────────────────────────────────────────────

def _make_stable_id(entry: dict) -> str:
    """エントリの安定したIDを生成する（重複検知用）。"""
    raw_id = entry.get("id", "")
    if raw_id:
        src_key = entry.get("source_key", entry.get("source_file", ""))
        return f"copilot:{src_key}:{raw_id}"
    # ID が空の場合はテキストの先頭64文字をキーに
    text_key = entry.get("text", "")[:64]
    return f"copilot:text:{hash(text_key) & 0xFFFFFFFF:08x}"


def load_import_log(category_dir: Path) -> set[str]:
    """インポート済みIDセットをログから読み込む。"""
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
    """インポート済みIDをログに追記保存する。"""
    log_path = category_dir / IMPORT_LOG_FILENAME
    existing = load_import_log(category_dir)
    all_ids = sorted(existing | new_ids)
    os.makedirs(category_dir, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({"imported_ids": all_ids}, f, ensure_ascii=False, indent=2)


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
    # タイトルがない/長すぎる → 本文の先頭から生成
    text = entry.get("text", "")
    first_line = text.split("\n")[0].strip()
    if len(first_line) <= 60:
        return first_line or "Copilot Memory"
    return first_line[:57] + "..."


def _make_summary(entry: dict) -> str:
    text = entry.get("text", "").strip()
    # 最初の2文を要約として使用
    sentences = re.split(r"(?<=[。\.！!？?\n])\s*", text)
    summary = " ".join(s.strip() for s in sentences[:2] if s.strip())[:200]
    return summary or text[:200]


def import_entry(entry: dict, scope: str, dry_run: bool) -> bool:
    """1エントリを ltm-use 形式に変換して保存する。True = 成功（または dry-run で対象）。"""
    text = entry.get("text", "").strip()
    if not text:
        return False

    title = _make_title(entry)
    summary = _make_summary(entry)
    tags = entry.get("tags", []) + ["copilot-memory", "imported"]

    source_label = entry.get("source_key") or entry.get("source_file", "VSCode Copilot")
    context = f"VSCode Copilot Memory からインポート（ソース: {source_label}）"

    if dry_run:
        print(f"  [DRY-RUN] タイトル : {title}")
        print(f"            要約    : {summary[:80]}{'...' if len(summary) > 80 else ''}")
        print(f"            タグ    : {', '.join(tags)}")
        print()
        return True

    filepath = _ltm_save(
        category="copilot-memory",
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
        description="VSCode Copilot Memory を ltm-use にインポートする"
    )
    parser.add_argument(
        "--storage",
        help="VSCode globalStorage のパスを明示指定（省略時は OS 標準パスを使用）",
    )
    parser.add_argument(
        "--scope", default="home", choices=["workspace", "home"],
        help="インポート先スコープ: home（デフォルト・プロジェクト横断）/ workspace（プロジェクト固有）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="何が見つかるか確認するだけ（ファイルを作成しない）",
    )
    parser.add_argument(
        "--list-keys", action="store_true",
        help="Copilot 拡張の state.vscdb にある全キーを表示する（フォーマット調査用）",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="インポート済みIDを無視して再インポートする",
    )
    args = parser.parse_args()

    global_storage = Path(args.storage) if args.storage else get_global_storage_path()

    print(f"globalStorage パス: {global_storage}")

    if not global_storage.is_dir():
        print(f"[ERROR] globalStorage が見つかりません: {global_storage}", file=sys.stderr)
        print("  --storage オプションでパスを指定してください。", file=sys.stderr)
        sys.exit(1)

    copilot_dirs = find_copilot_dirs(global_storage)
    if not copilot_dirs:
        print("[ERROR] Copilot 拡張機能ディレクトリが見つかりません。", file=sys.stderr)
        print("  GitHub Copilot Chat が VSCode にインストールされているか確認してください。",
              file=sys.stderr)
        sys.exit(1)

    print(f"Copilot 拡張ディレクトリ: {[str(d) for d in copilot_dirs]}")
    print()

    # --list-keys モード
    if args.list_keys:
        for ext_dir in copilot_dirs:
            db_path = ext_dir / "state.vscdb"
            if not db_path.exists():
                continue
            print(f"=== {db_path} ===")
            for key, preview in list_all_keys(db_path):
                print(f"  {key}")
                print(f"    {preview}")
            print()
        return

    # 全エントリを収集
    all_entries: list[dict] = []
    for ext_dir in copilot_dirs:
        # SQLite からメモリを抽出
        db_path = ext_dir / "state.vscdb"
        sqlite_entries = query_sqlite_memories(db_path)
        if sqlite_entries:
            print(f"SQLite ({ext_dir.name}): {len(sqlite_entries)} エントリ検出")
        all_entries.extend(sqlite_entries)

        # JSON ファイルからメモリを抽出
        json_entries = scan_json_files(ext_dir)
        if json_entries:
            print(f"JSON   ({ext_dir.name}): {len(json_entries)} エントリ検出")
        all_entries.extend(json_entries)

    if not all_entries:
        print("[INFO] Copilot メモリが見つかりませんでした。")
        print("  ヒント: --list-keys で state.vscdb のキー一覧を確認できます。")
        return

    print(f"\n合計 {len(all_entries)} エントリを検出しました。\n")

    # インポート済みチェック
    memory_dir = Path(memory_utils.get_memory_dir(args.scope))
    category_dir = memory_dir / "copilot-memory"
    imported_ids = set() if args.force else load_import_log(category_dir)

    skipped = 0
    imported = 0
    new_ids: set[str] = set()

    for entry in all_entries:
        stable_id = _make_stable_id(entry)

        if stable_id in imported_ids:
            skipped += 1
            continue

        success = import_entry(entry, args.scope, args.dry_run)
        if success:
            imported += 1
            if not args.dry_run:
                new_ids.add(stable_id)

    # インポートログを更新
    if not args.dry_run and new_ids:
        save_import_log(category_dir, new_ids)

    # サマリー
    print(f"\n=== 完了 ===")
    if args.dry_run:
        print(f"  インポート対象: {imported} エントリ（dry-run のため実際には保存されていません）")
    else:
        print(f"  インポート完了: {imported} エントリ → {args.scope} スコープ")
    if skipped:
        print(f"  スキップ（重複）: {skipped} エントリ")
    if not args.dry_run and imported:
        print(f"\n  次のステップ:")
        print(f"    python recall_memory.py 'copilot-memory' --scope {args.scope}  # インポート結果を確認")
        print(f"    python list_memories.py --scope {args.scope}                    # 一覧を表示")


if __name__ == "__main__":
    main()
