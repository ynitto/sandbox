"""納品棚 — accept 成立時に deliverable をオーナーホームへ搬出する（push 型納品）。

バスの `deliverable/` は受け渡しの場であり gc 対象なので、accept し忘れ・collect し忘れは
成果物の喪失に直結する。accept という明示の意思表示があった時点で手元へ確保する。

    <home>/deliveries/<mid>/      # 納品棚（永続・gc 既定は無期限）
      <role>/<...>                #   成果物本体（文書・調査結果・小さい画像）
      delivery.json               #   納品書（正典: schemas/delivery.schema.json）
    <home>/DELIVERY.md            # 受領一覧（1 ミッション 1 行。agent-project と同じ流儀）

正本の置き場は種別で分ける（設計 §3.2）: コードは mission.workspace.repo の統合ブランチが
正本で納品棚には参照だけ、MAX_EXPORT_BYTES を超えるファイルは搬出せず参照だけを納品書に残す。
"""
from __future__ import annotations

import os
import shutil

from .bus import MissionPaths
from .mission import budget_spent_seconds, current_round
from .util import log, now_iso, read_json, write_json_atomic

# 搬出するファイルの上限。超えるものはバス（または repo）に置いたまま参照だけ残す。
MAX_EXPORT_BYTES = 10 * 1024 * 1024


def deliveries_dir(home: str) -> str:
    return os.path.join(home, "deliveries")


def delivery_dir(home: str, mission_id: str) -> str:
    return os.path.join(deliveries_dir(home), mission_id)


def delivery_json(home: str, mission_id: str) -> str:
    return os.path.join(delivery_dir(home, mission_id), "delivery.json")


def delivery_index(home: str) -> str:
    return os.path.join(home, "DELIVERY.md")


def _manifest_meta(manifest: dict) -> "dict[str, tuple[str, str]]":
    """MANIFEST の files から {相対パス: (ロール, ハッシュ)} を作る。"""
    out = {}
    for role_id, entries in (manifest.get("files") or {}).items():
        for ent in entries or []:
            path = str((ent or {}).get("path") or "")
            if path:
                out[path] = (role_id, str(ent.get("sha256_16") or ""))
    return out


def _copy_deliverable(mp: MissionPaths, dest: str, manifest: dict) -> list:
    """deliverable/ を納品棚へ複製し、納品書のファイル一覧を作る。
    MANIFEST.json は納品書に置き換わるので複製しない。"""
    meta = _manifest_meta(manifest)
    src_root = mp.deliverable_dir()
    files = []
    for base, _dirs, names in os.walk(src_root):
        for name in sorted(names):
            if name == "MANIFEST.json" or ".tmp." in name:
                continue
            src = os.path.join(base, name)
            rel = os.path.relpath(src, src_root)
            role, digest = meta.get(rel.replace(os.sep, "/"), ("", ""))
            try:
                size = os.path.getsize(src)
            except OSError:
                continue
            row = {"path": rel.replace(os.sep, "/"), "role": role,
                   "sha256_16": digest, "bytes": size, "exported": True}
            if size > MAX_EXPORT_BYTES:
                row.update(exported=False, skip_reason="size")
            else:
                dst = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
            files.append(row)
    return sorted(files, key=lambda r: r["path"])


def _append_index(home: str, record: dict) -> None:
    """DELIVERY.md へ 1 行追記する（受領一覧。見出しは初回だけ書く）。"""
    path = delivery_index(home)
    header = ("# 納品一覧\n\n"
              "agent-amigos が受け入れた成果物の受領記録。実体は `deliveries/<ミッション ID>/`。\n\n"
              "| 受入日時 | ミッション | タイトル | 状態 | 実行時間 | 納品先 |\n"
              "|---|---|---|---|---|---|\n")
    mins = float(record.get("execution_seconds") or 0.0) / 60.0
    state = "partial" if record.get("partial") else "完全"
    row = (f"| {record['accepted_at']} | {record['mission']} | "
           f"{str(record.get('title') or '').replace('|', '/')} | {state} | "
           f"{mins:.1f}m | `deliveries/{record['mission']}/` |\n")
    exists = os.path.isfile(path)
    with open(path, "a", encoding="utf-8") as f:
        if not exists:
            f.write(header)
        f.write(row)


def export_delivery(mp: MissionPaths, mission: dict, home: str, by: str) -> "dict | None":
    """accept 成立後の搬出。納品書を返す（deliverable が無ければ None）。

    搬出先が既にある場合（同一ミッションの再 accept）は上書きせず作り直す —
    納品棚の中身は常に「受け入れたラウンドの deliverable」と一致させる。"""
    manifest = read_json(mp.manifest())
    if not manifest:
        return None
    dest = delivery_dir(home, mp.mission_id)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    files = _copy_deliverable(mp, dest, manifest)
    record = {
        "mission": mp.mission_id,
        "title": mission.get("title") or "",
        "goal": mission.get("goal") or "",
        "accepted_at": now_iso(),
        "accepted_by": by,
        "acceptance": str(mission.get("acceptance") or "manual"),
        "round": current_round(mp),
        "partial": bool(manifest.get("partial")),
        "partial_reason": str(manifest.get("reason") or ""),
        "execution_seconds": round(budget_spent_seconds(mp), 1),
        "files": files,
    }
    repo = (mission.get("workspace") or {}).get("repo")
    if repo:
        # コードは repo が正本（設計書 §8.3）。納品棚には参照だけを置く。
        record["code"] = {"repo": str(repo),
                          "branch": f"amigos/{mp.mission_id}/integration"}
    write_json_atomic(delivery_json(home, mp.mission_id), record)
    _append_index(home, record)
    skipped = sum(1 for r in files if not r["exported"])
    log("owner", f"{mp.mission_id}: 納品しました → {dest}"
                 f"（{len(files) - skipped} ファイル"
                 f"{f'・{skipped} 件は参照のみ' if skipped else ''}"
                 f"{'・partial' if record['partial'] else ''}）")
    return record


def list_deliveries(home: str) -> list:
    """納品棚の納品書を新しい順に読む（CLI / dashboard の一覧用）。"""
    out = []
    base = deliveries_dir(home)
    try:
        names = sorted(os.listdir(base))
    except FileNotFoundError:
        return out
    for name in names:
        rec = read_json(delivery_json(home, name))
        if isinstance(rec, dict):
            out.append(rec)
    return sorted(out, key=lambda r: str(r.get("accepted_at") or ""), reverse=True)
