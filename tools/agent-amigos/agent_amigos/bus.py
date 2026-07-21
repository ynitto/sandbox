"""バス — ミッションの全状態が置かれるファイル空間（真実は常にバス上のファイル）。

P0 は LocalBus（同一ディレクトリ共有・sync は no-op）のみ。
GitBus（専用バスリポジトリ＋ミッション別ブランチ）と HubBus は P1/P2
（設計書 §5）。協調ロジックは転送層に依存しないよう、すべて Bus の
パスヘルパ経由でファイルを読み書きする。

書き込み規律（設計書 §4.2）: 書き込み所有権はパス単位で分割される。
このモジュールはレイアウト（どこに何があるか）だけを知り、所有権の強制は
呼び出し側（runner のアクション封筒検証・owner コマンド）が行う。
"""
from __future__ import annotations

import glob
import os

from .util import read_json, write_json_atomic


class MissionPaths:
    """ミッションディレクトリ配下のレイアウト（設計書 §4.1）。

    root はミッション内容の実体ディレクトリ:
    LocalBus では `<bus>/missions/<mid>/`、GitBus では `mission/<mid>` ブランチの
    クローン作業ツリー（リポジトリ直下が内容ルート）。"""

    def __init__(self, root: str, mission_id: str):
        self.root = root
        self.mission_id = mission_id

    def mission_json(self) -> str:
        return os.path.join(self.root, "mission.json")

    def design_doc(self) -> str:
        return os.path.join(self.root, "design-doc.md")

    def role_json(self, role_id: str) -> str:
        return os.path.join(self.root, "roles", f"{role_id}.json")

    def roles_dir(self) -> str:
        return os.path.join(self.root, "roles")

    def assignment(self, role_id: str, node_id: str) -> str:
        return os.path.join(self.root, "assignments", role_id, f"{node_id}.json")

    def assignments_dir(self, role_id: str) -> str:
        return os.path.join(self.root, "assignments", role_id)

    def roster(self) -> str:
        return os.path.join(self.root, "roster.json")

    def status(self, who: str) -> str:
        return os.path.join(self.root, "status", f"{who}.json")

    def status_dir(self) -> str:
        return os.path.join(self.root, "status")

    def events(self, who: str) -> str:
        return os.path.join(self.root, "events", f"{who}.jsonl")

    def events_dir(self) -> str:
        return os.path.join(self.root, "events")

    def channel_all_dir(self, who: "str | None" = None) -> str:
        d = os.path.join(self.root, "channels", "all")
        return os.path.join(d, who) if who else d

    def inbox_dir(self, role_id: str) -> str:
        return os.path.join(self.root, "inbox", role_id)

    def artifacts_dir(self, role_id: str) -> str:
        return os.path.join(self.root, "artifacts", role_id)

    def decisions(self) -> str:
        return os.path.join(self.root, "decisions.jsonl")

    def rejections_dir(self) -> str:
        return os.path.join(self.root, "rejections")

    def pruned_dir(self) -> str:
        return os.path.join(self.root, "pruned")

    def pruned(self, role_id: str) -> str:
        return os.path.join(self.root, "pruned", f"{role_id}.json")

    def conductor_state(self) -> str:
        return os.path.join(self.root, "conductor.json")

    def deliverable_dir(self) -> str:
        return os.path.join(self.root, "deliverable")

    def manifest(self) -> str:
        return os.path.join(self.root, "deliverable", "MANIFEST.json")

    def final(self) -> str:
        return os.path.join(self.root, "final.json")

    def cancelled(self) -> str:
        return os.path.join(self.root, "cancelled.json")

    def exists(self) -> bool:
        return os.path.isfile(self.mission_json())


class Bus:
    """LocalBus: 同一ディレクトリ共有。sync_pull/sync_push は転送層のフック
    （LocalBus では no-op。GitBus はここで pull --rebase / commit+push を行う予定）。"""

    kind = "local"

    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.expanduser(root))

    # --- 転送層フック -------------------------------------------------------
    def sync_pull(self, force: bool = False) -> None:
        """最新化する。force=True は間隔律速を無視する（claim の勝者確認など、
        鮮度がプロトコルの正しさに効く箇所で使う）。LocalBus は no-op。"""
        pass

    def sync_push(self, msg: str = "") -> None:
        pass

    # --- ミッションのライフサイクルフック -----------------------------------
    def prepare_mission(self, mission_id: str) -> None:
        """公示前の準備（GitBus: mission/<mid> ブランチのローカル作成）。"""
        pass

    def register_mission(self, mission_id: str, meta: dict) -> None:
        """公示の登録（GitBus: main の index/<mid>.json 追記）。LocalBus は
        ディレクトリ走査で発見できるため no-op。"""
        pass

    def remove_mission(self, mission_id: str) -> None:
        """gc: ミッションを掃除する（GitBus: ブランチ削除 + index 除去）。"""
        import shutil
        shutil.rmtree(self.mission(mission_id).root, ignore_errors=True)

    # --- レイアウト ---------------------------------------------------------
    def mission(self, mission_id: str) -> MissionPaths:
        return MissionPaths(os.path.join(self.root, "missions", mission_id), mission_id)

    def list_missions(self) -> list:
        pat = os.path.join(self.root, "missions", "*", "mission.json")
        out = []
        for p in sorted(glob.glob(pat)):
            out.append(os.path.basename(os.path.dirname(p)))
        return out


def make_bus(spec: str, workdir: "str | None" = None) -> Bus:
    """バス指定からバス実装を作る。
    - ローカルディレクトリ: そのままパス
    - `git+<url>`: 専用バスリポジトリ（ミッション別ブランチ、設計書 §5.1）
    - `hub+<url>`: hub サーバ経由（`agent-amigos hub` の対向、設計書 §5.2）
    """
    s = str(spec or "").strip()
    if s.startswith("git+"):
        from .gitbus import GitBus
        return GitBus(s[4:], workdir=workdir)
    if s.startswith("hub+"):
        from .hubbus import HubBus
        return HubBus(s[4:], workdir=workdir)
    if not s:
        raise SystemExit("[agent-amigos] バスのパスを指定してください（--bus <dir>）")
    return Bus(s)


class TurnTxn:
    """ターン原子性（設計書 §6.6）のローカル近似。

    1 ターンの成果（アクション封筒の適用 + events 追記 + status 更新）を先に
    メモリへ積み、`apply()` で「成果物 → メッセージ → status/events」の順に
    一括適用する。LocalBus では各ファイルの書き込みは atomic（tmp → rename）、
    順序は「読み手が中間状態を観測しても矛盾しない」向き（status/events が
    最後 = ターン完了の宣言が最後）に固定する。GitBus（P1）ではこの一括が
    そのまま単一コミットになる。
    """

    def __init__(self):
        self._writes: "list[tuple[str, object]]" = []      # (path, json データ)
        self._raw_writes: "list[tuple[str, str]]" = []     # (path, テキスト)
        self._appends: "list[tuple[str, dict]]" = []       # (path, レコード)

    def write_json(self, path: str, data) -> None:
        self._writes.append((path, data))

    def write_text(self, path: str, text: str) -> None:
        self._raw_writes.append((path, text))

    def append_jsonl(self, path: str, record: dict) -> None:
        self._appends.append((path, record))

    def apply(self, bus: Bus, msg: str = "") -> None:
        from .util import append_jsonl
        for path, text in self._raw_writes:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = f"{path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        for path, data in self._writes:
            write_json_atomic(path, data)
        for path, record in self._appends:
            append_jsonl(path, record)
        bus.sync_push(msg)


def read_all_json(dir_path: str) -> "dict[str, dict]":
    """ディレクトリ直下の *.json を {ファイル名(拡張子なし): データ} で読む。
    壊れた/書きかけ（*.tmp）は無視する。"""
    out = {}
    try:
        names = sorted(os.listdir(dir_path))
    except FileNotFoundError:
        return out
    for name in names:
        if not name.endswith(".json") or ".tmp." in name:
            continue
        data = read_json(os.path.join(dir_path, name))
        if isinstance(data, dict):
            out[name[:-5]] = data
    return out
