"""HubBus — hub サーバを転送層にするバス実装（設計書 §5.2、P2）。

GitBus と同じく**ローカルミラー**（workdir）上でファイル操作し、sync_pull/push で
hub と差分同期する。協調ロジック（claim・状態導出）は他バスと完全に同一。

- ミラーのレイアウトは LocalBus と同じ `missions/<mid>/…`（hub のデータディレクトリも
  同型 — hub ホストの dashboard はそこを直接読める）。
- pull: `GET /list?since=<rev>` で差分パスを列挙し、変わったファイルだけ取得。
  間隔律速（claim の勝者確認は force）。
- push: ミラーを走査し、前回 push 時とハッシュが変わった**自分の書き込み分**だけ PUT。
  所有権分割（§4.2）によりリモートと衝突しない（hub は最後の書き込みを保持するだけ）。
- 認証: 環境変数 AGENT_AMIGOS_HUB_TOKEN の Bearer。プロキシは**常に迂回**する
  （オンプレ LAN 前提。HTTPS_PROXY 環境でも hub へ直接届く）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request

from .bus import Bus, MissionPaths
from .util import log, read_json, write_json_atomic

DEFAULT_PULL_INTERVAL = 5.0


def _pull_interval() -> float:
    try:
        return float(os.environ.get("AGENT_AMIGOS_PULL_INTERVAL", DEFAULT_PULL_INTERVAL))
    except ValueError:
        return DEFAULT_PULL_INTERVAL


class HubBus(Bus):
    kind = "hub"

    def __init__(self, url: str, workdir: "str | None" = None,
                 pull_interval: "float | None" = None):
        self.url = url.rstrip("/")
        digest = hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:8]
        self.root = os.path.abspath(os.path.expanduser(
            workdir or os.path.join("~", ".agents", "amigos", "hub", digest)))
        self.pull_interval = pull_interval if pull_interval is not None else _pull_interval()
        os.makedirs(self.root, exist_ok=True)
        # オンプレ LAN 前提: 環境のプロキシ設定を常に迂回して hub へ直接届ける
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._state_path = os.path.join(self.root, ".hub-state.json")
        st = read_json(self._state_path) or {}
        self._server_rev = int(st.get("server_rev") or 0)
        self._pushed = {str(k): str(v) for k, v in dict(st.get("pushed") or {}).items()}
        self._last_pull = 0.0

    # --- HTTP 低レベル ------------------------------------------------------
    def _request(self, method: str, path: str, body: "bytes | None" = None):
        req = urllib.request.Request(self.url + path, data=body, method=method)
        token = os.environ.get("AGENT_AMIGOS_HUB_TOKEN", "")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with self._opener.open(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"[agent-amigos] hub に接続できません: {self.url} ({e})")

    def _save_state(self) -> None:
        write_json_atomic(self._state_path,
                          {"server_rev": self._server_rev, "pushed": self._pushed})

    # --- 同期 ---------------------------------------------------------------
    def sync_pull(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_pull < self.pull_interval:
            return
        self._last_pull = now
        code, body = self._request("GET", f"/list?prefix=missions/&since={self._server_rev}")
        if code == 401:
            raise RuntimeError("[agent-amigos] hub 認証に失敗しました"
                               "（AGENT_AMIGOS_HUB_TOKEN を確認してください）")
        if code != 200:
            log("hubbus", f"list 失敗 (HTTP {code}) — 次の同期で再試行")
            return
        data = json.loads(body)
        for ent in data.get("files") or []:
            rel = str(ent.get("path") or "")
            code, content = self._request("GET", f"/o/{rel}")
            if code != 200:
                continue        # 取得までに削除された等 — 次の list で追いつく
            local = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            tmp = f"{local}.tmp.{os.getpid()}"
            with open(tmp, "wb") as f:
                f.write(content)
            os.replace(tmp, local)
            # 取り込んだ内容を push 済み扱いにする（自分の書き戻しループを作らない）
            self._pushed[rel] = hashlib.sha1(content).hexdigest()
        self._server_rev = int(data.get("rev") or self._server_rev)
        self._save_state()

    def sync_push(self, msg: str = "") -> None:
        base = os.path.join(self.root, "missions")
        changed = 0
        for dirpath, _dirs, names in os.walk(base):
            for name in sorted(names):
                if ".tmp." in name or name.startswith("."):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self.root).replace(os.sep, "/")
                try:
                    with open(full, "rb") as f:
                        content = f.read()
                except OSError:
                    continue
                digest = hashlib.sha1(content).hexdigest()
                if self._pushed.get(rel) == digest:
                    continue
                code, body = self._request("PUT", f"/o/{rel}", content)
                if code == 401:
                    raise RuntimeError("[agent-amigos] hub 認証に失敗しました"
                                       "（AGENT_AMIGOS_HUB_TOKEN を確認してください）")
                if code not in (200, 204):
                    log("hubbus", f"PUT {rel} 失敗 (HTTP {code}) — 次の同期で再試行")
                    continue
                self._pushed[rel] = digest
                changed += 1
        if changed:
            self._save_state()

    # --- ミッションのライフサイクル -----------------------------------------
    def remove_mission(self, mission_id: str) -> None:
        prefix = f"missions/{mission_id}"
        self._request("DELETE", f"/tree?prefix={prefix}")
        import shutil
        shutil.rmtree(os.path.join(self.root, "missions", mission_id), ignore_errors=True)
        self._pushed = {k: v for k, v in self._pushed.items()
                        if not k.startswith(prefix + "/")}
        self._save_state()

    def mission(self, mission_id: str) -> MissionPaths:
        d = os.path.join(self.root, "missions", mission_id)
        if not os.path.isfile(os.path.join(d, "mission.json")):
            self.sync_pull(force=True)      # 新規ミラー: まず hub から取り込む
        return MissionPaths(d, mission_id)

    def list_missions(self) -> list:
        self.sync_pull()
        base = os.path.join(self.root, "missions")
        try:
            return sorted(n for n in os.listdir(base)
                          if os.path.isfile(os.path.join(base, n, "mission.json")))
        except FileNotFoundError:
            return []
