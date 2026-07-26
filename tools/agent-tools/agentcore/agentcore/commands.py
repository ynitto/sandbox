"""agentcore.commands — 指示ドロップ（`commands/<name>.json`）の取り込み規約の単一定義。

CLI を実行できない場所（Windows の dashboard・本体は WSL 内、など）から人の指示を渡す口として、
agent-project は**プロジェクトの状態リポジトリ配下**に `commands/` を持っている。
Phase 4 で**ノードスコープ**（`~/.agents/commands/`）にも同じ口が要るようになった——
板（agent-board）はプロジェクトに属さないので、プロジェクト経由の口しか無いと
**プロジェクトを 1 つも持たない PC から板を操作できない**。

同じ規約を 2 実装にすると、利用者から見える挙動（送信済み → 受理済み → 失敗バナー）が
2 種類になる。ここが持つのは**スコープに依存しない土台**だけ:

    <dir>/<name>.json            … 指示（書き手が原子的に置く）
    <dir>/processed/<name>.json  … 受理レシート（消費側が書く。「受理済み」表示の根拠）
    <dir>/<name>.json.err        … 取り込み失敗（理由つき。失敗バナーの根拠）

「何を実行するか」（approve / board-bid …）はスコープごとの消費側が持つ——
ここには語彙を置かない。
"""
from __future__ import annotations

import json
import os
import time

RECEIPT_KEEP = 200                    # 受理レシートを直近この件数だけ残す
RECEIPT_TTL_SEC = 24 * 3600           # かつ、これより古いレシートは消す（同期越しの閲覧猶予）


def receipts_dir(dir_path) -> str:
    return os.path.join(os.fspath(dir_path), "processed")


def pending(dir_path) -> "list[str]":
    """取り込み待ちの指示ファイル（`*.json`）。無いディレクトリは空リスト。

    `processed/` はディレクトリなので `glob("*.json")` には現れない。名前順に返すのは、
    同じ対象への指示が複数あるときに**置かれた順**（ファイル名に時刻を含める規約）で
    処理するため。
    """
    d = os.fspath(dir_path)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    except OSError:
        return []
    return [os.path.join(d, n) for n in names if os.path.isfile(os.path.join(d, n))]


def read_command(path) -> "tuple[dict | None, str]":
    """指示を読む。`(rec, why)` を返し、`rec` が None なら `why` が理由（未完・不正）。

    「取り込めるか」の唯一の判定点。「起こすか」と「処理するか」が別の述語を持つと、
    起きたのに取り込めないパスが生まれる（agent-project が実際に踏んだ罠）。
    """
    try:
        with open(os.fspath(path), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"JSON 解析失敗: {e}"
    if not isinstance(rec, dict):
        return None, "オブジェクトではない"
    return rec, ""


def reject(path, why: str, *, now: "str | None" = None) -> str:
    """処理できない指示を `<name>.json.err` へ退避する（無限再試行を防ぐ）。退避先を返す。

    退避先には失敗理由も書く——「なぜ通らなかったか」がファイルに無いと、画面には
    成功トーストだけが出て「押しても何も起きない」に見える。元の指示は `command` として保つ。
    """
    src = os.fspath(path)
    dest = f"{src}.err"
    rec = read_command(src)[0]
    payload = {"error": str(why), "failed_at": now or _now_iso(), "command": rec}
    try:
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.unlink(src)
    except OSError:
        try:
            os.replace(src, dest)
        except OSError:
            try:
                os.unlink(src)
            except OSError:
                pass
    return dest


def write_receipt(dir_path, source_name: str, payload: dict, *, now: "str | None" = None,
                  keep: int = RECEIPT_KEEP, ttl_sec: float = RECEIPT_TTL_SEC) -> str:
    """取り込みに成功した指示のレシートを `processed/<name>.json` へ原子的に残す。

    成功時にファイルを消すだけだと、リモートの閲覧者からは「保留中（未取り込み）」と
    「処理済み」が区別できない。書き手は元のファイル名（`source`）で自分の『送信済み』表示を
    『受理済み』へ更新できる。best-effort——本処理は既に成功しているので、ここの失敗で
    呼び出し側を落とさない。
    """
    rdir = receipts_dir(dir_path)
    dest = os.path.join(rdir, source_name)
    try:
        os.makedirs(rdir, exist_ok=True)
        body = {"ok": True, "source": source_name, "processed_at": now or _now_iso(), **payload}
        tmp = f"{dest}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
        os.replace(tmp, dest)
        prune_receipts(dir_path, keep=keep, ttl_sec=ttl_sec)
    except OSError:
        pass
    return dest


def prune_receipts(dir_path, *, keep: int = RECEIPT_KEEP,
                   ttl_sec: float = RECEIPT_TTL_SEC) -> int:
    """受理レシートを件数上限と TTL で掃除する（履歴の肥大を防ぐ）。消した件数を返す。"""
    rdir = receipts_dir(dir_path)
    try:
        files = sorted((os.path.join(rdir, n) for n in os.listdir(rdir) if n.endswith(".json")),
                       key=lambda p: os.stat(p).st_mtime)
    except OSError:
        return 0
    now = time.time()
    removed = 0
    survivors: "list[str]" = []
    for p in files:
        try:
            if now - os.stat(p).st_mtime > ttl_sec:
                os.unlink(p)
                removed += 1
            else:
                survivors.append(p)
        except OSError:
            pass
    for p in survivors[:max(0, len(survivors) - max(0, keep))]:
        try:
            os.unlink(p)
            removed += 1
        except OSError:
            pass
    return removed


def _now_iso() -> str:
    from .heartbeat import now_iso
    return now_iso()
