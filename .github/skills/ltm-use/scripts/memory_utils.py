"""
memory_utils.py - 記憶スクリプト共通ユーティリティ

scripts/ 内の全スクリプトから import して使う。
Python標準ライブラリのみ使用（外部依存なし）。
"""

import datetime
import json
import math
import os
import re
import subprocess
import sys


# ─── パス定数 ────────────────────────────────────────────────

def get_skill_dir() -> str:
    """このファイルの2階層上 = SKILL_DIR"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# registry.py の __file__ ベースのパス解決を利用
# このスクリプト: {skill_home}/ltm-use/scripts/memory_utils.py
# skill_home = scripts/../.. の2段上
_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_HOME = os.path.dirname(os.path.dirname(_HERE))
_REG_SCRIPTS = os.path.join(_SKILL_HOME, "git-skill-manager", "scripts")
if _REG_SCRIPTS not in sys.path:
    sys.path.insert(0, _REG_SCRIPTS)
from registry import _agent_home, _registry_path as _get_registry_path

HOME_MEMORY_ROOT = os.path.join(_agent_home(), "memory")

SCOPE_DIRS = {
    "workspace": os.path.join(get_skill_dir(), "memories"),
    "home":      os.path.join(HOME_MEMORY_ROOT, "home"),
    "shared":    os.path.join(HOME_MEMORY_ROOT, "shared"),  # 後方互換用レガシーパス
}

REGISTRY_PATH = _get_registry_path()
SHARED_BASE = os.path.join(HOME_MEMORY_ROOT, "shared")

DEFAULT_CONFIG = {
    "shared_remote": "",
    "shared_branch": "main",
    "auto_promote_threshold": 85,
    "semi_auto_promote_threshold": 70,
    "cleanup_inactive_days": 30,
    "cleanup_archived_days": 60,
}

INDEX_FILENAME = ".memory-index.json"
CORPUS_FILENAME = ".memory-corpus.json"


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


def _get_home_dir() -> str:
    """ホームメモリールートを返す（home/shared 共通の親ディレクトリ）。

    build_index / cleanup / rate の scope_label 計算で使用する。
    """
    return HOME_MEMORY_ROOT


# ─── skill-registry.json 連携 ────────────────────────────────

def load_registry() -> dict:
    """skill-registry.json を読み込む（存在しなければ空を返す）"""
    if os.path.exists(REGISTRY_PATH):
        try:
            with open(REGISTRY_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def get_shared_repos() -> list[dict]:
    """skill-registry.json の repositories から shared memory 設定を返す。

    skill-registry.json にリポジトリが未登録の場合は、
    config.json の shared_remote をフォールバックとして使用する。

    各エントリの構造:
      name        : リポジトリ名
      url         : git remote URL
      branch      : ブランチ名
      readonly    : 読み取り専用フラグ（True の場合は commit/push 不可）
      memory_root : リポジトリ内のメモリールートパス（省略時: "memories"）
      local_dir   : ~/.copilot/memory/shared/<name>/  （git clone 先のリポジトリルート）
      memory_dir  : local_dir/memory_root  （実際の .md ファイルが置かれるディレクトリ）
    """
    reg = load_registry()
    repos = reg.get("repositories", [])
    if repos:
        result = []
        for repo in repos:
            url = repo.get("url", "")
            if not url:
                continue
            name = repo.get("name", "origin")
            local_dir = os.path.join(SHARED_BASE, name)
            memory_root = repo.get("memory_root", "memories")
            memory_dir = os.path.join(local_dir, memory_root) if memory_root else local_dir
            result.append({
                "name": name,
                "url": url,
                "branch": repo.get("branch", "main"),
                "readonly": bool(repo.get("readonly", False)),
                "memory_root": memory_root,
                "local_dir": local_dir,
                "memory_dir": memory_dir,
                "priority": repo.get("priority", 99),
            })
        result.sort(key=lambda r: r["priority"])
        return result

    # フォールバック: config.json の shared_remote
    cfg = load_config()
    remote = cfg.get("shared_remote", "")
    if remote:
        # 旧形式の ~/.copilot/memory/shared/ が git リポジトリなら互換パスを使う
        old_shared = SCOPE_DIRS["shared"]
        if os.path.isdir(os.path.join(old_shared, ".git")):
            local_dir, memory_root = old_shared, ""
        else:
            local_dir, memory_root = os.path.join(SHARED_BASE, "default"), ""
        return [{
            "name": "default",
            "url": remote,
            "branch": cfg.get("shared_branch", "main"),
            "readonly": False,
            "memory_root": memory_root,
            "local_dir": local_dir,
            "memory_dir": local_dir,
            "priority": 1,
        }]
    return []


def get_primary_writable_repo() -> dict | None:
    """書き込み可能な最優先リポジトリを返す（promote/commit のターゲット）"""
    for repo in get_shared_repos():
        if not repo["readonly"]:
            return repo
    return None


def get_memory_dir(scope: str) -> str:
    """スコープのメモリーディレクトリを返す（shared は書き込み可能な優先リポジトリ）"""
    if scope == "shared":
        repo = get_primary_writable_repo()
        return repo["memory_dir"] if repo else SCOPE_DIRS["shared"]
    return SCOPE_DIRS.get(scope, SCOPE_DIRS["workspace"])


def get_memory_dirs(scope: str) -> list[str]:
    """scope='all' なら全スコープ、'shared' なら全リポジトリのディレクトリを返す"""
    if scope == "all":
        shared_dirs = [r["memory_dir"] for r in get_shared_repos()] or [SCOPE_DIRS["shared"]]
        return [SCOPE_DIRS["workspace"], SCOPE_DIRS["home"]] + shared_dirs
    if scope == "shared":
        repos = get_shared_repos()
        return [r["memory_dir"] for r in repos] if repos else [SCOPE_DIRS["shared"]]
    return [SCOPE_DIRS.get(scope, SCOPE_DIRS["workspace"])]


# ─── フロントマター ──────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """YAMLフロントマターをパースする（PyYAML不要のシンプル実装）

    対応形式:
      key: value          # 文字列
      key: "value"        # クォート文字列（コロン等を含む値に使用）
      key: 42             # 整数（負の値も可）
      key: [a, b, c]      # 文字列リスト（1行形式のみ）
      key: ""             # 空文字列

    非対応: 複数行ブロックスカラー、ネストオブジェクト、YAML リスト（- item 形式）
    → これらが必要な場合は手動で値を1行形式に記述すること
    """
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2].strip()
            for line in fm_text.splitlines():
                # インデント行（リスト項目 "  - item" など）とコメント行はスキップ
                stripped = line.lstrip()
                if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                    continue
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                # 空値
                if not val:
                    meta[key] = ""
                    continue
                # 文字列リスト [a, b]
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1]
                    meta[key] = [v.strip().strip('"') for v in inner.split(",") if v.strip()]
                # 整数（負の値も対応）
                elif re.fullmatch(r"-?\d+", val):
                    meta[key] = int(val)
                # クォート付き文字列（コロン等が含まれる値を安全に扱う）
                elif val.startswith('"') and val.endswith('"'):
                    meta[key] = val[1:-1]
                # 非クォート文字列
                else:
                    meta[key] = val
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
        text = re.sub(
            rf"^{escaped}:.*",
            replacement,
            text,
            flags=re.MULTILINE,
        )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)


def update_file_with_body(filepath: str, meta_updates: dict, new_body: str) -> None:
    """フロントマターと本文を同時に更新する（修正ログ追記などに使用）"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    for field, value in meta_updates.items():
        escaped = re.escape(field)
        if isinstance(value, int):
            repl = f"{field}: {value}"
        elif isinstance(value, list):
            inner = ", ".join(f'"{v}"' if " " in v else v for v in value)
            repl = f"{field}: [{inner}]"
        else:
            repl = f'{field}: "{value}"'
        text = re.sub(rf"^{escaped}:.*", repl, text, flags=re.MULTILINE)
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = "---" + parts[1] + "---\n\n" + new_body.lstrip()
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)


# ─── スコアリング v2 ─────────────────────────────────────────

def compute_share_score(meta: dict, body: str) -> int:
    """共有価値スコアを計算する（0〜100点）

    参照頻度      : access_count * 8 点（上限 32）
    タグ豊富さ    : tags 数 * 5 点（上限 20）
    情報量        : 本文 100 文字ごとに 1 点（上限 18）
    アクティブ    : status == active なら 10 点
    ユーザー評価  : user_rating * 10 点（-20〜+20）
    修正ペナルティ: correction_count * 5 点（最大 -20）
    合計を [0, 100] にクランプ
    """
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    access_count = int(meta.get("access_count", 0))
    user_rating = int(meta.get("user_rating", 0))
    correction_count = int(meta.get("correction_count", 0))
    status = meta.get("status", "active")

    score = 0
    score += min(access_count * 8, 32)
    score += min(len(tags) * 5, 20)
    score += min(len(body) // 100, 18)
    score += 10 if status == "active" else 0
    score += max(min(user_rating * 10, 20), -20)
    score -= min(correction_count * 5, 20)
    return max(0, min(100, score))


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


# ─── v5.0.0 脳科学モデル ─────────────────────────────────────

def compute_retention_score(meta: dict) -> float:
    """エビングハウス忘却曲線に基づく retention スコアを計算する（0.0〜1.0）。

    retention = e^(-0.693 * days_since_access / half_life)
    half_life = base_half_life * importance_factor * repetition_factor

    importance_factor: critical=∞(→1.0固定), high=3.0, normal=1.0, low=0.5
    repetition_factor: 1 + ln(1 + access_count)
    """
    importance = meta.get("importance", "normal")
    if importance == "critical":
        return 1.0

    importance_factor = {"high": 3.0, "normal": 1.0, "low": 0.5}.get(importance, 1.0)
    access_count = int(meta.get("access_count", 0))
    repetition_factor = 1.0 + math.log(1.0 + access_count)

    cfg = load_config()
    base_half_life = float(cfg.get("retention_base_half_life", 30))
    half_life = base_half_life * importance_factor * repetition_factor

    # 最終アクセス日を優先し、なければ更新日 → 作成日
    last = (meta.get("last_accessed") or meta.get("updated") or meta.get("created") or "")
    days = days_since(last) if last else 365

    retention = math.exp(-0.693 * days / half_life)
    return max(0.0, min(1.0, retention))


def detect_memory_type(content: str, title: str = "", summary: str = "") -> str:
    """コンテンツから memory_type を自動推定する（procedural > episodic > semantic）。

    algorithms.md の分類ルールを実装。
    """
    combined = (title + " " + summary + " " + content).lower()
    procedural_kw = ["手順", "ステップ", "方法", "やり方", "手順書", "ワークフロー"]
    if any(k in combined for k in procedural_kw) or re.search(r'^\d+\.\s+', content, re.MULTILINE):
        return "procedural"
    episodic_kw = ["したとき", "で起きた", "が発生した", "を発見した", "今日", "昨日", "さっき", "先ほど"]
    if any(k in combined for k in episodic_kw) or re.search(r'\d{4}-\d{2}-\d{2}', content):
        return "episodic"
    return "semantic"


def detect_importance(content: str, title: str = "", summary: str = "") -> str:
    """コンテンツから importance を自動推定する（algorithms.md のキーワードルール）。"""
    combined = (title + " " + summary + " " + content).lower()
    critical_kw = ["本番障害", "セキュリティ", "データ損失", "脆弱性", "インシデント", "絶対に", "致命的", "重大な"]
    high_kw = ["設計決定", "アーキテクチャ", "重要", "再発防止", "根本原因", "ベストプラクティス", "パフォーマンス"]
    low_kw = ["仮", "試し", "とりあえず", "一時的", "wip"]
    if any(k in combined for k in critical_kw):
        return "critical"
    if any(k in combined for k in high_kw):
        return "high"
    if any(k in combined for k in low_kw):
        return "low"
    return "normal"


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
            if fname.endswith(".md"):
                yield os.path.join(root, fname), rel_cat


# ─── インデックス ────────────────────────────────────────────

def get_index_path(memory_dir: str) -> str:
    return os.path.join(memory_dir, INDEX_FILENAME)


def build_index_entry(filepath: str, memory_dir: str) -> dict:
    """ファイルを読み込んでインデックスエントリを生成する"""
    rel_path = os.path.relpath(filepath, memory_dir)
    with open(filepath, encoding="utf-8") as f:
        text = f.read()
    meta, _ = parse_frontmatter(text)
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "filepath": rel_path,
        "mtime": os.path.getmtime(filepath),
        "id": meta.get("id", ""),
        "title": meta.get("title", os.path.basename(filepath)),
        "summary": meta.get("summary", ""),
        "tags": tags,
        "status": meta.get("status", "active"),
        "scope": meta.get("scope", "workspace"),
        "share_score": int(meta.get("share_score", 0)),
        "access_count": int(meta.get("access_count", 0)),
        "correction_count": int(meta.get("correction_count", 0)),
        "user_rating": int(meta.get("user_rating", 0)),
        "created": meta.get("created", ""),
        "updated": meta.get("updated", ""),
    }


def load_index(memory_dir: str) -> dict:
    """インデックスファイルを読み込む（存在しなければ空を返す）"""
    path = get_index_path(memory_dir)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 2, "built_at": "", "count": 0, "entries": []}


def save_index(memory_dir: str, index: dict) -> None:
    """インデックスをファイルに書き込む"""
    os.makedirs(memory_dir, exist_ok=True)
    path = get_index_path(memory_dir)
    index["built_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    index["count"] = len(index.get("entries", []))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def refresh_index(memory_dir: str) -> dict:
    """インデックスを増分更新して返す（mtime が変わったファイルのみ再読み込み）

    ファイルのstat比較のみで変更を検出し、変更分だけ読み直す。
    通常のフルスキャンより大幅に高速。
    """
    index = load_index(memory_dir)
    entries_by_rel = {e["filepath"]: e for e in index.get("entries", [])}
    current_rels: set[str] = set()
    needs_save = False

    for fpath, _ in iter_memory_files(memory_dir):
        rel = os.path.relpath(fpath, memory_dir)
        current_rels.add(rel)
        try:
            mtime = os.path.getmtime(fpath)
        except OSError:
            continue
        existing = entries_by_rel.get(rel)
        if existing and existing.get("mtime", 0) >= mtime:
            continue  # up to date
        try:
            entries_by_rel[rel] = build_index_entry(fpath, memory_dir)
            needs_save = True
        except (OSError, IOError):
            pass

    # 削除されたファイルのエントリを除去
    removed = set(entries_by_rel.keys()) - current_rels
    for r in removed:
        del entries_by_rel[r]
        needs_save = True

    index["entries"] = list(entries_by_rel.values())
    if needs_save or not index.get("built_at"):
        save_index(memory_dir, index)
    return index


def update_index_entry(memory_dir: str, filepath: str) -> None:
    """単一ファイルのインデックスエントリを更新する（save/rate/delete 後に呼ぶ）"""
    index = load_index(memory_dir)
    entries_by_rel = {e["filepath"]: e for e in index.get("entries", [])}
    rel = os.path.relpath(filepath, memory_dir)
    if os.path.exists(filepath):
        try:
            entries_by_rel[rel] = build_index_entry(filepath, memory_dir)
        except (OSError, IOError):
            return
    else:
        entries_by_rel.pop(rel, None)
    index["entries"] = list(entries_by_rel.values())
    save_index(memory_dir, index)


def find_memory_dir(filepath: str) -> str | None:
    """ファイルパスからそのスコープのメモリーディレクトリを特定する"""
    abs_path = os.path.abspath(filepath)
    candidates = [SCOPE_DIRS["workspace"], SCOPE_DIRS["home"]]
    candidates += [r["memory_dir"] for r in get_shared_repos()]
    # 旧形式の shared ディレクトリも確認
    if SCOPE_DIRS["shared"] not in candidates:
        candidates.append(SCOPE_DIRS["shared"])
    for memory_dir in candidates:
        if abs_path.startswith(os.path.abspath(memory_dir) + os.sep):
            return memory_dir
    return None


# ─── Git ヘルパー ────────────────────────────────────────────

def git_pull_repo(repo: dict) -> tuple[bool, str]:
    """リポジトリを git pull する。local_dir が存在しなければ clone する。

    memory_root が設定されている場合は sparse-checkout を使用して
    そのフォルダのみを取得し、リポジトリ全体のクローンを避ける。
    """
    local_dir = repo["local_dir"]
    remote = repo.get("url", "")
    branch = repo.get("branch", "main")
    memory_root = repo.get("memory_root", "")
    if not remote:
        return False, "URL が設定されていません"
    try:
        if os.path.isdir(os.path.join(local_dir, ".git")):
            result = subprocess.run(
                ["git", "-C", local_dir, "pull", "origin", branch],
                capture_output=True, text=True, timeout=30,
            )
        else:
            os.makedirs(os.path.dirname(local_dir), exist_ok=True)
            if memory_root:
                # sparse-checkout でメモリフォルダのみ取得（リポジトリ全体を避ける）
                result = subprocess.run(
                    ["git", "clone", "--no-checkout", "--filter=blob:none",
                     "--branch", branch, remote, local_dir],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    subprocess.run(
                        ["git", "-C", local_dir, "sparse-checkout", "set", memory_root],
                        capture_output=True, timeout=10,
                    )
                    result = subprocess.run(
                        ["git", "-C", local_dir, "checkout"],
                        capture_output=True, text=True, timeout=30,
                    )
                if result.returncode != 0:
                    # sparse-checkout 非対応の古い git へのフォールバック（フルclone）
                    import shutil as _shutil
                    print(f"警告: sparse-checkout に失敗しました。リポジトリ全体をクローンします。"
                          f"（Git 2.25+ が必要）", file=__import__("sys").stderr)
                    _shutil.rmtree(local_dir, ignore_errors=True)
                    os.makedirs(os.path.dirname(local_dir), exist_ok=True)
                    result = subprocess.run(
                        ["git", "clone", "--branch", branch, remote, local_dir],
                        capture_output=True, text=True, timeout=60,
                    )
            else:
                result = subprocess.run(
                    ["git", "clone", "--branch", branch, remote, local_dir],
                    capture_output=True, text=True, timeout=60,
                )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "git コマンドがタイムアウトしました"
    except FileNotFoundError:
        return False, "git コマンドが見つかりません"


def git_commit_repo(repo: dict, message: str) -> tuple[bool, str]:
    """リポジトリのメモリー変更を git commit する（push は行わない）"""
    if repo.get("readonly"):
        return False, "読み取り専用リポジトリへのコミットはできません"
    local_dir = repo["local_dir"]
    memory_root = repo.get("memory_root", "")
    add_path = memory_root if memory_root else "."
    try:
        subprocess.run(["git", "-C", local_dir, "add", add_path],
                       capture_output=True, timeout=10)
        result = subprocess.run(
            ["git", "-C", local_dir, "commit", "-m", message],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        if "nothing to commit" in result.stdout + result.stderr:
            return True, "変更なし（コミット不要）"
        return False, result.stderr.strip()
    except Exception as e:
        return False, str(e)


def git_push_repo(repo: dict) -> tuple[bool, str]:
    """リポジトリを git push する"""
    if repo.get("readonly"):
        return False, "読み取り専用リポジトリへの push はできません"
    local_dir = repo["local_dir"]
    branch = repo.get("branch", "main")
    try:
        result = subprocess.run(
            ["git", "-C", local_dir, "push", "origin", branch],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "git コマンドがタイムアウトしました"
    except FileNotFoundError:
        return False, "git コマンドが見つかりません"


def git_pull_shared(shared_dir: str, remote: str, branch: str) -> tuple[bool, str]:
    """後方互換ラッパー: git_pull_repo を呼ぶ"""
    return git_pull_repo({"local_dir": shared_dir, "url": remote, "branch": branch})


def git_commit_shared(shared_dir: str, message: str) -> tuple[bool, str]:
    """後方互換ラッパー: git_commit_repo を呼ぶ"""
    return git_commit_repo(
        {"local_dir": shared_dir, "readonly": False, "memory_root": ""}, message
    )
