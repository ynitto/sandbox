#!/usr/bin/env python3
"""
git-file-sync — git リポジトリを同期ハブとして使うファイル同期ツール。

概要:
  ローカルフォルダと「git リポジトリ上のフォルダ」を対 (ペア) にして登録し、
  定期的に双方向同期する。git のクローンを介して pull / push することで、
  複数マシン間でフォルダ内容を同期できる（簡易 Dropbox のような使い方）。

特徴:
  - 複数のペア (ローカル ⇔ リポジトリ内サブフォルダ) を登録・管理
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
  --config       設定ファイルのパス（省略時は既定の探索順）

インタラクティブコマンド (--once / --sync なしで起動した場合):
  sync [<pair>]        全ペアまたは指定ペアを今すぐ同期
  list                 登録済みペアを表示
  status               最終同期時刻とステータスを表示
  interval <pair> <m>  ポーリング間隔 (分) を変更
  policy <pair> <p>    コンフリクトポリシーを変更 (mine / theirs)
  help                 コマンド一覧
  quit                 終了
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
        self.poll_interval_minutes: float = float(
            raw.get("poll_interval_minutes", defaults.get("poll_interval_minutes", 5))
        )
        self.keep_conflict_backup: bool = bool(
            raw.get("keep_conflict_backup", defaults.get("keep_conflict_backup", True))
        )
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


def scan_tree(root: Path, ignore: list[str]) -> dict[str, str]:
    """root 以下の全ファイルを {相対パス: sha256} で返す。"""
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        # .git ディレクトリと .conflict バックアップは除外
        if rel.startswith(".git/") or rel == ".git" or rel.endswith(".conflict"):
            continue
        if _is_ignored(rel, ignore):
            continue
        result[rel] = file_hash(path)
    return result


# ---------------------------------------------------------------------------
# 状態 (前回同期スナップショット) の永続化
# ---------------------------------------------------------------------------


class StateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, pair_name: str) -> Path:
        safe = pair_name.replace("/", "_").replace(" ", "_")
        return self.state_dir / f"{safe}.json"

    def load(self, pair_name: str) -> dict[str, str]:
        p = self._path(pair_name)
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("files", {})
        except (json.JSONDecodeError, OSError):
            log.warning("状態ファイルの読み込みに失敗: %s", p)
            return {}

    def save(self, pair_name: str, files: dict[str, str]) -> None:
        p = self._path(pair_name)
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

    def summary(self) -> str:
        return (
            f"local→repo={self.l2r}, repo→local={self.r2l}, "
            f"deleted={self.deleted}, conflicts={self.conflicts}"
        )

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

    base = state.load(pair.name)
    local = scan_tree(local_dir, pair.ignore)
    repo = scan_tree(repo_dir, pair.ignore)

    new_base: dict[str, str] = {}
    stats = SyncStats()

    all_paths = set(base) | set(local) | set(repo)
    for rel in sorted(all_paths):
        bh = base.get(rel)
        lh = local.get(rel)
        rh = repo.get(rel)
        lpath = local_dir / rel
        rpath = repo_dir / rel

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
        state.save(pair.name, new_base)
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
                print(f"  {p.name}: {p.local_path} ⇔ {p.repo_subpath or '(root)'}  "
                      f"policy={p.policy} interval={p.poll_interval_minutes}m")
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

    if args.sync:
        syncer.sync_all(args.sync)
    elif args.once:
        syncer.sync_all()
    else:
        interactive_loop(syncer)


if __name__ == "__main__":
    main()
