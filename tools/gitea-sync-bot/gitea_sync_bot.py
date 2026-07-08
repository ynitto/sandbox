#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gitea-sync-bot — Gitea ⇄ GitLab のコードを安全に双方向同期する調停ロボット。

設計正典: docs/designs/gitea-gitlab-sync-design.md（§3 が本実装の対象）。

やること（1 リポジトリ・1 ref あたり）:
  - 同期対象は allowlist（include/exclude の glob）に一致する ref のみ（§3.6）。
    allowlist 外（feature/* 等の Gitea 発ブランチ）は一切 GitLab へ push しない。
  - 両側の HEAD を比べて **fast-forward できるときだけ自動同期**する（§3.2）:
      両側同一          → 何もしない
      Gitea だけ進行    → GitLab へ ff push
      GitLab だけ進行   → Gitea へ ff push
      分岐（diverged）  → 自動同期せず、Gitea に統合ブランチを作り MR を起票して人手へ（§3.4）
  - **絶対に --force しない**。分岐は必ず人手（MR）で解決する。

GitLab 負荷を抑える工夫（§3.7）:
  - webhook 主導。無変化のときは GitLab に接続しない。
  - GitLab の HEAD 確認は軽量な `git ls-remote <ref>` のみ（履歴を転送しない）。
    キャッシュした SHA と一致すれば object の fetch をしない。
  - fetch は対象 ref だけに限定（全 ref 総なめ禁止）。
  - 連続イベントは debounce_seconds でまとめる。429/5xx は指数バックオフ。

依存:
  - git コマンド（PATH 上に存在すること）
  - PyYAML（YAML 設定を使う場合のみ。JSON 設定なら不要）
  - 標準ライブラリのみ（http.server / urllib / hmac 等）

使い方:
  python3 gitea_sync_bot.py --config config.yaml --once          # allowlist 全 ref を 1 回同期して終了
  python3 gitea_sync_bot.py --config config.yaml --repo NAME --ref refs/heads/main --once
  python3 gitea_sync_bot.py --config config.yaml --serve         # webhook 待受 + cron バックストップ
  python3 gitea_sync_bot.py --config config.yaml --once --dry-run # 実際の push をせず予定だけ表示
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------- #
# 判定コア（純粋関数 — git を呼ばずに単体テストできる）
# --------------------------------------------------------------------------- #

# アクション種別
NOOP = "noop"                       # 両側同一
CREATE_ON_GITEA = "create_gitea"    # GitLab のみに存在 → Gitea へ作成（GitLab 発を取り込む）
CREATE_ON_GITLAB = "create_gitlab"  # Gitea のみに存在（allowlist 内）→ GitLab へ作成
PUSH_FF_TO_GITLAB = "ff_gitlab"     # Gitea だけ進行 → GitLab へ ff
PUSH_FF_TO_GITEA = "ff_gitea"       # GitLab だけ進行 → Gitea へ ff
DIVERGED = "diverged"               # 双方進行 → 人手（MR）


def ref_in_scope(ref: str, include: list[str], exclude: list[str]) -> bool:
    """ref が同期対象か（§3.6）。exclude が include より優先。

    include/exclude は fnmatch の glob（例: 'refs/heads/release/*'）。
    include が空なら対象なし（安全側）。
    """
    if any(fnmatch.fnmatch(ref, pat) for pat in exclude):
        return False
    return any(fnmatch.fnmatch(ref, pat) for pat in include)


def decide_action(gitea_sha, gitlab_sha, merge_base):
    """両側の SHA と merge-base から取るべきアクションを決める（純粋関数・§3.2）。

    引数:
      gitea_sha / gitlab_sha : 40 桁 SHA または None（その側に ref が無い）
      merge_base             : 両者の共通祖先 SHA（両方存在するときのみ意味を持つ）

    本関数に渡る ref は必ず allowlist 内であること（呼び出し側で ref_in_scope 済み）。
    したがって「Gitea のみに存在」でも allowlist 内なので GitLab へ作成してよい
    （Gitea 発の feature/* は allowlist に載らないためここには来ない）。
    """
    if gitea_sha == gitlab_sha:
        return NOOP
    if gitea_sha is None:
        # GitLab のみに存在 → Gitea へ取り込む（双方向のうち GitLab→Gitea 方向）
        return CREATE_ON_GITEA
    if gitlab_sha is None:
        # Gitea のみに存在（allowlist 内の共有ブランチ）→ GitLab へ作成
        return CREATE_ON_GITLAB
    if merge_base == gitlab_sha:
        # GitLab は Gitea の祖先 = Gitea だけ進行
        return PUSH_FF_TO_GITLAB
    if merge_base == gitea_sha:
        # Gitea は GitLab の祖先 = GitLab だけ進行
        return PUSH_FF_TO_GITEA
    return DIVERGED


# --------------------------------------------------------------------------- #
# 設定
# --------------------------------------------------------------------------- #

DEFAULT_INCLUDE = ["refs/heads/main", "refs/heads/master",
                   "refs/heads/release/*", "refs/heads/hotfix/*", "refs/tags/*"]
DEFAULT_EXCLUDE = ["refs/heads/feature/*", "refs/heads/fix/*", "refs/heads/sync/*"]


@dataclass
class RepoConfig:
    name: str
    workdir: str
    gitea_url: str
    gitlab_url: str
    gitea_token: str = ""   # 統合 MR 起票 / 認証付き push 用（環境変数展開可）
    gitlab_token: str = ""


@dataclass
class Config:
    repos: list[RepoConfig] = field(default_factory=list)
    include: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    poll_interval_minutes: int = 20         # cron バックストップ（長間隔・§3.7）
    debounce_seconds: int = 5
    state_dir: str = ""
    git_timeout: int = 120
    integration_branch_prefix: str = "sync/integrate"
    create_gitea_pr: bool = False           # 分岐時に Gitea API で MR を自動起票するか
    gitea_api_base: str = ""                 # 例: http://gitea.local:3000/api/v1
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 9000
    webhook_secret: str = ""

    def repo(self, name):
        for r in self.repos:
            if r.name == name:
                return r
        return None


def _expand(value):
    """設定値中の $VAR / ${VAR} を環境変数で展開する（トークンを config に直書きしないため）。"""
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value


def load_config(path):
    """YAML（PyYAML があれば）または JSON の設定を読み込む。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    data = None
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(text)
        except ImportError:
            raise SystemExit("PyYAML が必要です（pip install pyyaml）。または JSON 設定を使ってください。")
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit("設定のトップレベルはマップである必要があります。")

    cfg = Config()
    sync = data.get("sync", {}) or {}
    cfg.include = list(sync.get("include", DEFAULT_INCLUDE))
    cfg.exclude = list(sync.get("exclude", DEFAULT_EXCLUDE))
    cfg.poll_interval_minutes = int(data.get("poll_interval_minutes", cfg.poll_interval_minutes))
    cfg.debounce_seconds = int(data.get("debounce_seconds", cfg.debounce_seconds))
    cfg.state_dir = _expand(data.get("state_dir", "")) or os.path.join(
        os.path.expanduser("~"), ".gitea-sync-bot", "state")
    cfg.git_timeout = int(data.get("git_timeout", cfg.git_timeout))

    integ = data.get("integration", {}) or {}
    cfg.integration_branch_prefix = integ.get("branch_prefix", cfg.integration_branch_prefix)
    cfg.create_gitea_pr = bool(integ.get("create_gitea_pr", False))
    cfg.gitea_api_base = _expand(integ.get("gitea_api_base", ""))

    wh = data.get("webhook", {}) or {}
    cfg.webhook_host = wh.get("host", cfg.webhook_host)
    cfg.webhook_port = int(wh.get("port", cfg.webhook_port))
    cfg.webhook_secret = _expand(wh.get("secret", ""))

    for r in data.get("repos", []) or []:
        gitea = r.get("gitea", {}) or {}
        gitlab = r.get("gitlab", {}) or {}
        cfg.repos.append(RepoConfig(
            name=r["name"],
            workdir=_expand(r["workdir"]),
            gitea_url=_expand(gitea.get("url", "")),
            gitlab_url=_expand(gitlab.get("url", "")),
            gitea_token=_expand(gitea.get("token", "")),
            gitlab_token=_expand(gitlab.get("token", "")),
        ))
    if not cfg.repos:
        raise SystemExit("設定に repos が 1 つも定義されていません。")
    return cfg


# --------------------------------------------------------------------------- #
# git ラッパ
# --------------------------------------------------------------------------- #

class GitError(RuntimeError):
    pass


def run_git(args, cwd=None, timeout=120, check=True):
    """git を単発・有界で実行する。stdout を返す。"""
    proc = subprocess.run(
        ["git"] + args, cwd=cwd, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


class LocalMirror:
    """ボット専用のローカル git ディレクトリ。gitea/gitlab の 2 remote を持つ。

    fetch した内容は refs/gitea/* と refs/gitlab/* に格納し、
    push は `refs/gitea/heads/main:refs/heads/main` のように明示 refspec で行う。
    """

    def __init__(self, repo: RepoConfig, timeout=120):
        self.repo = repo
        self.timeout = timeout
        self.dir = repo.workdir

    def _git(self, args, check=True):
        return run_git(args, cwd=self.dir, timeout=self.timeout, check=check)

    def ensure(self):
        """ローカルディレクトリと 2 つの remote を用意する（冪等）。"""
        if not os.path.isdir(os.path.join(self.dir, ".git")):
            os.makedirs(self.dir, exist_ok=True)
            run_git(["init", "-q", self.dir], timeout=self.timeout)
        self._set_remote("gitea", self.repo.gitea_url,
                         ["+refs/heads/*:refs/gitea/heads/*", "+refs/tags/*:refs/gitea/tags/*"])
        self._set_remote("gitlab", self.repo.gitlab_url,
                         ["+refs/heads/*:refs/gitlab/heads/*", "+refs/tags/*:refs/gitlab/tags/*"])

    def _set_remote(self, name, url, refspecs):
        existing = self._git(["remote"], check=False).split()
        if name in existing:
            self._git(["remote", "set-url", name, url])
        else:
            self._git(["remote", "add", name, url])
        # fetch refspec を設定（既存を置き換え）
        self._git(["config", "--unset-all", f"remote.{name}.fetch"], check=False)
        for rs in refspecs:
            self._git(["config", "--add", f"remote.{name}.fetch", rs])

    def ls_remote(self, remote, ref):
        """リモートの ref の SHA を軽量に取得（履歴を転送しない・§3.7）。無ければ None。"""
        out = self._git(["ls-remote", remote, ref], check=False).strip()
        if not out:
            return None
        return out.split()[0]

    def fetch_ref(self, remote, ref):
        """対象 ref だけを fetch（refspec 限定・全 ref 総なめ禁止・§3.7）。"""
        # refs/heads/main → refs/<remote>/heads/main へ
        local = ref.replace("refs/heads/", f"refs/{remote}/heads/", 1)
        local = local.replace("refs/tags/", f"refs/{remote}/tags/", 1)
        self._git(["fetch", "--no-tags", "-q", remote, f"+{ref}:{local}"])

    def local_ref(self, remote, ref):
        return (ref.replace("refs/heads/", f"refs/{remote}/heads/", 1)
                   .replace("refs/tags/", f"refs/{remote}/tags/", 1))

    def rev(self, local_ref):
        out = self._git(["rev-parse", "--verify", "-q", local_ref], check=False).strip()
        return out or None

    def merge_base(self, a, b):
        out = self._git(["merge-base", a, b], check=False).strip()
        return out or None

    def push(self, remote, src_local_ref, dst_ref, dry_run=False):
        """ff push（--force なし）。dst が非 ff なら git 側が拒否して失敗する = 安全。"""
        args = ["push", remote, f"{src_local_ref}:{dst_ref}"]
        if dry_run:
            args.insert(1, "--dry-run")
        self._git(args)


# --------------------------------------------------------------------------- #
# 状態（GitLab HEAD キャッシュ）
# --------------------------------------------------------------------------- #

class State:
    """GitLab の HEAD SHA をキャッシュして、無変化時に fetch を省く（§3.7）。"""

    def __init__(self, state_dir):
        self.dir = state_dir
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "gitlab_heads.json")
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, ValueError):
            self._data = {}

    def get(self, repo, ref):
        return self._data.get(f"{repo}\t{ref}")

    def set(self, repo, ref, sha):
        self._data[f"{repo}\t{ref}"] = sha
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)


# --------------------------------------------------------------------------- #
# 調停本体
# --------------------------------------------------------------------------- #

def log(msg):
    print(f"[gitea-sync-bot] {msg}", flush=True)


def short(sha):
    return sha[:10] if sha else "-"


def reconcile_ref(mirror: LocalMirror, cfg: Config, state: State, ref: str,
                  known_gitlab_sha=None, dry_run=False):
    """1 つの ref を調停する（§3.2–3.4）。取ったアクション名を返す。"""
    repo = mirror.repo.name

    # --- GitLab の HEAD を軽量取得（ls-remote は履歴を転送しない・§3.7）---
    if known_gitlab_sha is not None:
        gitlab_head = known_gitlab_sha            # webhook の after を信用（接続すら省く）
    else:
        gitlab_head = mirror.ls_remote("gitlab", ref)

    cached = state.get(repo, ref)
    gitlab_local = mirror.rev(mirror.local_ref("gitlab", ref))

    # GitLab に object を取りに行くのは「GitLab が進んだ」ときだけ（§3.7 早期リターン）
    if gitlab_head is not None and gitlab_head != gitlab_local:
        mirror.fetch_ref("gitlab", ref)
        gitlab_local = mirror.rev(mirror.local_ref("gitlab", ref))
    state.set(repo, ref, gitlab_head or "")

    # --- Gitea は LAN 内なので対象 ref を取得（安価）---
    gitea_head = mirror.ls_remote("gitea", ref)
    gitea_local = mirror.rev(mirror.local_ref("gitea", ref))
    if gitea_head is not None and gitea_head != gitea_local:
        mirror.fetch_ref("gitea", ref)
        gitea_local = mirror.rev(mirror.local_ref("gitea", ref))

    g, l = gitea_local, gitlab_local
    base = mirror.merge_base(g, l) if (g and l) else None
    action = decide_action(g, l, base)

    if action == NOOP:
        return NOOP

    log(f"{repo} {ref}: gitea={short(g)} gitlab={short(l)} → {action}")

    if action == PUSH_FF_TO_GITLAB or action == CREATE_ON_GITLAB:
        mirror.push("gitlab", mirror.local_ref("gitea", ref), ref, dry_run=dry_run)
    elif action == PUSH_FF_TO_GITEA or action == CREATE_ON_GITEA:
        mirror.push("gitea", mirror.local_ref("gitlab", ref), ref, dry_run=dry_run)
        # Gitea を更新したら GitLab キャッシュはそのまま（GitLab は変わっていない）
    elif action == DIVERGED:
        handle_diverged(mirror, cfg, ref, g, l, dry_run=dry_run)
    return action


def handle_diverged(mirror: LocalMirror, cfg: Config, ref: str, gitea_sha, gitlab_sha, dry_run=False):
    """分岐時: GitLab 側コミットを Gitea に統合ブランチとして取り込み、MR を起票する（§3.4）。

    どちらのコミットも消さない。GitLab へは push しない（人手のマージ結果を待つ）。
    """
    branch_name = ref.replace("refs/heads/", "")
    integ = f"{cfg.integration_branch_prefix}-gitlab-{branch_name}-{short(gitlab_sha)}"
    integ_ref = f"refs/heads/{integ}"
    log(f"  DIVERGED: 統合ブランチ {integ} を Gitea に作成（gitlab {short(gitlab_sha)} を取り込み）")
    # GitLab のコミットを Gitea に統合用ブランチとして push（sync/* は allowlist 外なので GitLab へは伝播しない）
    mirror.push("gitea", mirror.local_ref("gitlab", ref), integ_ref, dry_run=dry_run)
    if cfg.create_gitea_pr and not dry_run:
        try:
            create_gitea_pull_request(mirror.repo, cfg, head=integ, base=branch_name,
                                      title=f"[sync] {branch_name} の分岐を統合",
                                      body=("GitLab 側の分岐コミット " + gitlab_sha +
                                            " を取り込みました。競合を解決してマージしてください。"
                                            "\n\n(gitea-sync-bot が自動起票)"))
            log(f"  Gitea に統合 MR を起票しました（{integ} → {branch_name}）")
        except Exception as e:  # noqa: BLE001  API 失敗で同期全体を止めない
            log(f"  警告: Gitea MR 起票に失敗（手動で作成してください）: {e}")


def create_gitea_pull_request(repo: RepoConfig, cfg: Config, head, base, title, body):
    """Gitea API で PR を起票する（owner/name は gitea_url から推定）。"""
    if not cfg.gitea_api_base or not repo.gitea_token:
        raise RuntimeError("gitea_api_base と gitea トークンが必要です")
    owner, name = _owner_repo_from_url(repo.gitea_url)
    url = f"{cfg.gitea_api_base.rstrip('/')}/repos/{owner}/{name}/pulls"
    payload = json.dumps({"head": head, "base": base, "title": title, "body": body}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"token {repo.gitea_token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _owner_repo_from_url(url):
    """http://host/owner/name.git → (owner, name)。"""
    tail = url.rstrip("/").split("/")
    name = tail[-1]
    if name.endswith(".git"):
        name = name[:-4]
    owner = tail[-2] if len(tail) >= 2 else ""
    return owner, name


def resolve_refs(mirror: LocalMirror, cfg: Config):
    """allowlist に一致する現存 ref（Gitea/GitLab 両側の和集合）を列挙する。"""
    refs = set()
    for remote in ("gitea", "gitlab"):
        out = mirror._git(["ls-remote", remote], check=False)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2:
                refs.add(parts[1])
    return sorted(r for r in refs if ref_in_scope(r, cfg.include, cfg.exclude))


def sync_repo(cfg: Config, repo: RepoConfig, state: State, only_ref=None, dry_run=False):
    mirror = LocalMirror(repo, timeout=cfg.git_timeout)
    mirror.ensure()
    if only_ref:
        targets = [only_ref] if ref_in_scope(only_ref, cfg.include, cfg.exclude) else []
        if not targets:
            log(f"{repo.name} {only_ref}: allowlist 外のためスキップ（§3.6）")
    else:
        targets = resolve_refs(mirror, cfg)
    for ref in targets:
        try:
            reconcile_ref(mirror, cfg, state, ref, dry_run=dry_run)
        except GitError as e:
            log(f"警告: {repo.name} {ref} の同期に失敗: {e}")


def sync_all(cfg: Config, state: State, only_repo=None, only_ref=None, dry_run=False):
    for repo in cfg.repos:
        if only_repo and repo.name != only_repo:
            continue
        with_backoff(lambda r=repo: sync_repo(cfg, r, state, only_ref=only_ref, dry_run=dry_run))


def with_backoff(fn, attempts=4, base_delay=2.0):
    """429/5xx やネットワーク断に対する指数バックオフ（§3.7）。"""
    for i in range(attempts):
        try:
            return fn()
        except (GitError, urllib.error.URLError) as e:
            if i == attempts - 1:
                log(f"リトライ上限に達しました: {e}")
                return None
            delay = base_delay * (2 ** i)
            log(f"再試行 {i + 1}/{attempts - 1}（{delay:.0f}s 待機）: {e}")
            time.sleep(delay)


# --------------------------------------------------------------------------- #
# webhook + debounce + cron バックストップ（--serve）
# --------------------------------------------------------------------------- #

class Debouncer:
    """(repo, ref) 単位でイベントをまとめ、debounce 秒後に 1 回だけ実行する（§3.7）。"""

    def __init__(self, delay, worker):
        self.delay = delay
        self.worker = worker
        self._timers = {}
        self._lock = threading.Lock()

    def trigger(self, repo, ref, gitlab_sha=None):
        key = (repo, ref)
        with self._lock:
            t = self._timers.get(key)
            if t:
                t.cancel()
            timer = threading.Timer(self.delay, self._fire, args=(repo, ref, gitlab_sha))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def _fire(self, repo, ref, gitlab_sha):
        with self._lock:
            self._timers.pop((repo, ref), None)
        try:
            self.worker(repo, ref, gitlab_sha)
        except Exception as e:  # noqa: BLE001
            log(f"webhook 処理でエラー: {e}")


def _verify_signature(cfg: Config, headers, body: bytes):
    """Gitea(X-Gitea-Signature: HMAC-SHA256) / GitLab(X-Gitlab-Token) の検証。"""
    if not cfg.webhook_secret:
        return True  # secret 未設定なら検証しない（LAN 限定運用向け）
    gitlab_token = headers.get("X-Gitlab-Token")
    if gitlab_token is not None:
        return hmac.compare_digest(gitlab_token, cfg.webhook_secret)
    sig = headers.get("X-Gitea-Signature")
    if sig is not None:
        mac = hmac.new(cfg.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, mac)
    return False


def parse_webhook(cfg: Config, headers, body: bytes):
    """webhook ペイロードから (repo_name, ref, after_sha) を取り出す。対象外なら None。"""
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    ref = data.get("ref")
    after = data.get("after")  # push イベントの新 HEAD（Gitea/GitLab 共通）
    # リポジトリ名の照合: ペイポードの repository.name / full path から設定の repo を探す
    repo_obj = data.get("repository", {}) or {}
    candidates = [repo_obj.get("name"), repo_obj.get("path_with_namespace"),
                  repo_obj.get("full_name")]
    matched = None
    for r in cfg.repos:
        if r.name in candidates or r.name == repo_obj.get("name"):
            matched = r.name
            break
    if matched is None and len(cfg.repos) == 1:
        matched = cfg.repos[0].name  # 単一リポジトリ運用なら決め打ち
    if not ref or matched is None:
        return None
    if not ref_in_scope(ref, cfg.include, cfg.exclude):
        return None
    # 削除 push（after が全 0）は既定で無視（§3.2 の delete opt-in）
    if after and set(after) == {"0"}:
        after = None
    return matched, ref, after


def make_handler(cfg: Config, debouncer: Debouncer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):  # デフォルトのアクセスログを黙らせる
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            if not _verify_signature(cfg, self.headers, body):
                self.send_response(401)
                self.end_headers()
                return
            parsed = parse_webhook(cfg, self.headers, body)
            self.send_response(202 if parsed else 204)
            self.end_headers()
            if parsed:
                repo, ref, after = parsed
                log(f"webhook 受信: {repo} {ref} (after={short(after)})")
                debouncer.trigger(repo, ref, after)

    return Handler


def serve(cfg: Config, state: State):
    def worker(repo, ref, gitlab_sha):
        r = cfg.repo(repo)
        if not r:
            return
        mirror = LocalMirror(r, timeout=cfg.git_timeout)
        mirror.ensure()
        with_backoff(lambda: reconcile_ref(mirror, cfg, state, ref, known_gitlab_sha=gitlab_sha))

    debouncer = Debouncer(cfg.debounce_seconds, worker)

    # cron バックストップ（長間隔）: webhook 取りこぼしを回収（§3.7）
    stop = threading.Event()

    def backstop():
        while not stop.wait(cfg.poll_interval_minutes * 60):
            log("cron バックストップ: allowlist 全 ref を照合")
            sync_all(cfg, state)

    threading.Thread(target=backstop, daemon=True).start()

    httpd = ThreadingHTTPServer((cfg.webhook_host, cfg.webhook_port), make_handler(cfg, debouncer))
    log(f"webhook 待受: http://{cfg.webhook_host}:{cfg.webhook_port}/  "
        f"（cron バックストップ {cfg.poll_interval_minutes} 分間隔）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("停止します")
    finally:
        stop.set()
        httpd.shutdown()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(description="Gitea ⇄ GitLab 双方向同期ボット")
    ap.add_argument("--config", required=True, help="設定ファイル（.yaml / .yml / .json）")
    ap.add_argument("--once", action="store_true", help="1 回同期して終了")
    ap.add_argument("--serve", action="store_true", help="webhook 待受 + cron バックストップ")
    ap.add_argument("--repo", help="対象リポジトリ名（--once 時）")
    ap.add_argument("--ref", help="対象 ref（例: refs/heads/main）")
    ap.add_argument("--dry-run", action="store_true", help="push せず予定だけ表示")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    state = State(cfg.state_dir)

    if args.serve:
        serve(cfg, state)
    elif args.once:
        sync_all(cfg, state, only_repo=args.repo, only_ref=args.ref, dry_run=args.dry_run)
    else:
        ap.error("--once か --serve のいずれかを指定してください")
    return 0


if __name__ == "__main__":
    sys.exit(main())
