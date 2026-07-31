#!/usr/bin/env python3
"""
makaroshki-bridge — git をバックエンドにした Macaroni Messenger (makaroshki) を
ハブにして、人間（PC-A）とエージェント（PC-B の Hermes など）を非同期に橋渡しする。

  https://github.com/vanyapr/makaroshki

構成:

    PC-A（人間）                git リモート (ハブ)            PC-B（エージェント）
  ┌──────────────────┐        ┌───────────────────┐        ┌────────────────────────┐
  │ messenger.html   │ push   │  <repo>/.macaroni  │  pull  │ makaroshki-bridge      │
  │ （ブラウザで送信） │ ─────▶ │  chats/.../*.json  │ ◀───── │   ポーリング検知        │
  │                  │ pull   │  inbox/<id>/*.json │  push  │   → agent 実行          │
  │ ◀────── 返信表示  │ ◀───── │                    │ ─────▶ │   → 返信を投函           │
  └──────────────────┘        └───────────────────┘        └────────────────────────┘

このスクリプトは PC-B（エージェント側）で動かす。Macaroni のリポジトリをローカルに
クローンして定期的に pull し、人間からの新着メッセージを検知したら設定した「エージェント
実行コマンド」に渡し、その出力を Macaroni のメッセージとして書き戻して push する。

人間側（PC-A）は makaroshki の messenger.html をそのまま使えばよい。テスト用に本ツールの
`send` サブコマンドでも人間役のメッセージを投函できる。

依存:
  - git コマンド（PATH 上にあること）
  - Python 3.9+
  - PyYAML（YAML 設定を使う場合のみ。JSON 設定なら不要） pip install pyyaml

使い方:
  makaroshki-bridge run                  # 既定。ポーリングループを開始
  makaroshki-bridge once                 # 1 回だけ pull→処理→push して終了
  makaroshki-bridge send --chat <id> "本文"   # 人間役としてメッセージを投函（テスト用）
  makaroshki-bridge chats                # ハブに存在するチャット一覧
  makaroshki-bridge status               # 処理済み状態を表示
  makaroshki-bridge --config ~/makaroshki-bridge.yaml run

設定ファイルの探索順:
  1. --config で明示指定したパス
  2. カレントディレクトリの makaroshki-bridge.{yaml,yml,json}
  3. HOME の makaroshki-bridge.{yaml,yml,json}
"""

import argparse
import datetime
import json
import logging
import os
import random
import re
import shlex
import shutil
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 依存チェック ─────────────────────────────────────────────────────────────

if shutil.which("git") is None:
    print("[makaroshki-bridge] ERROR: git が見つかりません。PATH を確認してください。", file=sys.stderr)
    sys.exit(1)

try:
    import yaml  # type: ignore

    def _load_config_file(path: Path) -> dict:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

except ImportError:
    yaml = None  # type: ignore

    def _load_config_file(path: Path) -> dict:  # type: ignore[misc]
        if path.suffix.lower() in (".yaml", ".yml"):
            print("[makaroshki-bridge] ERROR: YAML 設定を読むには PyYAML が必要です。", file=sys.stderr)
            print("  pip install pyyaml （または JSON 設定を使ってください）", file=sys.stderr)
            sys.exit(1)
        with path.open(encoding="utf-8") as f:
            return json.load(f)


# ── ログ ─────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("makaroshki-bridge")

# ── 既定パス / 定数 ──────────────────────────────────────────────────────────

_HOME_DIR = Path.home() / ".makaroshki-bridge"
_DEFAULT_STATE_DIR = _HOME_DIR / "state"
_DEFAULT_REPO_DIR = _HOME_DIR / "hub"
_CONFIG_NAMES = ["makaroshki-bridge.yaml", "makaroshki-bridge.yml", "makaroshki-bridge.json"]

_CLIENT_VERSION = "makaroshki-bridge 1.0.0"
_PROTOCOL_VERSION = 1
_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits


# ── 設定読み込み ─────────────────────────────────────────────────────────────

def _find_config() -> Optional[Path]:
    for name in _CONFIG_NAMES:
        for base in (Path.cwd(), Path.home()):
            p = base / name
            if p.exists():
                return p
    return None


def load_config(path: Optional[Path]) -> dict:
    if path is None:
        path = _find_config()
    if path is None:
        print("[makaroshki-bridge] ERROR: 設定ファイルが見つかりません。", file=sys.stderr)
        print("  config.yaml.example をコピーして makaroshki-bridge.yaml を作成してください。", file=sys.stderr)
        sys.exit(1)
    log.info("設定ファイル: %s", path)
    cfg = _load_config_file(path)
    if not isinstance(cfg, dict):
        print("[makaroshki-bridge] ERROR: 設定ファイルの形式が不正です。", file=sys.stderr)
        sys.exit(1)
    return cfg


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))


# ── 時刻 / ID ────────────────────────────────────────────────────────────────

def now_iso() -> str:
    """ISO 8601（ミリ秒・末尾 Z）。例: 2026-06-11T08:31:25.054Z"""
    dt = datetime.datetime.now(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def iso_to_datepath(iso: str) -> Tuple[str, str, str]:
    """ISO 文字列から YYYY, MM, DD を取り出す（UTC 前提）。"""
    # "2026-06-11T08:31:25.054Z" → 日付部分はそのまま使える
    date = iso.split("T", 1)[0]
    y, m, d = date.split("-")
    return y, m, d


def _rand_suffix(n: int = 6) -> str:
    return "".join(random.choice(_SUFFIX_ALPHABET) for _ in range(n))


def make_message_id(created_at: str, client_id: str) -> str:
    """Macaroni 形式の message_id。<created_at の ':' を '-' に置換>_<CLIENT_ID>_<rand6>"""
    ts = created_at.replace(":", "-")
    return f"{ts}_{client_id}_{_rand_suffix()}"


# ── git ヘルパー ─────────────────────────────────────────────────────────────

class Git:
    def __init__(self, repo_dir: Path, branch: str, author_name: str, author_email: str) -> None:
        self.repo_dir = repo_dir
        self.branch = branch
        self.author_name = author_name
        self.author_email = author_email

    def _run(self, *args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
        cmd = [
            "git",
            "-c", f"user.name={self.author_name}",
            "-c", f"user.email={self.author_email}",
            "-C", str(self.repo_dir),
            *args,
        ]
        cp = subprocess.run(cmd, capture_output=capture, text=True)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} 失敗 (code {cp.returncode}): {(cp.stderr or cp.stdout).strip()}"
            )
        return cp

    def ensure_clone(self, remote: str) -> None:
        if (self.repo_dir / ".git").is_dir():
            return
        self.repo_dir.parent.mkdir(parents=True, exist_ok=True)
        log.info("ハブをクローン: %s → %s", remote, self.repo_dir)
        cp = subprocess.run(
            ["git", "clone", "--branch", self.branch, remote, str(self.repo_dir)],
            capture_output=True, text=True,
        )
        if cp.returncode != 0:
            # ブランチ未指定クローン（リモートに当該ブランチが無い場合のフォールバック）
            cp2 = subprocess.run(
                ["git", "clone", remote, str(self.repo_dir)], capture_output=True, text=True
            )
            if cp2.returncode != 0:
                raise RuntimeError(f"クローン失敗: {(cp.stderr or cp2.stderr).strip()}")

    def pull(self) -> None:
        # ローカルのコミットを保ったまま取り込む（id がユニークなので衝突はほぼ起きない）
        self._run("fetch", "origin", self.branch)
        # ローカルに未 push のコミットがあるかもしれないので rebase で取り込む
        cp = self._run("rebase", f"origin/{self.branch}", check=False)
        if cp.returncode != 0:
            log.warning("rebase 失敗、abort して reset で追従します: %s", (cp.stderr or "").strip())
            self._run("rebase", "--abort", check=False)
            self._run("reset", "--hard", f"origin/{self.branch}")

    def commit_push(self, paths: List[Path], message: str, retries: int = 4) -> bool:
        for p in paths:
            self._run("add", str(p))
        # 変更が無ければ何もしない
        if self._run("status", "--porcelain").stdout.strip() == "":
            return False
        self._run("commit", "-m", message)
        delay = 2
        for attempt in range(retries + 1):
            cp = self._run("push", "-u", "origin", self.branch, check=False)
            if cp.returncode == 0:
                return True
            log.warning("push 失敗 (試行 %d): %s", attempt + 1, (cp.stderr or "").strip())
            # リジェクト/ネットワーク両対応: 取り込み直してから再試行
            try:
                self.pull()
            except Exception as e:  # noqa: BLE001
                log.warning("再 pull に失敗: %s", e)
            if attempt < retries:
                time.sleep(delay)
                delay = min(delay * 2, 16)
        raise RuntimeError("push に繰り返し失敗しました。")


# ── Macaroni リポジトリ操作 ──────────────────────────────────────────────────

class Macaroni:
    def __init__(self, repo_dir: Path) -> None:
        self.root = repo_dir
        self.base = repo_dir / ".macaroni"

    # --- 読み取り ---

    def list_chats(self) -> List[str]:
        chats_dir = self.base / "chats"
        if not chats_dir.is_dir():
            return []
        return sorted(p.name for p in chats_dir.iterdir() if (p / "meta.json").is_file())

    def chat_meta(self, chat_id: str) -> dict:
        return self._read_json(self.base / "chats" / chat_id / "meta.json")

    def chat_members(self, chat_id: str) -> List[dict]:
        f = self.base / "chats" / chat_id / "members.json"
        if not f.is_file():
            return []
        return self._read_json(f).get("members", [])

    def read_messages(self, chat_id: str) -> List[dict]:
        """チャットの全メッセージを created_at, id 昇順で返す。"""
        msg_dir = self.base / "chats" / chat_id / "messages"
        if not msg_dir.is_dir():
            return []
        out: List[dict] = []
        for f in msg_dir.rglob("*.json"):
            try:
                out.append(self._read_json(f))
            except Exception as e:  # noqa: BLE001
                log.warning("メッセージ読み込み失敗 %s: %s", f, e)
        out.sort(key=lambda m: (m.get("created_at", ""), m.get("id", "")))
        return out

    def user_exists(self, client_id: str) -> bool:
        return (self.base / "users" / f"{client_id}.json").is_file()

    @staticmethod
    def _read_json(path: Path) -> dict:
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    # --- 書き込み ---

    def ensure_user(self, client_id: str, display_name: str) -> Optional[Path]:
        path = self.base / "users" / f"{client_id}.json"
        if path.is_file():
            return None
        self._write_json(path, {
            "version": _PROTOCOL_VERSION,
            "id": client_id,
            "display_name": display_name,
            "created_at": now_iso(),
            "meta": {"client": _CLIENT_VERSION},
        })
        return path

    def write_message(
        self,
        chat_id: str,
        from_id: str,
        from_name: str,
        to: List[str],
        text: str,
        reply_to: Optional[str] = None,
    ) -> Tuple[str, List[Path]]:
        """メッセージ本体 + 受信者ごとの inbox を書き出し、(message_id, 書いたパス一覧) を返す。"""
        created_at = now_iso()
        msg_id = make_message_id(created_at, from_id)
        y, m, d = iso_to_datepath(created_at)
        rel = f".macaroni/chats/{chat_id}/messages/{y}/{m}/{d}/{msg_id}.json"
        msg_path = self.root / rel
        self._write_json(msg_path, {
            "version": _PROTOCOL_VERSION,
            "id": msg_id,
            "chat_id": chat_id,
            "type": "text",
            "from": from_id,
            "from_name": from_name,
            "to": to,
            "created_at": created_at,
            "text": text,
            "reply_to": reply_to,
            "attachments": [],
            "meta": {"client": _CLIENT_VERSION},
            "signature": None,
        })
        written = [msg_path]
        for recipient in to:
            inbox_path = self.base / "inbox" / recipient / f"{msg_id}.json"
            self._write_json(inbox_path, {
                "version": _PROTOCOL_VERSION,
                "recipient": recipient,
                "message_id": msg_id,
                "chat_id": chat_id,
                "message_path": rel,
                "created_at": created_at,
            })
            written.append(inbox_path)
        return msg_id, written


# ── 処理済み状態 ─────────────────────────────────────────────────────────────

class State:
    """チャットごとに処理済み message_id を記録（二重処理防止）。"""

    def __init__(self, state_dir: Path, key: str) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        self.path = state_dir / f"{safe}.json"
        self.data: Dict[str, List[str]] = {}
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self.data = {}

    def processed(self, chat_id: str, msg_id: str) -> bool:
        return msg_id in self.data.get(chat_id, [])

    def mark(self, chat_id: str, msg_id: str) -> None:
        self.data.setdefault(chat_id, [])
        if msg_id not in self.data[chat_id]:
            self.data[chat_id].append(msg_id)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


# ── エージェント実行 ─────────────────────────────────────────────────────────

def build_prompt(template: str, msg: dict, chat_meta: dict) -> str:
    """受信メッセージからエージェントへ渡すプロンプト文字列を組み立てる。"""
    return template.format(
        text=msg.get("text", ""),
        from_id=msg.get("from", ""),
        from_name=msg.get("from_name", ""),
        chat_id=msg.get("chat_id", ""),
        chat_title=chat_meta.get("title", ""),
        message_id=msg.get("id", ""),
        created_at=msg.get("created_at", ""),
    )


def run_agent_command(cmd: str, prompt: str, cwd: Optional[str], timeout: int, env_extra: Dict[str, str]) -> str:
    """シェルコマンドを起動し、prompt を stdin で渡して stdout を返信本文として受け取る。"""
    env = dict(os.environ)
    env.update(env_extra)
    log.info("エージェント実行（command）: %s", cmd)
    cp = subprocess.run(
        cmd, shell=True, input=prompt, capture_output=True, text=True,
        cwd=cwd, env=env, timeout=timeout,
    )
    if cp.returncode != 0:
        log.warning("エージェントコマンドが非ゼロ終了 (code %d): %s", cp.returncode, cp.stderr.strip())
    reply = cp.stdout.strip()
    if not reply:
        reply = (cp.stderr.strip() or "(エージェントから空の応答)")
    return reply


# tmux ランナー用: kiro-cli / hermes など対話 TUI の入力待ちプロンプト行
_PROMPT_RE = re.compile(r"(^\s*[>?❯›]\s*$|!>|Ask me anything)", re.MULTILINE)


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def run_agent_tmux(
    session: str, start_cmd: str, prompt: str, cwd: Optional[str],
    startup_timeout: int, response_timeout: int,
) -> str:
    """tmux セッション上の対話エージェント（hermes chat 等）にプロンプトを送って応答を回収する。

    対話 TUI からの抽出はヒューリスティック。安定運用には command モードを推奨。
    """
    if shutil.which("tmux") is None:
        raise RuntimeError("tmux が見つかりません（runner: tmux には tmux が必要）。")

    exists = _tmux("has-session", "-t", session).returncode == 0
    if not exists:
        log.info("tmux セッション起動: %s", session)
        new_args = ["new-session", "-d", "-s", session]
        if cwd:
            new_args += ["-c", cwd]
        new_args += [start_cmd]
        if _tmux(*new_args).returncode != 0:
            raise RuntimeError("tmux セッションの起動に失敗しました。")
        # 起動とプロンプト表示を待つ
        deadline = time.time() + startup_timeout
        while time.time() < deadline:
            pane = _tmux("capture-pane", "-p", "-t", session).stdout
            if _PROMPT_RE.search(pane):
                break
            time.sleep(1)

    before = _tmux("capture-pane", "-p", "-t", session).stdout

    # 1 行に畳んで送信（改行が途中送信を誘発するのを避ける）
    line = " ".join(prompt.splitlines())
    _tmux("send-keys", "-t", session, "-l", line)
    _tmux("send-keys", "-t", session, "Enter")

    # 応答待ち: プロンプト再出現 + 出力の安定を待つ
    deadline = time.time() + response_timeout
    last = ""
    stable = 0
    while time.time() < deadline:
        time.sleep(2)
        cur = _tmux("capture-pane", "-p", "-S", "-2000", "-t", session).stdout
        if cur == last:
            stable += 1
        else:
            stable = 0
        last = cur
        if stable >= 2 and _PROMPT_RE.search(cur):
            break

    return _extract_reply(before, last)


def _extract_reply(before: str, after: str) -> str:
    """送信前と後のペイン内容を比べ、新規に出た行から返信本文を推定する。"""
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    # 末尾共通部分を除いて、新規出力だけを取る
    new_lines = after_lines[len(before_lines):] if len(after_lines) > len(before_lines) else after_lines
    cleaned = []
    for ln in new_lines:
        if _PROMPT_RE.search(ln):
            continue
        cleaned.append(ln)
    reply = "\n".join(cleaned).strip()
    return reply or "(エージェントの応答を取得できませんでした)"


# ── 1 チャットの処理 ─────────────────────────────────────────────────────────

def process_chat(
    chat_id: str,
    mac: Macaroni,
    git: Git,
    state: State,
    agent_cfg: dict,
    bot_id: str,
    bot_name: str,
    respond_to: str,
    dry_run: bool,
) -> int:
    """新着メッセージを処理し、処理したメッセージ数を返す。"""
    chat_meta = mac.chat_meta(chat_id)
    members = [m.get("id") for m in mac.chat_members(chat_id)]
    recipients = [m for m in members if m and m != bot_id]

    handled = 0
    for msg in mac.read_messages(chat_id):
        msg_id = msg.get("id", "")
        if not msg_id or state.processed(chat_id, msg_id):
            continue
        if msg.get("from") == bot_id:
            # 自分の投稿は処理済み扱い（ループ防止）
            state.mark(chat_id, msg_id)
            continue
        if respond_to == "addressed" and bot_id not in (msg.get("to") or []):
            state.mark(chat_id, msg_id)
            continue

        log.info("新着 [%s] from %s: %s", chat_id, msg.get("from_name") or msg.get("from"),
                 (msg.get("text") or "")[:80])

        if dry_run:
            log.info("(dry-run) 応答生成をスキップ")
            state.mark(chat_id, msg_id)
            handled += 1
            continue

        prompt = build_prompt(agent_cfg.get("prompt_template", "{text}"), msg, chat_meta)
        env_extra = {
            "MACARONI_CHAT_ID": chat_id,
            "MACARONI_FROM": msg.get("from", ""),
            "MACARONI_FROM_NAME": msg.get("from_name", ""),
            "MACARONI_MESSAGE_ID": msg_id,
        }
        try:
            reply = _run_agent(agent_cfg, prompt, env_extra)
        except Exception as e:  # noqa: BLE001
            log.error("エージェント実行に失敗: %s", e)
            # 失敗時は処理済みにせず、次回再試行
            continue

        # 返信を Macaroni に書き戻す
        to = recipients if recipients else [msg.get("from")]
        new_id, written = mac.write_message(
            chat_id=chat_id, from_id=bot_id, from_name=bot_name,
            to=[r for r in to if r], text=reply, reply_to=msg_id,
        )
        user_file = mac.ensure_user(bot_id, bot_name)
        if user_file:
            written.append(user_file)
        git.commit_push(written, f"agent reply in {chat_id} ({new_id})")
        log.info("返信を投函: %s", new_id)

        state.mark(chat_id, msg_id)
        state.save()
        handled += 1
    return handled


def _run_agent(agent_cfg: dict, prompt: str, env_extra: Dict[str, str]) -> str:
    runner = agent_cfg.get("runner", "command")
    cwd = agent_cfg.get("cwd")
    if cwd:
        cwd = str(_expand(cwd))
    if runner == "command":
        return run_agent_command(
            agent_cfg["command"], prompt, cwd,
            int(agent_cfg.get("timeout_seconds", 600)), env_extra,
        )
    if runner == "tmux":
        return run_agent_tmux(
            agent_cfg.get("session", "makaroshki-agent"),
            agent_cfg["command"], prompt, cwd,
            int(agent_cfg.get("startup_timeout_seconds", 60)),
            int(agent_cfg.get("response_timeout_seconds", 300)),
        )
    raise RuntimeError(f"未知の runner: {runner}（command / tmux のみ対応）")


# ── 全体ループ ───────────────────────────────────────────────────────────────

def _setup(cfg: dict) -> Tuple[Macaroni, Git, State, dict]:
    hub = cfg.get("hub", {})
    remote = hub.get("remote")
    if not remote:
        print("[makaroshki-bridge] ERROR: hub.remote（ハブ git リモート URL）が未設定です。", file=sys.stderr)
        sys.exit(1)
    repo_dir = _expand(hub.get("repo_dir", str(_DEFAULT_REPO_DIR)))
    branch = hub.get("branch", "main")

    bot = cfg.get("agent", {}).get("identity", {})
    author_name = bot.get("display_name", "Hermes Agent")
    author_email = bot.get("git_email", "hermes@example.com")

    git = Git(repo_dir, branch, author_name, author_email)
    git.ensure_clone(remote)
    mac = Macaroni(repo_dir)

    state_dir = _expand(cfg.get("state_dir", str(_DEFAULT_STATE_DIR)))
    state = State(state_dir, key=remote)
    return mac, git, state, cfg


def cmd_once(cfg: dict, dry_run: bool, mark_backlog: bool = False) -> int:
    mac, git, state, cfg = _setup(cfg)
    agent_cfg = cfg.get("agent", {})
    bot_id = agent_cfg.get("identity", {}).get("client_id")
    bot_name = agent_cfg.get("identity", {}).get("display_name", "Hermes")
    if not bot_id:
        print("[makaroshki-bridge] ERROR: agent.identity.client_id が未設定です（4 文字推奨）。", file=sys.stderr)
        sys.exit(1)
    respond_to = cfg.get("respond_to", "all")

    try:
        git.pull()
    except Exception as e:  # noqa: BLE001
        log.error("pull 失敗: %s", e)
        return 0

    watch = cfg.get("watch_chats") or mac.list_chats()
    total = 0
    for chat_id in watch:
        if mark_backlog:
            # 既存メッセージは応答せず処理済みにする（初回のバックログ無視）
            for msg in mac.read_messages(chat_id):
                if msg.get("id"):
                    state.mark(chat_id, msg["id"])
            state.save()
            continue
        total += process_chat(chat_id, mac, git, state, agent_cfg, bot_id, bot_name, respond_to, dry_run)
    state.save()
    if mark_backlog:
        log.info("既存メッセージをバックログとして処理済みに記録しました。")
    return total


def cmd_run(cfg: dict, dry_run: bool) -> None:
    interval = int(cfg.get("poll_interval_seconds", 30))
    log.info("ポーリング開始（間隔 %d 秒）。Ctrl+C で終了。", interval)
    # 起動時に既存を無視する設定
    if cfg.get("ignore_backlog_on_start", True):
        cmd_once(cfg, dry_run=False, mark_backlog=True)
    try:
        while True:
            try:
                n = cmd_once(cfg, dry_run)
                if n:
                    log.info("%d 件処理しました。", n)
            except Exception as e:  # noqa: BLE001
                log.error("ループ中エラー: %s", e)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("終了します。")


def cmd_send(cfg: dict, chat_id: str, text: str, as_id: Optional[str], as_name: Optional[str]) -> None:
    """人間役としてメッセージを投函（テスト・CLI 利用向け）。"""
    mac, git, state, cfg = _setup(cfg)
    sender = cfg.get("sender", {})
    from_id = as_id or sender.get("client_id")
    from_name = as_name or sender.get("display_name", "Human")
    if not from_id:
        print("[makaroshki-bridge] ERROR: 送信者 client_id が必要です（--as または sender.client_id）。", file=sys.stderr)
        sys.exit(1)
    git.pull()
    members = [m.get("id") for m in mac.chat_members(chat_id)]
    to = [m for m in members if m and m != from_id]
    msg_id, written = mac.write_message(chat_id, from_id, from_name, to, text, reply_to=None)
    user_file = mac.ensure_user(from_id, from_name)
    if user_file:
        written.append(user_file)
    git.commit_push(written, f"message in {chat_id} ({msg_id})")
    log.info("送信しました: %s", msg_id)


def cmd_chats(cfg: dict) -> None:
    mac, _git, _state, _cfg = _setup(cfg)
    _git.pull()
    chats = mac.list_chats()
    if not chats:
        print("チャットがありません。")
        return
    for c in chats:
        meta = mac.chat_meta(c)
        members = ", ".join(m.get("id", "?") for m in mac.chat_members(c))
        print(f"  {c}\n    title: {meta.get('title','')}\n    members: {members}")


def cmd_status(cfg: dict) -> None:
    _mac, _git, state, _cfg = _setup(cfg)
    print(f"state file: {state.path}")
    if not state.data:
        print("  （処理済みメッセージはまだありません）")
        return
    for chat_id, ids in state.data.items():
        print(f"  {chat_id}: {len(ids)} 件処理済み")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="makaroshki-bridge", description="Macaroni Messenger を git ハブにした人間↔エージェント橋渡し")
    parser.add_argument("--config", type=str, default=None, help="設定ファイルのパス")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="ポーリングループを開始（既定）")
    p_once = sub.add_parser("once", help="1 回だけ処理して終了")
    p_once.add_argument("--dry-run", action="store_true", help="エージェント実行・投函をせず検知のみ")

    p_send = sub.add_parser("send", help="メッセージを投函（人間役・テスト用）")
    p_send.add_argument("--chat", required=True, help="チャット ID")
    p_send.add_argument("--as", dest="as_id", default=None, help="送信者 client_id")
    p_send.add_argument("--name", dest="as_name", default=None, help="送信者表示名")
    p_send.add_argument("text", help="本文")

    sub.add_parser("chats", help="ハブのチャット一覧")
    sub.add_parser("status", help="処理済み状態を表示")

    args = parser.parse_args(argv)
    cfg = load_config(Path(args.config) if args.config else None)

    command = args.command or "run"
    if command == "run":
        cmd_run(cfg, dry_run=False)
    elif command == "once":
        n = cmd_once(cfg, dry_run=args.dry_run)
        log.info("完了（%d 件処理）。", n)
    elif command == "send":
        cmd_send(cfg, args.chat, args.text, args.as_id, args.as_name)
    elif command == "chats":
        cmd_chats(cfg)
    elif command == "status":
        cmd_status(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
