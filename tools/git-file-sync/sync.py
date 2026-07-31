#!/usr/bin/env python3
"""
git-file-sync — git リポジトリを同期ハブとして使うファイル同期ツール。

概要:
  ローカルフォルダと「git リポジトリ上のフォルダ」を対 (ペア) にして登録し、
  定期的に双方向同期する。git のクローンを介して pull / push することで、
  複数マシン間でフォルダ内容を同期できる（簡易 Dropbox のような使い方）。

特徴:
  - 複数のペア (ローカル ⇔ リポジトリ内サブフォルダ) を登録・管理
  - 同期方向を選択可能: bidirectional (双方向) / pull (git→ローカル) / push (ローカル→git)
  - ペアごとに定期ポーリング間隔を設定
  - 前回同期スナップショットを基準にした 3-way 差分で、
    「ローカルだけ変更」「リポジトリだけ変更」「両方変更 (コンフリクト)」を判定
  - コンフリクト時の採用ポリシーを設定可能:
      mine   (= local / ours)   … 自分 (ローカル) を採用
      theirs (= remote)          … 他人 (リポジトリ) を採用
    グローバル既定値とペア単位の上書きに対応
  - コンフリクトで負けた側は .conflict バックアップとして残す（任意）
  - 削除も伝播 (ローカルで消したファイルはリポジトリからも削除、逆も同様)

依存:
  - PyYAML        (pip install pyyaml)  ※ JSON 設定なら不要
  - git コマンド   (PATH 上に存在すること)

使い方:
  python3 sync.py [--config CONFIG] [--once] [--sync PAIR] [--dry-run]

  --once         全ペアを 1 回同期して終了
  --sync PAIR    指定ペアのみ 1 回同期して終了
  --dry-run      実際のコピー/削除/コミットを行わず、予定だけ表示
  --fast         高速スキャン（前回から差分のあったファイルのみ読む）を全ペアに適用
  --config       設定ファイルのパス（省略時は既定の探索順）

インタラクティブコマンド (--once / --sync なしで起動した場合):
  sync [<pair>]        全ペアまたは指定ペアを今すぐ同期
  list                 登録済みペアを表示
  status               最終同期時刻とステータスを表示
  interval <pair> <m>   ポーリング間隔 (分) を変更
  policy <pair> <p>      コンフリクトポリシーを変更 (mine / theirs)
  scan <pair> <mode>     スキャンモードを変更 (content / fast)
  direction <pair> <d>   同期方向を変更 (bidirectional / pull / push)
  maxage <pair> <dur>    pull の更新日時フィルタを変更 (例 7d / 48h / none)
  help                  コマンド一覧
  quit                  終了

同期方向 (direction):
  bidirectional (既定)  ローカルと git を双方向に同期する。
  pull                  git → ローカルのみ。git を正とし、リポジトリには一切
                        書き込まない（削除もしない）。ローカルの削除・編集は尊重し、
                        git 側が更新されたファイルだけを（再）ダウンロードする。
                        「git=全アーカイブ、ローカル=最近更新分の作業キャッシュ」
                        （古いファイルを別プログラムが削除する構成）に向く。
                        max_age を設定すると、git 側の最終コミット時刻が古い
                        ファイルは取得しない（初回の全件ダウンロードを抑制）。
  push                  ローカル → git のみ。ローカルを正とし、ローカルには
                        書き込まない。

スキャンモード:
  content (既定)  毎サイクル全ファイルのハッシュを計算する。確実だが大量
                  ファイルでは遅い。
  fast            mtime+サイズが前回同期と同じファイルはハッシュ計算を省略し、
                  「前回から差分のあったファイルのみ」を読む。高速だが、mtime を
                  保ったまま中身が変わるケースは検知できない。
                  --fast で全ペアに一括適用可。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 依存チェック
# ---------------------------------------------------------------------------

try:
    import yaml
except ImportError:  # JSON 設定のみ使う場合は yaml 不要なので遅延エラーにする
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# ログ
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("git-file-sync")

# ---------------------------------------------------------------------------
# 設定の探索・読み込み
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_NAMES = [
    "git-file-sync.yaml",
    "git-file-sync.yml",
    "git-file-sync.json",
]

# コンフリクトポリシーのエイリアスを正規化
POLICY_MINE = "mine"
POLICY_THEIRS = "theirs"
_POLICY_ALIASES = {
    "mine": POLICY_MINE,
    "local": POLICY_MINE,
    "ours": POLICY_MINE,
    "self": POLICY_MINE,
    "theirs": POLICY_THEIRS,
    "remote": POLICY_THEIRS,
    "repo": POLICY_THEIRS,
    "other": POLICY_THEIRS,
}


def normalize_policy(value: str | None, default: str = POLICY_THEIRS) -> str:
    if not value:
        return default
    norm = _POLICY_ALIASES.get(str(value).strip().lower())
    if norm is None:
        log.warning("不明なコンフリクトポリシー %r → 既定の %r を使用", value, default)
        return default
    return norm


# スキャンモード（変更検知方式）
SCAN_CONTENT = "content"  # 毎回全ファイルをハッシュ（確実・既定）
SCAN_FAST = "fast"        # mtime+サイズが一致するファイルはハッシュ省略（高速）
_SCAN_ALIASES = {
    "content": SCAN_CONTENT,
    "full": SCAN_CONTENT,
    "hash": SCAN_CONTENT,
    "accurate": SCAN_CONTENT,
    "fast": SCAN_FAST,
    "incremental": SCAN_FAST,
    "quick": SCAN_FAST,
    "diff": SCAN_FAST,
    "mtime": SCAN_FAST,
}


def normalize_scan_mode(value: str | None, default: str = SCAN_CONTENT) -> str:
    if not value:
        return default
    norm = _SCAN_ALIASES.get(str(value).strip().lower())
    if norm is None:
        log.warning("不明なスキャンモード %r → 既定の %r を使用", value, default)
        return default
    return norm


# 同期方向
DIR_BIDIR = "bidirectional"  # 双方向（既定）
DIR_PULL = "pull"            # git → ローカルのみ（git が正・アーカイブは不変）
DIR_PUSH = "push"            # ローカル → git のみ
_DIR_ALIASES = {
    "bidirectional": DIR_BIDIR,
    "bidir": DIR_BIDIR,
    "both": DIR_BIDIR,
    "two-way": DIR_BIDIR,
    "sync": DIR_BIDIR,
    "pull": DIR_PULL,
    "down": DIR_PULL,
    "download": DIR_PULL,
    "mirror": DIR_PULL,
    "repo-to-local": DIR_PULL,
    "push": DIR_PUSH,
    "up": DIR_PUSH,
    "upload": DIR_PUSH,
    "local-to-repo": DIR_PUSH,
}


def normalize_direction(value: str | None, default: str = DIR_BIDIR) -> str:
    if not value:
        return default
    norm = _DIR_ALIASES.get(str(value).strip().lower())
    if norm is None:
        log.warning("不明な同期方向 %r → 既定の %r を使用", value, default)
        return default
    return norm


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration_seconds(value: Any) -> float | None:
    """期間指定を秒に変換する。

    - 数値（int/float）は「日」とみなす（例: 7 → 7 日）
    - 文字列は末尾の単位 s/m/h/d/w を解釈（例: "48h", "7d", "90m", "2w"）。
      単位が無ければ「日」とみなす。
    - None / 空 / 解釈不能なら None（フィルタ無効）を返す。
    """
    if value is None:
        return None
    if isinstance(value, bool):  # True/False は無効扱い
        return None
    if isinstance(value, (int, float)):
        return float(value) * 86400.0
    s = str(value).strip().lower()
    if not s:
        return None
    unit = s[-1]
    if unit in _DURATION_UNITS:
        num = s[:-1].strip()
        try:
            return float(num) * _DURATION_UNITS[unit]
        except ValueError:
            log.warning("max_age を解釈できません: %r → 無効", value)
            return None
    try:
        return float(s) * 86400.0  # 単位なし → 日
    except ValueError:
        log.warning("max_age を解釈できません: %r → 無効", value)
        return None


def find_default_config(cwd: Path) -> Path | None:
    for name in DEFAULT_CONFIG_NAMES:
        for base in (cwd, Path.home()):
            p = base / name
            if p.is_file():
                return p
    return None


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        if path.suffix.lower() in (".yaml", ".yml"):
            if yaml is None:
                log.error("PyYAML が必要です。  pip install pyyaml")
                sys.exit(1)
            return yaml.safe_load(f) or {}
        return json.load(f)


# ---------------------------------------------------------------------------
# データモデル
# ---------------------------------------------------------------------------


class SyncPair:
    """ローカルフォルダ ⇔ リポジトリ内サブフォルダ の 1 ペア。"""

    def __init__(self, raw: dict[str, Any], defaults: dict[str, Any]):
        self.name: str = raw["name"]
        self.local_path: Path = Path(raw["local_path"]).expanduser()
        # リポジトリのワークツリーからの相対サブパス（"" でリポジトリ直下）
        self.repo_subpath: str = str(raw.get("repo_subpath", "")).strip("/")
        self.policy: str = normalize_policy(
            raw.get("conflict_policy"), defaults.get("conflict_policy", POLICY_THEIRS)
        )
        # 同期方向: bidirectional（既定）/ pull（git→ローカルのみ）/ push（ローカル→git のみ）
        self.direction: str = normalize_direction(
            raw.get("direction"), defaults.get("direction", DIR_BIDIR)
        )
        self.poll_interval_minutes: float = float(
            raw.get("poll_interval_minutes", defaults.get("poll_interval_minutes", 5))
        )
        self.keep_conflict_backup: bool = bool(
            raw.get("keep_conflict_backup", defaults.get("keep_conflict_backup", True))
        )
        # スキャンモード:
        #   content … 毎回全ファイルのハッシュを計算（確実だが遅い・既定）
        #   fast    … mtime+サイズが前回同期と同じファイルはハッシュ計算を省略
        self.scan_mode: str = normalize_scan_mode(
            raw.get("scan_mode"), defaults.get("scan_mode", SCAN_CONTENT)
        )
        # 取得の更新日時フィルタ（pull モード専用）:
        # git 側の「最後にそのファイルを変更したコミット時刻」が古いファイルは
        # 取得しない。"7d" / "48h" / "90m" / "2w" / 数値(=日) 等で指定。
        self.max_age_raw: Any = raw.get(
            "max_age", raw.get("pull_max_age",
                                defaults.get("max_age", defaults.get("pull_max_age")))
        )
        self.max_age_seconds: float | None = parse_duration_seconds(self.max_age_raw)
        if self.max_age_seconds is not None and self.direction != DIR_PULL:
            log.warning(
                "[%s] max_age は pull モードでのみ有効です（現在 direction=%s）。無視します",
                self.name, self.direction,
            )
            self.max_age_seconds = None
        # 無視パターン（相対パスの接頭辞 / glob 風の単純一致）
        self.ignore: list[str] = list(
            raw.get("ignore", defaults.get("ignore", [])) or []
        )
        # ランタイム状態
        self.last_sync: datetime | None = None
        self.last_status: str = "未同期"
        self._next_due: float = 0.0  # monotonic 時刻

    def repo_dir(self, repo_worktree: Path) -> Path:
        return repo_worktree / self.repo_subpath if self.repo_subpath else repo_worktree

    def is_due(self, now_mono: float) -> bool:
        return now_mono >= self._next_due

    def schedule_next(self, now_mono: float) -> None:
        self._next_due = now_mono + self.poll_interval_minutes * 60.0


# ---------------------------------------------------------------------------
# git 操作
# ---------------------------------------------------------------------------


class GitError(RuntimeError):
    pass


def run_git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(repo), *args]
    log.debug("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
        )
    return proc


def git_commit_times(worktree: Path, subpath: str) -> dict[str, int]:
    """subpath 以下の各ファイルについて「最後にそのファイルを変更したコミットの
    コミット時刻 (unix 秒)」を {subpath からの相対パス: 時刻} で返す。

    git の履歴（新しい順）を 1 回走査し、各パスの最初の出現（＝最新）を採用する。
    """
    args = ["log", "--format=__ts__:%ct", "--name-only", "--no-renames"]
    if subpath:
        args += ["--", subpath]
    proc = run_git(worktree, args, check=False)
    times: dict[str, int] = {}
    cur = 0
    prefix = (subpath.rstrip("/") + "/") if subpath else ""
    for line in proc.stdout.splitlines():
        if line.startswith("__ts__:"):
            try:
                cur = int(line[len("__ts__:"):])
            except ValueError:
                cur = 0
            continue
        path = line.strip()
        if not path:
            continue
        if prefix:
            if not path.startswith(prefix):
                continue
            rel = path[len(prefix):]
        else:
            rel = path
        if rel and rel not in times:  # 新しい順なので最初の出現が最新
            times[rel] = cur
    return times


class GitRepo:
    """同期ハブとなる git リポジトリのローカルクローンを管理する。"""

    def __init__(self, config: dict[str, Any]):
        self.remote: str | None = config.get("remote") or config.get("remote_url")
        self.branch: str = config.get("branch", "main")
        self.worktree: Path = Path(config["worktree"]).expanduser()
        self.commit_prefix: str = config.get("commit_message_prefix", "git-file-sync")
        self.auto_push: bool = bool(config.get("auto_push", True))
        self.author_name: str | None = config.get("author_name")
        self.author_email: str | None = config.get("author_email")

    # -- 初期化 ----------------------------------------------------------
    def ensure_clone(self, dry_run: bool = False) -> None:
        git_dir = self.worktree / ".git"
        if git_dir.exists():
            return
        if dry_run:
            log.info("[dry-run] clone %s → %s", self.remote, self.worktree)
            return
        if not self.remote:
            # リモート無し: ローカル git リポジトリとして初期化
            log.info("worktree に git リポジトリを初期化: %s", self.worktree)
            self.worktree.mkdir(parents=True, exist_ok=True)
            run_git(self.worktree, ["init"])
            run_git(self.worktree, ["checkout", "-B", self.branch])
        else:
            log.info("リポジトリを clone: %s → %s", self.remote, self.worktree)
            self.worktree.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                ["git", "clone", "--branch", self.branch, self.remote, str(self.worktree)],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                # 指定ブランチが存在しない場合はデフォルトブランチで clone
                log.warning("ブランチ %s での clone に失敗、既定ブランチで再試行", self.branch)
                proc = subprocess.run(
                    ["git", "clone", self.remote, str(self.worktree)],
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    raise GitError(f"clone failed:\n{proc.stderr.strip()}")
                run_git(self.worktree, ["checkout", "-B", self.branch])
        self._configure_identity()

    def _configure_identity(self) -> None:
        if self.author_name:
            run_git(self.worktree, ["config", "user.name", self.author_name], check=False)
        if self.author_email:
            run_git(self.worktree, ["config", "user.email", self.author_email], check=False)

    # -- pull / push -----------------------------------------------------
    def pull(self, policy: str, dry_run: bool = False) -> None:
        if not self.remote or dry_run:
            if dry_run and self.remote:
                log.info("[dry-run] git pull (%s)", self.branch)
            return
        # コンフリクトポリシーに応じて git のマージ戦略オプションを選ぶ。
        #   mine   → -X ours    (ローカル側のコミットを優先)
        #   theirs → -X theirs  (リモート側のコミットを優先)
        strategy = "ours" if policy == POLICY_MINE else "theirs"
        run_git(self.worktree, ["fetch", "origin", self.branch], check=False)
        proc = run_git(
            self.worktree,
            ["merge", f"-X{strategy}", "--no-edit", f"origin/{self.branch}"],
            check=False,
        )
        if proc.returncode != 0:
            log.warning("merge に問題: %s", proc.stderr.strip() or proc.stdout.strip())

    def commit_and_push(self, message: str, dry_run: bool = False) -> bool:
        """変更があればコミット。push する場合は True を返す。"""
        status = run_git(self.worktree, ["status", "--porcelain"])
        if not status.stdout.strip():
            return False
        if dry_run:
            log.info("[dry-run] commit: %s\n%s", message, status.stdout.strip())
            return False
        run_git(self.worktree, ["add", "-A"])
        run_git(self.worktree, ["commit", "-m", message], check=False)
        if self.remote and self.auto_push:
            for attempt in range(4):
                proc = run_git(self.worktree, ["push", "origin", self.branch], check=False)
                if proc.returncode == 0:
                    return True
                wait = 2 ** (attempt + 1)
                log.warning("push 失敗 (%d/4)、%ds 後に再試行: %s", attempt + 1, wait,
                            proc.stderr.strip())
                time.sleep(wait)
            log.error("push に繰り返し失敗しました")
        return True


# ---------------------------------------------------------------------------
# ファイルスナップショット / ハッシュ
# ---------------------------------------------------------------------------


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_ignored(rel: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    from fnmatch import fnmatch

    parts = rel.split("/")
    for pat in patterns:
        if fnmatch(rel, pat) or any(fnmatch(p, pat) for p in parts):
            return True
        # ディレクトリ接頭辞一致
        if rel == pat or rel.startswith(pat.rstrip("/") + "/"):
            return True
    return False


Meta = tuple[int, int]  # (mtime_ns, size)


def _stat_meta(path: Path) -> Meta | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def scan_tree(
    root: Path,
    ignore: list[str],
    *,
    fast: bool = False,
    prev_hashes: dict[str, str] | None = None,
    prev_meta: dict[str, Meta] | None = None,
) -> tuple[dict[str, str], dict[str, Meta]]:
    """root 以下の全ファイルを走査し ({相対パス: sha256}, {相対パス: meta}) を返す。

    fast=True かつ前回の hash/meta が与えられた場合、mtime+サイズが前回と一致する
    ファイルはハッシュ計算を省略し、前回のハッシュを再利用する。
    """
    hashes: dict[str, str] = {}
    metas: dict[str, Meta] = {}
    prev_hashes = prev_hashes or {}
    prev_meta = prev_meta or {}
    if not root.exists():
        return hashes, metas
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        # .git ディレクトリと .conflict バックアップは除外
        if rel.startswith(".git/") or rel == ".git" or rel.endswith(".conflict"):
            continue
        if _is_ignored(rel, ignore):
            continue
        meta = _stat_meta(path)
        if meta is None:
            continue
        metas[rel] = meta
        if fast and prev_meta.get(rel) == meta and rel in prev_hashes:
            # mtime+サイズが前回同期から不変 → ハッシュ計算をスキップして再利用
            hashes[rel] = prev_hashes[rel]
        else:
            hashes[rel] = file_hash(path)
    return hashes, metas


# ---------------------------------------------------------------------------
# 状態 (前回同期スナップショット) の永続化
# ---------------------------------------------------------------------------


class Snapshot:
    """前回同期スナップショット。

    各相対パスについて、合意済みハッシュ (h) と、ローカル側 / リポジトリ側それぞれの
    mtime+サイズ (l / r) を保持する。fast スキャンの変更検知に meta を使う。
    """

    def __init__(self) -> None:
        self.hashes: dict[str, str] = {}
        self.local_meta: dict[str, Meta] = {}
        self.repo_meta: dict[str, Meta] = {}

    def add(self, rel: str, h: str, lmeta: Meta | None, rmeta: Meta | None) -> None:
        self.hashes[rel] = h
        if lmeta is not None:
            self.local_meta[rel] = lmeta
        if rmeta is not None:
            self.repo_meta[rel] = rmeta


class StateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, pair_name: str) -> Path:
        safe = pair_name.replace("/", "_").replace(" ", "_")
        return self.state_dir / f"{safe}.json"

    def load(self, pair_name: str) -> Snapshot:
        snap = Snapshot()
        p = self._path(pair_name)
        if not p.is_file():
            return snap
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("状態ファイルの読み込みに失敗: %s", p)
            return snap
        for rel, entry in (data.get("files", {}) or {}).items():
            if isinstance(entry, str):
                # 旧フォーマット（ハッシュ文字列のみ）との後方互換
                snap.hashes[rel] = entry
                continue
            h = entry.get("h")
            if not h:
                continue
            snap.hashes[rel] = h
            if entry.get("l"):
                snap.local_meta[rel] = tuple(entry["l"])  # type: ignore[assignment]
            if entry.get("r"):
                snap.repo_meta[rel] = tuple(entry["r"])  # type: ignore[assignment]
        return snap

    def save(self, pair_name: str, snap: Snapshot) -> None:
        p = self._path(pair_name)
        files: dict[str, Any] = {}
        for rel, h in snap.hashes.items():
            entry: dict[str, Any] = {"h": h}
            if rel in snap.local_meta:
                entry["l"] = list(snap.local_meta[rel])
            if rel in snap.repo_meta:
                entry["r"] = list(snap.repo_meta[rel])
            files[rel] = entry
        payload = {
            "pair": pair_name,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 同期エンジン (3-way 差分)
# ---------------------------------------------------------------------------


def _copy_file(src: Path, dst: Path, dry_run: bool) -> None:
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _remove_file(path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    if path.is_file():
        path.unlink()
        # 空になった親ディレクトリを掃除
        parent = path.parent
        try:
            while parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except OSError:
            pass


def _backup_conflict(path: Path, dry_run: bool) -> None:
    if dry_run or not path.is_file():
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.{ts}.conflict")
    try:
        shutil.copy2(path, backup)
    except OSError as e:
        log.warning("コンフリクトバックアップ作成失敗 %s: %s", path, e)


class SyncStats:
    def __init__(self) -> None:
        self.l2r = 0  # local → repo
        self.r2l = 0  # repo → local
        self.deleted = 0
        self.conflicts = 0
        self.skipped_old = 0  # pull: 更新日時が古く取得しなかった件数

    def summary(self) -> str:
        s = (
            f"local→repo={self.l2r}, repo→local={self.r2l}, "
            f"deleted={self.deleted}, conflicts={self.conflicts}"
        )
        if self.skipped_old:
            s += f", skipped_old={self.skipped_old}"
        return s

    @property
    def changed(self) -> bool:
        return any((self.l2r, self.r2l, self.deleted, self.conflicts))


def sync_pair(
    pair: SyncPair,
    repo_root: Path,
    state: StateStore,
    dry_run: bool = False,
) -> SyncStats:
    """1 ペアの双方向同期を行い、新しいスナップショットを保存する。"""
    local_dir = pair.local_path
    repo_dir = pair.repo_dir(repo_root)
    local_dir.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        repo_dir.mkdir(parents=True, exist_ok=True)

    snap = state.load(pair.name)
    base = snap.hashes
    fast = pair.scan_mode == SCAN_FAST
    local, _local_meta = scan_tree(
        local_dir, pair.ignore, fast=fast, prev_hashes=base, prev_meta=snap.local_meta
    )
    repo, _repo_meta = scan_tree(
        repo_dir, pair.ignore, fast=fast, prev_hashes=base, prev_meta=snap.repo_meta
    )

    new_base: dict[str, str] = {}
    stats = SyncStats()

    # pull モードの更新日時フィルタ用に、git 側の各ファイルの最終コミット時刻を取得
    commit_times: dict[str, int] = {}
    age_cutoff: float | None = None
    if (
        pair.direction == DIR_PULL
        and pair.max_age_seconds is not None
        and (repo_root / ".git").exists()
    ):
        commit_times = git_commit_times(repo_root, pair.repo_subpath)
        age_cutoff = time.time() - pair.max_age_seconds

    all_paths = set(base) | set(local) | set(repo)
    for rel in sorted(all_paths):
        bh = base.get(rel)
        lh = local.get(rel)
        rh = repo.get(rel)
        lpath = local_dir / rel
        rpath = repo_dir / rel

        if pair.direction == DIR_PULL:
            # git → ローカルのみ。git を正とし、リポジトリには一切書き込まない。
            # ローカルの削除・編集は尊重し、git 側が更新された時だけ反映する。
            if rh != bh:  # git 側で新規/更新/削除
                if rh is None:
                    if lh is not None:
                        log.info("  [%s] git削除→local: %s", pair.name, rel)
                        _remove_file(lpath, dry_run)
                        stats.deleted += 1
                else:
                    if lh != rh:  # ローカルに無い/古い → ダウンロード候補
                        ct = commit_times.get(rel)
                        if (
                            age_cutoff is not None
                            and ct is not None
                            and ct < age_cutoff
                        ):
                            # git 側の更新が古い → 取得しない（baseline だけ更新して
                            # 以後は再評価しない。再び git で更新されれば取得対象に戻る）
                            log.debug("  [%s] 古いため取得スキップ: %s", pair.name, rel)
                            stats.skipped_old += 1
                        else:
                            log.info("  [%s] repo→local: %s", pair.name, rel)
                            _copy_file(rpath, lpath, dry_run)
                            stats.r2l += 1
                    new_base[rel] = rh
            else:
                # git 側不変 → ローカルの削除/編集はそのまま尊重（git は触らない）
                if rh is not None:
                    new_base[rel] = rh
            continue

        if pair.direction == DIR_PUSH:
            # ローカル → git のみ。ローカルを正とし、ローカルには書き込まない。
            if lh != bh:  # ローカル側で新規/更新/削除
                if lh is None:
                    if rh is not None:
                        log.info("  [%s] local削除→repo: %s", pair.name, rel)
                        _remove_file(rpath, dry_run)
                        stats.deleted += 1
                else:
                    if rh != lh:
                        log.info("  [%s] local→repo: %s", pair.name, rel)
                        _copy_file(lpath, rpath, dry_run)
                        stats.l2r += 1
                    new_base[rel] = lh
            else:
                if lh is not None:
                    new_base[rel] = lh
            continue

        if lh == rh:
            # 既に一致（両側同一 or 両側削除）
            if lh is not None:
                new_base[rel] = lh
            continue

        local_changed = lh != bh
        repo_changed = rh != bh

        if local_changed and not repo_changed:
            # ローカルのみ変更 → リポジトリへ反映
            if lh is None:
                log.info("  [%s] 削除→repo: %s", pair.name, rel)
                _remove_file(rpath, dry_run)
                stats.deleted += 1
            else:
                log.info("  [%s] local→repo: %s", pair.name, rel)
                _copy_file(lpath, rpath, dry_run)
                stats.l2r += 1
                new_base[rel] = lh
        elif repo_changed and not local_changed:
            # リポジトリのみ変更 → ローカルへ反映
            if rh is None:
                log.info("  [%s] 削除→local: %s", pair.name, rel)
                _remove_file(lpath, dry_run)
                stats.deleted += 1
            else:
                log.info("  [%s] repo→local: %s", pair.name, rel)
                _copy_file(rpath, lpath, dry_run)
                stats.r2l += 1
                new_base[rel] = rh
        else:
            # 両側変更 = コンフリクト → ポリシーで採用側を決定
            stats.conflicts += 1
            if pair.policy == POLICY_MINE:
                log.warning("  [%s] CONFLICT %s → mine(local) を採用", pair.name, rel)
                if pair.keep_conflict_backup and rh is not None:
                    _backup_conflict(rpath, dry_run)
                if lh is None:
                    _remove_file(rpath, dry_run)
                else:
                    _copy_file(lpath, rpath, dry_run)
                    new_base[rel] = lh
            else:
                log.warning("  [%s] CONFLICT %s → theirs(repo) を採用", pair.name, rel)
                if pair.keep_conflict_backup and lh is not None:
                    _backup_conflict(lpath, dry_run)
                if rh is None:
                    _remove_file(lpath, dry_run)
                else:
                    _copy_file(rpath, lpath, dry_run)
                    new_base[rel] = rh

    if not dry_run:
        # 反映後の最新 mtime+サイズを記録（次回の fast スキャンの基準にする）
        new_snap = Snapshot()
        for rel, h in new_base.items():
            new_snap.add(
                rel,
                h,
                _stat_meta(local_dir / rel),
                _stat_meta(repo_dir / rel),
            )
        state.save(pair.name, new_snap)
    pair.last_sync = datetime.now()
    pair.last_status = stats.summary() if stats.changed else "変更なし"
    return stats


# ---------------------------------------------------------------------------
# オーケストレーション
# ---------------------------------------------------------------------------


class Syncer:
    def __init__(self, config: dict[str, Any], config_path: Path | None, dry_run: bool):
        self.config = config
        self.config_path = config_path
        self.dry_run = dry_run

        defaults = config.get("defaults", {}) or config.get("global", {}) or {}
        self.repo = GitRepo(config["repository"])
        self.pairs: list[SyncPair] = [
            SyncPair(raw, defaults) for raw in config.get("pairs", [])
        ]
        if not self.pairs:
            log.warning("同期ペアが 1 つも定義されていません")

        state_dir = Path(
            config.get("state_dir", Path.home() / ".git-file-sync" / "state")
        ).expanduser()
        self.state = StateStore(state_dir)

    def find_pair(self, name: str) -> SyncPair | None:
        for p in self.pairs:
            if p.name == name:
                return p
        return None

    def sync_all(self, only: str | None = None) -> None:
        """pull → 各ペア同期 → commit/push を 1 サイクル実行。"""
        targets = self.pairs
        if only:
            p = self.find_pair(only)
            if not p:
                log.error("ペアが見つかりません: %s", only)
                return
            targets = [p]

        try:
            self.repo.ensure_clone(self.dry_run)
        except GitError as e:
            log.error("リポジトリ準備に失敗: %s", e)
            return

        # pull は全体に効くので、対象ペアの中で最も「他人優先」なら theirs を使う。
        # （mine ペアのみなら ours、それ以外は theirs）
        pull_policy = (
            POLICY_MINE if all(p.policy == POLICY_MINE for p in targets) else POLICY_THEIRS
        )
        try:
            self.repo.pull(pull_policy, self.dry_run)
        except GitError as e:
            log.error("pull に失敗: %s", e)

        total = SyncStats()
        changed_pairs: list[str] = []
        for pair in targets:
            log.info("同期: %s  (%s ⇔ %s)  policy=%s",
                     pair.name, pair.local_path, pair.repo_subpath or "(root)", pair.policy)
            try:
                stats = sync_pair(pair, self.repo.worktree, self.state, self.dry_run)
            except Exception as e:  # noqa: BLE001 個々のペアの失敗で全体を止めない
                log.error("ペア %s の同期に失敗: %s", pair.name, e)
                pair.last_status = f"エラー: {e}"
                continue
            log.info("  → %s", pair.last_status)
            if stats.changed:
                changed_pairs.append(pair.name)
            total.l2r += stats.l2r
            total.r2l += stats.r2l
            total.deleted += stats.deleted
            total.conflicts += stats.conflicts

        # リポジトリ側に反映された変更をコミット & push
        if changed_pairs:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = f"{self.repo.commit_prefix}: sync {', '.join(changed_pairs)} ({ts})"
            try:
                pushed = self.repo.commit_and_push(msg, self.dry_run)
                if pushed:
                    log.info("リポジトリへコミット/プッシュしました")
            except GitError as e:
                log.error("commit/push に失敗: %s", e)
        log.info("サイクル完了: %s", total.summary())

    def run_forever(self) -> None:
        log.info("常駐モード開始 (Ctrl-C で終了)。ペア数=%d", len(self.pairs))
        # 起動直後に 1 回全同期
        self.sync_all()
        now = time.monotonic()
        for p in self.pairs:
            p.schedule_next(now)
        try:
            while True:
                time.sleep(1.0)
                now = time.monotonic()
                due = [p for p in self.pairs if p.is_due(now)]
                if due:
                    # 1 サイクルにまとめて pull/push したいので、due があれば全体回す
                    self.sync_all()
                    now = time.monotonic()
                    for p in self.pairs:
                        p.schedule_next(now)
        except KeyboardInterrupt:
            log.info("終了します")


# ---------------------------------------------------------------------------
# インタラクティブ CLI
# ---------------------------------------------------------------------------


def interactive_loop(syncer: Syncer) -> None:
    bg = threading.Thread(target=syncer.run_forever, daemon=True)
    bg.start()
    print("git-file-sync インタラクティブモード。'help' でコマンド一覧。")
    while True:
        try:
            line = input("git-file-sync> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError as e:
            print(f"パースエラー: {e}")
            continue
        cmd, *args = parts

        if cmd in ("quit", "exit"):
            break
        elif cmd == "help":
            print(__doc__.split("インタラクティブコマンド")[1] if __doc__ else "")
        elif cmd == "sync":
            syncer.sync_all(args[0] if args else None)
        elif cmd == "list":
            for p in syncer.pairs:
                arrow = {DIR_BIDIR: "⇔", DIR_PULL: "←", DIR_PUSH: "→"}[p.direction]
                age = f" max_age={p.max_age_raw}" if p.max_age_seconds is not None else ""
                print(f"  {p.name}: {p.local_path} {arrow} {p.repo_subpath or '(root)'}  "
                      f"dir={p.direction} policy={p.policy} scan={p.scan_mode} "
                      f"interval={p.poll_interval_minutes}m{age}")
        elif cmd == "status":
            for p in syncer.pairs:
                last = p.last_sync.strftime("%Y-%m-%d %H:%M:%S") if p.last_sync else "—"
                print(f"  {p.name}: last={last}  status={p.last_status}")
        elif cmd == "interval" and len(args) == 2:
            p = syncer.find_pair(args[0])
            if p:
                p.poll_interval_minutes = float(args[1])
                p.schedule_next(time.monotonic())
                print(f"  {p.name} の間隔を {args[1]} 分に変更")
            else:
                print("ペアが見つかりません")
        elif cmd == "policy" and len(args) == 2:
            p = syncer.find_pair(args[0])
            if p:
                p.policy = normalize_policy(args[1], p.policy)
                print(f"  {p.name} のポリシーを {p.policy} に変更")
            else:
                print("ペアが見つかりません")
        elif cmd == "scan" and len(args) == 2:
            p = syncer.find_pair(args[0])
            if p:
                p.scan_mode = normalize_scan_mode(args[1], p.scan_mode)
                print(f"  {p.name} のスキャンモードを {p.scan_mode} に変更")
            else:
                print("ペアが見つかりません")
        elif cmd == "direction" and len(args) == 2:
            p = syncer.find_pair(args[0])
            if p:
                p.direction = normalize_direction(args[1], p.direction)
                print(f"  {p.name} の同期方向を {p.direction} に変更")
            else:
                print("ペアが見つかりません")
        elif cmd == "maxage" and len(args) == 2:
            p = syncer.find_pair(args[0])
            if p:
                raw = None if args[1].lower() in ("none", "off", "-") else args[1]
                p.max_age_raw = raw
                secs = parse_duration_seconds(raw)
                if secs is not None and p.direction != DIR_PULL:
                    print(f"  max_age は pull モードでのみ有効です（{p.name} は {p.direction}）")
                p.max_age_seconds = secs if p.direction == DIR_PULL else None
                print(f"  {p.name} の max_age を {raw or '無効'} に変更")
            else:
                print("ペアが見つかりません")
        else:
            print(f"不明なコマンド: {cmd}  ('help' を参照)")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="git リポジトリを使ったファイル同期ツール")
    parser.add_argument("--config", help="設定ファイルのパス")
    parser.add_argument("--once", action="store_true", help="全ペアを 1 回同期して終了")
    parser.add_argument("--sync", metavar="PAIR", help="指定ペアのみ 1 回同期して終了")
    parser.add_argument("--dry-run", action="store_true", help="変更を加えず予定だけ表示")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="高速スキャンモード（mtime+サイズが前回と同じファイルはハッシュ計算を省略）を全ペアに適用",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="デバッグログを表示")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    config_path = Path(args.config).expanduser() if args.config else find_default_config(Path.cwd())
    if not config_path or not config_path.is_file():
        log.error(
            "設定ファイルが見つかりません。--config で指定するか、"
            "%s のいずれかを配置してください。",
            " / ".join(DEFAULT_CONFIG_NAMES),
        )
        sys.exit(1)

    config = load_config(config_path)
    syncer = Syncer(config, config_path, dry_run=args.dry_run)

    if args.fast:
        # --fast は全ペアのスキャンモードを fast に上書きする
        for p in syncer.pairs:
            p.scan_mode = SCAN_FAST
        log.info("全ペアを高速スキャンモード (fast) で実行します")

    if args.sync:
        syncer.sync_all(args.sync)
    elif args.once:
        syncer.sync_all()
    else:
        interactive_loop(syncer)


if __name__ == "__main__":
    main()
