"""フォルダの取得元。フォルダの中のファイルを 1 件ずつのレコードにする。

API の取得元と同じく、1 回の取得はフォルダ全体の一覧（全件取得）で、判定・受け渡しは API の取得元と変わらない。
ファイルの追加は appeared、更新（大きさ・更新時刻・中身のハッシュ）は changed、削除は removed で検知する。

- 書き込みの途中のファイルを拾わない: 更新時刻から settle_sec たっていないファイルは、前回の一覧にあればその時の姿を、
  無ければまだ無いものとして扱う（書き換え中のファイルが「消えた」「現れた」に見えない）
- フォルダが無い・読めないときは一時的な失敗にする（ネットワークのフォルダが切れたときに、全件が「消えた」に見えない）
- ファイル数が max_files を超えたら完了にしない（打ち切りを「全部取れた」と扱わない）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat as statmod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .util import IS_WINDOWS, ConfigError, PermanentError, TransientError, now

CONTENT_TYPES = ("none", "text", "json")
KEY = "rel"                         # フォルダの取得元のレコードを一意に指す項目（フォルダからの相対パス）


@dataclass
class FolderSpec:
    path: Path
    include: list[str] = field(default_factory=lambda: ["*"])
    exclude: list[str] = field(default_factory=list)
    hidden: bool = False            # 名前が . で始まるファイル・フォルダも見る
    settle_sec: float = 5
    hash: bool = False              # 中身の SHA-256 を sha256 に入れる（大きさ・更新時刻が同じままの書き換えも検知する）
    content: str = "none"           # none / text / json: 中身を content に入れる
    max_bytes: int = 1048576        # hash・content で読む上限。超えたら読まずに content_error: too_large
    max_files: int = 10000

    @property
    def recursive(self) -> bool:
        return any("/" in p or "**" in p for p in self.include)


def _glob_re(pattern: str) -> re.Pattern:
    """フォルダからの相対パス（/ 区切り）に当てる glob。* と ? は / をまたがず、** は何階層でもまたぐ。"""
    out = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
            continue
        out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
        i += 1
    return re.compile(out + r"\Z", re.IGNORECASE if IS_WINDOWS else 0)


def parse(d: Any, where: str, base: Path) -> FolderSpec:
    def expect(cond: bool, msg: str) -> None:
        if not cond:
            raise ConfigError(msg)

    expect(isinstance(d, dict) and isinstance(d.get("path"), str) and d["path"].strip() != "",
           f"{where}.path: 見るフォルダのパスを書いてください")
    unknown = sorted(set(d) - {"path", "include", "exclude", "hidden", "settle_sec", "hash", "content", "max_bytes",
                               "max_files"})
    expect(not unknown, f"{where}: 知らない項目です: {', '.join(unknown)}")
    p = Path(os.path.expanduser(os.path.expandvars(d["path"])))
    if not p.is_absolute():
        p = base / p                                        # 相対パスは設定ファイルから
    spec = FolderSpec(path=p)
    for k in ("include", "exclude"):
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            v = [v]
        expect(isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v),
               f"{where}.{k}: glob（例 \"*.csv\"・\"**/*.json\"）の配列を書いてください")
        setattr(spec, k, [x.replace("\\", "/").lstrip("/") for x in v])
    for k in ("hidden", "hash"):
        v = d.get(k, getattr(spec, k))
        expect(isinstance(v, bool), f"{where}.{k}: true か false を書いてください")
        setattr(spec, k, v)
    s = d.get("settle_sec", spec.settle_sec)
    expect(isinstance(s, (int, float)) and not isinstance(s, bool) and s >= 0, f"{where}.settle_sec: 0 以上の数を書いてください")
    spec.settle_sec = float(s)
    spec.content = d.get("content", "none")
    expect(spec.content in CONTENT_TYPES, f"{where}.content: {' / '.join(CONTENT_TYPES)} のどれかです")
    for k in ("max_bytes", "max_files"):
        v = d.get(k, getattr(spec, k))
        expect(isinstance(v, int) and not isinstance(v, bool) and v >= 1, f"{where}.{k}: 1 以上の整数を書いてください")
        setattr(spec, k, v)
    return spec


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _read(spec: FolderSpec, full: str, size: int, rec: dict) -> None:
    if not spec.hash and spec.content == "none":
        return
    if size > spec.max_bytes:
        rec["content_error"] = "too_large"
        return
    try:
        with open(full, "rb") as f:
            raw = f.read(spec.max_bytes + 1)
    except OSError as e:
        rec["content_error"] = f"unreadable: {e.strerror or e}"
        return
    if spec.hash:
        rec["sha256"] = hashlib.sha256(raw).hexdigest()
    if spec.content == "none":
        return
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        rec["content_error"] = "not_utf8"
        return
    if spec.content == "text":
        rec["content"] = text
        return
    try:
        rec["content"] = json.loads(text)
    except ValueError:
        rec["content_error"] = "invalid_json"


def scan(spec: FolderSpec, previous: dict[str, Any]) -> list[dict]:
    """フォルダの中の一覧をレコードの配列にする。previous は前回の一覧（rel → レコード）。"""
    root = spec.path
    try:
        if not root.is_dir():
            raise TransientError(f"フォルダがありません（ネットワークのフォルダなら切れていないか確かめてください）: {root}")
    except OSError as e:
        raise TransientError(f"フォルダを見られません: {root}: {e}") from e
    includes = [_glob_re(p) for p in spec.include]
    excludes = [_glob_re(p) for p in spec.exclude]
    t = now()
    found: list[tuple[str, str]] = []
    errors: list[str] = []

    def walk(d: str, rel_dir: str) -> None:
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except FileNotFoundError:
            if rel_dir:                                         # 見ている間に消えた下のフォルダ
                return
            raise
        except OSError as e:
            errors.append(f"{rel_dir or '.'}: {e.strerror or e}")
            return
        for e in entries:
            if not spec.hidden and e.name.startswith("."):
                continue
            rel = f"{rel_dir}{e.name}"
            try:
                if e.is_dir(follow_symlinks=False):
                    if spec.recursive and not any(x.match(rel + "/") or x.match(rel) for x in excludes):
                        walk(e.path, rel + "/")
                    continue
                if not e.is_file():
                    continue
            except OSError:
                continue
            if any(x.match(rel) for x in includes) and not any(x.match(rel) for x in excludes):
                found.append((rel, e.path))
                if len(found) > spec.max_files:
                    raise PermanentError(f"ファイル数が上限（max_files={spec.max_files}）を超えました。取りこぼさないよう、"
                                         "この取得は完了にしません。上限を上げるか、include / exclude で絞ってください")

    try:
        walk(str(root), "")
    except FileNotFoundError as e:
        raise TransientError(f"フォルダがありません: {root}") from e
    except OSError as e:
        raise TransientError(f"フォルダを見られません: {root}: {e}") from e
    if errors:
        # 読めなかったフォルダの中身を「消えた」にしないよう、この取得は完了にしない
        raise TransientError("読めないフォルダがあるので、この取得は完了にしません: " + "、".join(errors[:5]))

    out: list[dict] = []
    for rel, full in found:
        try:
            st = os.stat(full)
        except FileNotFoundError:
            continue                                            # 一覧を取ってから消えた
        except OSError as e:
            raise TransientError(f"ファイルを見られません: {rel}: {e}") from e
        if not statmod.S_ISREG(st.st_mode):
            continue
        if spec.settle_sec and t - st.st_mtime < spec.settle_sec:
            if rel in previous:                                 # 書き換え中: 前回の姿のまま
                out.append(previous[rel])
            continue                                            # 書き込み中の新しいファイル: まだ無いものとする
        p = Path(full)
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        rec: dict[str, Any] = {
            "rel": rel, "path": str(p), "name": p.name, "stem": p.stem, "ext": p.suffix, "dir": parent,
            "size": st.st_size, "mtime": round(st.st_mtime, 6), "mtime_iso": _iso(st.st_mtime),
        }
        _read(spec, full, st.st_size, rec)
        out.append(rec)
    return out
