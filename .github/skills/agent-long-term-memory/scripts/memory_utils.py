"""
memory_utils.py - 記憶スクリプト共通ユーティリティ

scripts/ 内の全スクリプトから import して使う。
Python標準ライブラリのみ使用（外部依存なし）。
"""

import datetime
import json
import os
import re
import subprocess


# ─── パス定数 ────────────────────────────────────────────────

def get_skill_dir() -> str:
    """このファイルの2階層上 = SKILL_DIR"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


HOME_MEMORY_ROOT = os.path.expanduser("~/.agent-memory")

SCOPE_DIRS = {
    "workspace": os.path.join(get_skill_dir(), "memories"),
    "home":      os.path.join(HOME_MEMORY_ROOT, "workspace"),
    "shared":    os.path.join(HOME_MEMORY_ROOT, "shared"),
}

DEFAULT_CONFIG = {
    "shared_remote": "",          # git remote URL（空なら git 連携無効）
    "shared_branch": "main",
    "auto_promote_threshold": 85,      # この値以上で自動昇格
    "semi_auto_promote_threshold": 70,  # この値以上で昇格候補として提示
    "cleanup_inactive_days": 30,        # access_count=0 の記憶の保持日数
    "cleanup_archived_days": 60,        # archived 記憶の保持日数
}


def load_config() -> dict:
    path = os.path.join(HOME_MEMORY_ROOT, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    os.makedirs(HOME_MEMORY_ROOT, exist_ok=True)
    path = os.path.join(HOME_MEMORY_ROOT, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_memory_dir(scope: str) -> str:
    return SCOPE_DIRS.get(scope, SCOPE_DIRS["workspace"])


def get_memory_dirs(scope: str) -> list[str]:
    """scope='all' なら全ディレクトリを返す"""
    if scope == "all":
        return list(SCOPE_DIRS.values())
    return [get_memory_dir(scope)]


# ─── フロントマター ──────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """YAMLフロントマターをパースする（PyYAML不要のシンプル実装）"""
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2].strip()
            for line in fm_text.splitlines():
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                # 文字列リスト [a, b]
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1]
                    meta[key] = [v.strip().strip('"') for v in inner.split(",") if v.strip()]
                # 整数
                elif re.fullmatch(r"-?\d+", val):
                    meta[key] = int(val)
                # クォート付き文字列
                else:
                    meta[key] = val.strip('"')
    return meta, body


def update_frontmatter_fields(filepath: str, updates: dict) -> None:
    """フロントマター内の特定フィールドを上書きする（型を保持）"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    for field, value in updates.items():
        escaped = re.escape(field)
        if isinstance(value, int):
            replacement = f"{field}: {value}"
        elif isinstance(value, list):
            inner = ", ".join(f'"{v}"' if " " in v else v for v in value)
            replacement = f"{field}: [{inner}]"
        else:
            replacement = f'{field}: "{value}"'
        # フロントマター内の該当行だけ置換（行頭マッチ）
        text = re.sub(
            rf"^{escaped}:.*",
            replacement,
            text,
            flags=re.MULTILINE,
        )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)


# ─── スコアリング ────────────────────────────────────────────

def compute_share_score(meta: dict, body: str) -> int:
    """共有価値スコアを計算する（0〜90点）

    - 参照頻度  : access_count * 10 点（上限 40）
    - タグ豊富さ: tags 数 * 5 点（上限 20）
    - 情報量    : 本文 100 文字ごとに 1 点（上限 20）
    - アクティブ: status == active なら 10 点
    """
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    access_count = int(meta.get("access_count", 0))
    status = meta.get("status", "active")

    score = 0
    score += min(access_count * 10, 40)
    score += min(len(tags) * 5, 20)
    score += min(len(body) // 100, 20)
    score += 10 if status == "active" else 0
    return score


# ─── 日付ユーティリティ ──────────────────────────────────────

def days_since(date_str: str) -> int:
    """ISO 8601 日付文字列から今日までの日数を返す"""
    if not date_str:
        return 0
    try:
        d = datetime.date.fromisoformat(date_str)
        return (datetime.date.today() - d).days
    except ValueError:
        return 0


def today_str() -> str:
    return datetime.date.today().isoformat()


# ─── ファイルスキャン ────────────────────────────────────────

def iter_memory_files(memory_dir: str, category: str = None):
    """memory_dir 以下の .md ファイルを (filepath, rel_category) で yield する"""
    if not os.path.isdir(memory_dir):
        return
    for root, dirs, files in os.walk(memory_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        rel_cat = os.path.relpath(root, memory_dir)
        if category and rel_cat != category:
            continue
        for fname in sorted(files):
            if fname.endswith(".md") and fname != ".gitkeep":
                yield os.path.join(root, fname), rel_cat


# ─── Git ヘルパー ────────────────────────────────────────────

def git_pull_shared(shared_dir: str, remote: str, branch: str) -> tuple[bool, str]:
    """shared_dir を git pull する。存在しなければ clone する"""
    if not remote:
        return False, "shared_remote が設定されていません（~/.agent-memory/config.json を確認）"
    try:
        if os.path.isdir(os.path.join(shared_dir, ".git")):
            result = subprocess.run(
                ["git", "-C", shared_dir, "pull", "origin", branch],
                capture_output=True, text=True, timeout=30,
            )
        else:
            os.makedirs(os.path.dirname(shared_dir), exist_ok=True)
            result = subprocess.run(
                ["git", "clone", "--branch", branch, remote, shared_dir],
                capture_output=True, text=True, timeout=60,
            )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "git コマンドがタイムアウトしました"
    except FileNotFoundError:
        return False, "git コマンドが見つかりません"


def git_commit_shared(shared_dir: str, message: str) -> tuple[bool, str]:
    """shared_dir の変更を git commit する（push は行わない）"""
    try:
        subprocess.run(["git", "-C", shared_dir, "add", "."],
                       capture_output=True, timeout=10)
        result = subprocess.run(
            ["git", "-C", shared_dir, "commit", "-m", message],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        if "nothing to commit" in result.stdout + result.stderr:
            return True, "変更なし（コミット不要）"
        return False, result.stderr.strip()
    except Exception as e:
        return False, str(e)
