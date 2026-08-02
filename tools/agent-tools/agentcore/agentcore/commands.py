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

import contextlib
import json
import os
import tempfile
import time

RECEIPT_KEEP = 200                    # 受理レシートを直近この件数だけ残す
RECEIPT_TTL_SEC = 24 * 3600           # かつ、これより古いレシートは消す（同期越しの閲覧猶予）
REJECTED_KEEP = 200                   # 失敗退避（.err）を直近この件数だけ残す
REJECTED_TTL_SEC = 7 * 24 * 3600      # かつ、これより古い .err は消す。受理レシートより長いのは
                                      # 「なぜ効かなかったか」を人が後から読む一次資料だから


def receipts_dir(dir_path) -> str:
    return os.path.join(os.fspath(dir_path), "processed")


def pending(dir_path, *, debounce_sec: float = 0.0, now: "float | None" = None,
            stop_at_deferred: bool = False) -> "list[str]":
    """取り込み待ちの指示ファイル（`*.json`）。無いディレクトリは空リスト。

    `processed/` はディレクトリなので `glob("*.json")` には現れない。名前順に返すのは、
    同じ対象への指示が複数あるときに**置かれた順**（ファイル名に時刻を含める規約）で
    処理するため。

    `debounce_sec > 0` のとき、**まだ読めない かつ 更新から猶予内**のファイルは返さない
    ——書きかけ（原子的に置かれなかった指示・人の手置き）を `.err` へ飛ばして失わない
    ための猶予で、次の巡回で読み直す。**読める指示は猶予しない**: 読める承認まで
    先送りすると、起こしたパスで承認が取り込まれず、そのパスが charter を再評価して
    承認済みマイルストーンを書き直す（agent-project が実際に踏んだ罠）。

    `stop_at_deferred=True` は、猶予したファイルより後ろも**その回は返さない**。
    ノードスコープの指示は同じ公示への「入札 → 中止」が順序を持ち、飛ばして後続を
    処理すると中止済みの板へ入札を書くことになる（ファイル名の時刻順＝処理順が規約）。
    """
    d = os.fspath(dir_path)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    except OSError:
        return []
    at = time.time() if now is None else now
    out: "list[str]" = []
    for name in names:
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        if debounce_sec > 0 and read_command(p)[0] is None:
            try:
                fresh = (at - os.stat(p).st_mtime) < debounce_sec
            except OSError:
                fresh = False
            if fresh:
                if stop_at_deferred:
                    break
                continue
        out.append(p)
    return out


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


def command_id(err_payload) -> str:
    """失敗退避（`.err`）の中身から対象 id を取り出す。読めなければ空文字。

    2 形式を読む: 新（`{"error":…, "command": {…, "id": …}}`）と、元の指示 JSON を
    そのまま改名していた旧形式（`{"command": "approve", "id": …}`）。旧 `.err` を
    `str.get` で踏むと AttributeError で落ちていたので、取り出しは 1 か所に置く。
    """
    if not isinstance(err_payload, dict):
        return ""
    cmd = err_payload.get("command")
    return str((cmd.get("id") if isinstance(cmd, dict) else err_payload.get("id")) or "")


def clear_rejected(dir_path, target_id: str) -> int:
    """同じ id への指示が通ったら、過去の失敗退避（`.err`）を消す。消した件数を返す。

    `.err` は書き手（dashboard）が「直前の指示は失敗した」を出す根拠になる。成功後も
    残すと、解決済みの失敗が同じ対象に出続ける（直ったのに失敗表示）。**掃除は成功時だけ**
    ——失敗の履歴を先に消すと、失敗が誰にも見えない元の不具合に戻る。
    """
    tid = str(target_id or "")
    if not tid:
        return 0
    d = os.fspath(dir_path)
    removed = 0
    try:
        names = [n for n in os.listdir(d) if n.endswith(".err")]
    except OSError:
        return 0
    for name in names:
        p = os.path.join(d, name)
        try:
            with open(p, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            continue
        if command_id(payload) != tid:
            continue
        try:
            os.unlink(p)
            removed += 1
        except OSError:
            pass
    return removed


def prune_rejected(dir_path, *, keep: int = REJECTED_KEEP,
                   ttl_sec: float = REJECTED_TTL_SEC) -> int:
    """失敗退避を件数上限と TTL で掃除する（gc 用）。消した件数を返す。

    `clear_rejected` は「同じ id の指示が次に通ったとき」しか消せない。対象が二度と
    来ない（板の公示は終端して消える）と `.err` は残り続けるので、期限でも掃く。
    """
    return _prune(os.fspath(dir_path), ".err", keep=keep, ttl_sec=ttl_sec)


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
        # payload を先に載せ、正規メタ（ok / source / processed_at）で上書きする。
        # 逆順だと呼び出し側が ok:false や偽の source を書けて受理証跡を偽装できる。
        body = {**(payload or {}), "ok": True, "source": source_name,
                "processed_at": now or _now_iso()}
        fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(dest)}.",
                                   suffix=".tmp", dir=rdir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
            os.replace(tmp, dest)
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise
        prune_receipts(dir_path, keep=keep, ttl_sec=ttl_sec)
    except OSError:
        pass
    return dest


def prune_receipts(dir_path, *, keep: int = RECEIPT_KEEP,
                   ttl_sec: float = RECEIPT_TTL_SEC) -> int:
    """受理レシートを件数上限と TTL で掃除する（履歴の肥大を防ぐ）。消した件数を返す。"""
    return _prune(receipts_dir(dir_path), ".json", keep=keep, ttl_sec=ttl_sec)


def _prune(dir_path: str, suffix: str, *, keep: int, ttl_sec: float) -> int:
    """ディレクトリ内の `*<suffix>` を「TTL 超過 → 古い順に件数上限」で掃く。消した件数を返す。

    受理レシート（`processed/*.json`）と失敗退避（`*.err`）で規則は同じ——保持数と期限が
    違うだけなので、掃除の実装は 1 つに保つ。
    """
    try:
        files = sorted((os.path.join(dir_path, n) for n in os.listdir(dir_path)
                        if n.endswith(suffix)),
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
