#!/usr/bin/env python3
"""
embeddings.py - 埋め込み索引（ollama）。TF-IDF が自信を持てないときだけ使う段。

設計: docs/designs/ltm-use-embedding-recall-design.md
  - 合成しない。TF-IDF の最上位コサインがしきい値未満のときだけ、埋め込みで採点し直す。
  - 索引は <memory_dir>/.memory-embeddings.json。本文のハッシュを鍵に持つので、
    記憶を書き換えれば古い行は自動で外れる（補修対象として数える）。
  - recall は決して失敗しない。ollama が落ちていれば段ごと飛ばし、現行の結果を返す。
  - recall では索引を作らない（検索の待ち時間に索引の費用を混ぜない）。

Python 標準ライブラリのみ。ollama の /api/embed を HTTP で叩く。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import sys
import urllib.error
import urllib.request

import memory_utils

EMBEDDINGS_FILENAME = ".memory-embeddings.json"
EMBED_CHARS = 4000              # 1 記憶あたり埋め込みへ渡す先頭文字数（設計の実測条件と同じ）
QUERY_TIMEOUT_SEC = 15          # recall 側の待ち上限。超えたら段を飛ばす（検索を待たせない）
BUILD_TIMEOUT_SEC = 600
_warned = False                 # 警告はプロセスあたり 1 回


def ollama_host() -> str:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    return host if host.startswith("http") else f"http://{host}"


def get_embeddings_path(memory_dir: str) -> str:
    return os.path.join(memory_dir, EMBEDDINGS_FILENAME)


def load_embeddings(memory_dir: str) -> dict:
    """索引を読む。無ければ空（{"vectors": {}}）。"""
    path = get_embeddings_path(memory_dir)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                store = json.load(f)
            if isinstance(store, dict) and isinstance(store.get("vectors"), dict):
                return store
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "model": "", "built_at": "", "vectors": {}}


def save_embeddings(memory_dir: str, store: dict) -> None:
    os.makedirs(memory_dir, exist_ok=True)
    store["built_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(get_embeddings_path(memory_dir), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)


def enabled(memory_dir: str) -> bool:
    """索引ファイルがあるときだけ段を使う（初回は現行と同じ挙動。build_index --embeddings で作る）。"""
    return os.path.exists(get_embeddings_path(memory_dir))


def embed_texts(texts: list[str], model: str, timeout: float = BUILD_TIMEOUT_SEC) -> list[list[float]]:
    """ollama /api/embed。失敗は OSError 系をそのまま上げる（呼び出し側が段を飛ばす）。"""
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(f"{ollama_host()}/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["embeddings"]


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def memory_text(filepath: str) -> str:
    """埋め込む本文 = ファイル先頭 EMBED_CHARS 文字（frontmatter 込み。設計の測定条件と同じ）。"""
    with open(filepath, encoding="utf-8", errors="replace") as f:
        return f.read()[:EMBED_CHARS]


def dense_cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _status(memory_dir: str, store: dict, entries: list[dict]) -> dict:
    """索引の鮮度。stale = 本文が変わった / missing = 未索引。"""
    fresh = stale = missing = 0
    todo: list[tuple[dict, str, str]] = []     # (entry, text, hash)
    for entry in entries:
        mem_id = entry.get("id") or ""
        if not mem_id or entry.get("status") == "deprecated":
            continue
        fpath = os.path.join(memory_dir, entry.get("filepath", ""))
        try:
            text = memory_text(fpath)
        except OSError:
            continue
        h = text_hash(text)
        row = store["vectors"].get(mem_id)
        if row and row.get("hash") == h:
            fresh += 1
        else:
            stale += 1 if row else 0
            missing += 0 if row else 1
            todo.append((entry, text, h))
    return {"fresh": fresh, "stale": stale, "missing": missing, "todo": todo}


def status(memory_dir: str) -> dict:
    """build_index --stats 用。索引が無ければ enabled=False だけ返す。"""
    if not enabled(memory_dir):
        return {"enabled": False}
    store = load_embeddings(memory_dir)
    index = memory_utils.load_index(memory_dir)
    st = _status(memory_dir, store, index.get("entries", []))
    st.pop("todo", None)
    return {"enabled": True, "model": store.get("model", ""), "built_at": store.get("built_at", ""),
            "indexed": len(store["vectors"]), **st}


def build(memory_dir: str, model: str, batch: int = 32) -> dict:
    """索引を作る / 補修する。新規・stale だけ埋め込む（1 件 0.6 秒見当）。ollama が要る。"""
    store = load_embeddings(memory_dir)
    if store.get("model") and store["model"] != model:
        store = {"version": 1, "model": model, "built_at": "", "vectors": {}}   # モデル替えは作り直し
    store["model"] = model
    index = memory_utils.load_index(memory_dir)
    if not index.get("entries"):
        index = memory_utils.refresh_index(memory_dir)
    st = _status(memory_dir, store, index.get("entries", []))
    todo = st["todo"]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        vectors = embed_texts([t for _, t, _ in chunk], model)
        for (entry, _, h), vec in zip(chunk, vectors):
            store["vectors"][entry["id"]] = {"hash": h, "vec": vec}
    # 消えた記憶の行を落とす
    live = {e.get("id") for e in index.get("entries", [])}
    for mem_id in [m for m in store["vectors"] if m not in live]:
        del store["vectors"][mem_id]
    save_embeddings(memory_dir, store)
    return {"model": model, "embedded": len(todo), "indexed": len(store["vectors"]),
            "fresh": st["fresh"], "stale": st["stale"], "missing": st["missing"]}


def update_entry(memory_dir: str, mem_id: str, filepath: str) -> bool:
    """save 直後の 1 件追加。索引が無ければ何もしない（recall 側の挙動を変えない）。
    ollama が落ちていれば黙って False（次の build_index で補修される）。"""
    if not mem_id or not enabled(memory_dir):
        return False
    store = load_embeddings(memory_dir)
    model = store.get("model") or memory_utils.load_config().get("embedding_model", "")
    if not model:
        return False
    try:
        text = memory_text(filepath)
        vec = embed_texts([text], model, timeout=60)[0]
    except (OSError, urllib.error.URLError, KeyError, IndexError, ValueError):
        return False
    store["model"] = model
    store["vectors"][mem_id] = {"hash": text_hash(text), "vec": vec}
    save_embeddings(memory_dir, store)
    return True


def _warn(msg: str) -> None:
    global _warned
    if not _warned:
        print(f"[ltm-use] 埋め込み段をスキップ: {msg}", file=sys.stderr)
        _warned = True


def query_scores(memory_dir: str, query: str, store: dict | None = None) -> dict[str, float] | None:
    """クエリを埋め込み、索引にある記憶ごとのコサインを返す。使えなければ None（段を飛ばす）。

    recall は決して失敗しない: ollama 停止・モデル未取得・タイムアウトはすべて None。
    索引が無い記憶はここに含まれない（埋め込み側の候補から外れる）。
    """
    store = store if store is not None else load_embeddings(memory_dir)
    model = store.get("model") or ""
    if not model or not store.get("vectors"):
        return None
    try:
        qv = embed_texts([query], model, timeout=QUERY_TIMEOUT_SEC)[0]
    except (OSError, urllib.error.URLError, KeyError, IndexError, ValueError) as e:
        _warn(f"{model} の埋め込みを取得できません（{e}）。TF-IDF の結果を返します")
        return None
    return {mem_id: dense_cosine(qv, row.get("vec") or []) for mem_id, row in store["vectors"].items()}
