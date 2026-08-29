#!/usr/bin/env python3
"""記憶検索の recall を測る — 埋め込みは既存の TF-IDF を上回るか。

品質再点検 §6 案 d（別モデル種別）の検証。生成品質の袋小路とは独立に評価できる筋で、
**客観指標（hit@k・MRR）で決着する**のが利点。判定役（LLM）は使わない。

基準線は現物の実装（ltm-use の `similarity.py`・日英混合の TF-IDF）で、候補は ollama の
埋め込みモデル。**基準線を上回らないなら入れない**、という決着の付け方をする。

コーパスは ltm-use の実記憶（`$HOME/.copilot/memory/home`。`LTM_MEMORY_DIR` で移せる）。
クエリは手書きで、正解はファイルパスで固定する（走行中にラベルを作らない）。

使い方: python3 retrieval_eval.py [--model bge-m3] [--k 5] [--tfidf-only]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

MEMORY_DIR = pathlib.Path(os.environ.get(
    "LTM_MEMORY_DIR", str(pathlib.Path.home() / ".copilot/memory/home")))
SIMILARITY = pathlib.Path(os.environ.get(
    "LTM_SIMILARITY", str(pathlib.Path.home() / ".claude/skills/ltm-use/scripts/similarity.py")))
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not HOST.startswith("http"):
    HOST = f"http://{HOST}"

# クエリは 2 通り書く。**lexical** は記憶の用語をそのまま使う思い出し方（現行の検索が
# 想定している形）、**paraphrase** は用語を思い出せずに意味だけで探す形（埋め込みが
# 効くとしたらここ）。同じ正解を両方で引き、どちらの土俵かで差が出るかを見る。
# 正解はコーパス内のパスで固定する。複数該当は全部挙げる（hit@k は 1 件でも入れば当たり）。
QUERIES = [
    ("worktree に base の SHA が無くて verify が exit 2 になる",
     "検証が途中で止まって、原因が手元に無い履歴だったやつ",
     ["procedural/codd-gate-verify-base-commit-procedural.md"]),
    ("スクショだけ貼って直してと頼むと途中で止まる",
     "画像だけ渡した依頼がうまくいかなかった話", ["failure/input-tokens.md"]),
    ("並列でループを回すときの排他の取り方",
     "同時に走らせたときの取り合いをどう防いだか", ["solution/kiro-loop-classic.md"]),
    ("記憶と人格とウィキ、どれにどう保存するか",
     "どの引き出しに何をしまうかの決め",
     ["semantic/ltm-use-wiki-use-persona-use-council-system.md"]),
    ("再計画の履歴を画面でどう見せると決めたか",
     "やり直しの経緯を後から追えるようにした画面の決定", ["design/agent-dashboard-001.md"]),
    ("チャットを起動するボタンをどこに置いたか",
     "対話を始める入口を画面のどこに用意したか", ["design/agent-dashboard-cli.md"]),
    ("新機能より先に session 管理や排他を固める判断",
     "土台を先に固めることにした判断", ["workflow/harness-engineering-episodic.md"]),
    ("外部から取ってきたページの取得成功をどう判定するか",
     "取ってきたつもりで中身が空だった件の教訓", ["workflow/procedural.md"]),
    ("空のファイルが .gitkeep とぶつかる",
     "中身が無いものが置き場の目印と衝突した", ["general/wiki-use-gitkeep.md"]),
    ("行頭のラベルを剥がす処理が None を返す条件",
     "先頭の見出しらしき部分を落とす処理が空を返す",
     ["procedural/first-command-line-procedural.md"]),
    ("dict | None の型注釈が古い Python で動かない",
     "新しい書き方の注釈が古い環境で落ちる",
     ["copilot-memory/ltm-use-python-compat-2026-04-04.md",
      "copilot-memory/ltm-use-python-compat-2026-04-04-001.md"]),
    ("特許の先行技術を調べたときの手順", "似た発明が既にないか調べる段取り",
     ["copilot-memory/patent-search-workflow-2026-04-04.md"]),
    ("タスクの粒度をどう決めるか、スコープの上限",
     "どこまでを 1 つの仕事にするかの決め方", ["design/flow-planner.md"]),
    ("README の件数表記と実ファイル数が合っているか確かめる",
     "説明書に書いた数と実物の数の突き合わせ",
     ["procedural/readme-verify-procedural-001.md", "procedural/readme-verify-procedural.md"]),
    ("合議のスキルが設計どおり動くか自分で評価した",
     "話し合いの仕組みを自分自身で試した記録", ["episodic/council-system-2026-04-30.md"]),
    ("MR を作るまでのワーカーの作業ツリーの流れ",
     "変更を取り込む依頼を出すまでの作業場所の扱い",
     ["copilot-memory/gitlab-idd-worker-worktree-mr-flow-2026-04-11.md"]),
    ("定常業務のパラメータを人に入力させるダイアログ",
     "決まりきった作業の設定値を人に聞く画面", ["design/agent-dashboard-002.md"]),
    ("ホームのスキルをまとめて最新化した記録",
     "手元の道具一式をまとめて新しくした日", ["general/2026-04-12.md"]),
    ("段と実行手法の設定をどの画面へまとめたか",
     "実行の段取りと手法の設定を 1 か所へ寄せた", ["design/agent-dashboard-ui-002.md"]),
    ("Wiki のページで出典の書き方が崩れてリンク切れになる",
     "出典の書き方が原因で警告が大量に出た", ["workflow/wiki-source-lint-episodic.md"]),
]


STYLES = ("lexical", "paraphrase")


def load_queries(path):
    """クエリ集合を JSON から読む（既定は組み込み）。返すのは `(訊き方の名前, 行)`。

    コーパスが替われば正解も替わるので、掃引のたびにスクリプトを書き換えない口にする。
    訊き方は 2 通りに固定しない——`styles` を宣言すれば 3 通り以上も測れる（行は
    `[<styles と同数のクエリ>..., [gold, ...]]`）。同じ「用語で探す」でも、題名をなぞる
    長い訊き方と数語だけの短い訊き方では TF-IDF の出方がまるで違う。"""
    if not path:
        return STYLES, [({"lexical": a, "paraphrase": b}, golds) for a, b, golds in QUERIES]
    data = json.loads(pathlib.Path(path).expanduser().read_text(encoding="utf-8"))
    rows = data.get("queries") if isinstance(data, dict) else data
    styles = tuple(data.get("styles") or STYLES) if isinstance(data, dict) else STYLES
    out = []
    for row in rows:
        *texts, golds = row
        if len(texts) != len(styles):
            raise SystemExit(f"クエリ行の要素数が styles={styles} と合いません: {row[:1]}")
        out.append((dict(zip(styles, (str(t) for t in texts))),
                    [str(g) for g in golds]))
    return styles, out


def load_corpus(distractors: bool = True) -> "list[tuple[str, str]]":
    """(相対パス, 本文) の一覧。正解は ltm の記憶にだけ置く。

    リポジトリの設計書・計画書を**妨害文書**として混ぜられるようにしてある。記憶 64 件
    だけだと語彙が重ならず、素朴なキーワード検索でも当たってしまい、腕の差が出ない
    ——「小さいコーパスで測って良いと結論する」のがここでの落とし穴。
    """
    docs = []
    for path in sorted(MEMORY_DIR.rglob("*.md")):
        docs.append((str(path.relative_to(MEMORY_DIR)),
                     path.read_text(encoding="utf-8", errors="replace")))
    if distractors:
        repo = pathlib.Path(__file__).resolve().parents[3]
        for sub in ("docs/plans", "docs/designs", "docs/guides"):
            for path in sorted((repo / sub).glob("*.md")):
                docs.append((f"[distractor] {sub}/{path.name}",
                             path.read_text(encoding="utf-8", errors="replace")))
    return docs


# ------------------------------------------------------------------ 検索の腕


def tfidf_ranker(docs):
    """基準線 — ltm-use の `similarity.py` をそのまま使う（写さない）。"""
    sys.path.insert(0, str(SIMILARITY.parent))
    import similarity  # noqa: PLC0415

    tokenized = [similarity.tokenize(text) for _, text in docs]
    df: dict[str, int] = {}
    for tokens in tokenized:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    idf = similarity.compute_idf(df, len(docs))
    vectors = [similarity.compute_tfidf_vector(t, idf) for t in tokenized]

    def rank(query: str):
        qv = similarity.compute_tfidf_vector(similarity.tokenize(query), idf)
        scores = [(similarity.cosine_similarity(qv, v), docs[i][0]) for i, v in enumerate(vectors)]
        return [name for _, name in sorted(scores, key=lambda s: -s[0])]

    def top_score(query: str) -> float:
        qv = similarity.compute_tfidf_vector(similarity.tokenize(query), idf)
        return max((similarity.cosine_similarity(qv, v) for v in vectors), default=0.0)

    rank.top_score = top_score      # 段構えの判定材料（最上位コサイン）
    return rank


def embed(model: str, texts: "list[str]") -> "list[list[float]]":
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(f"{HOST}/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())["embeddings"]


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def embed_ranker(docs, model: str, chars: int, cache: "pathlib.Path | None" = None):
    """候補 — ollama の埋め込みモデル。長文は先頭 `chars` 文字で切る。

    索引は使い回す（同じコーパスを測り直すたびに数分待たない）。鍵は本文そのものなので、
    記憶を書き換えたら自動で作り直される。
    """
    inputs = [text[:chars] for _, text in docs]
    store = json.loads(cache.read_text(encoding="utf-8")) if (cache and cache.exists()) else {}
    store = store if store.get("model") == model else {"model": model, "vectors": {}}
    known = store["vectors"]
    todo = [t for t in inputs if t not in known]
    started = time.time()
    if todo:
        for i in range(0, len(todo), 32):
            batch = todo[i:i + 32]
            for text, vec in zip(batch, embed(model, batch)):
                known[text] = vec
        print(f"  索引 {len(todo)}/{len(docs)} 件を {time.time() - started:.1f}s で埋め込み"
              f"（{len(next(iter(known.values())))} 次元）", flush=True)
        if cache:
            cache.write_text(json.dumps(store), encoding="utf-8")
    else:
        print(f"  索引 {len(docs)} 件はキャッシュ済み（{cache}）", flush=True)
    vectors = [known[t] for t in inputs]

    def rank(query: str):
        qv = embed(model, [query])[0]
        scores = [(cosine(qv, v), docs[i][0]) for i, v in enumerate(vectors)]
        return [name for _, name in sorted(scores, key=lambda s: -s[0])]

    return rank


def cascade_ranker(tfidf, emb, threshold: float):
    """段構え（設計 ltm-use-embedding-recall-design）— 合成せず、TF-IDF の最上位コサインが
    しきい値未満のときだけ埋め込みへ落とす。本番 `recall_memory.search_with_index` と同じ規則。"""
    def rank(query: str):
        return emb(query) if tfidf.top_score(query) < threshold else tfidf(query)

    return rank


def production_ranker(memory_dir: str, threshold: float, limit: int = 30):
    """**本番経路そのもの** — `recall_memory.search_with_index` を呼ぶ（写さない）。

    ハーネスの TF-IDF は本文全文で作るが、本番のコーパスは title / summary / tags だけで
    作る。最上位コサインの出方が違うので、しきい値はこちらで測らないと決められない
    （設計 ltm-use-embedding-recall-design の「未決」1 点目）。
    """
    sys.path.insert(0, str(SIMILARITY.parent))
    import memory_utils  # noqa: PLC0415
    import recall_memory  # noqa: PLC0415

    base = memory_utils.load_config()
    memory_utils.load_config = lambda: {**base, "embedding_threshold": threshold}
    memory_utils.refresh_index(memory_dir)

    def rank(query: str):
        results = recall_memory.search_with_index(
            memory_dir, query.split(), None, limit)
        rank.fired += bool(results) and results[0].get("ranker") == "embedding"
        return [r["entry"]["filepath"] for r in results]

    rank.fired = 0
    return rank


def hybrid_ranker(rankers, weights=(1.0, 1.0), kk: int = 60):
    """RRF（順位の逆数で足す）。スコアの尺度が違う腕を混ぜる定番で、正規化が要らない。"""
    def rank(query: str):
        fused: dict[str, float] = {}
        for r, w in zip(rankers, weights):
            for pos, name in enumerate(r(query)):
                fused[name] = fused.get(name, 0.0) + w / (kk + pos + 1)
        return [name for name, _ in sorted(fused.items(), key=lambda s: -s[1])]

    return rank


# ------------------------------------------------------------------ 採点


def score(rank, k: int, style: str, queries) -> dict:
    hit1 = hitk = 0
    rr = 0.0
    misses = []
    latencies = []
    for texts, golds in queries:
        query = texts[style]
        t0 = time.time()
        ranked = rank(query)
        latencies.append(time.time() - t0)
        positions = [ranked.index(g) for g in golds if g in ranked]
        best = min(positions) if positions else None
        if best == 0:
            hit1 += 1
        if best is not None and best < k:
            hitk += 1
        else:
            misses.append((query, ranked[0], (best + 1) if best is not None else None))
        rr += 1 / (best + 1) if best is not None else 0.0
    n = len(queries)
    return {"hit@1": hit1 / n, f"hit@{k}": hitk / n, "MRR": rr / n,
            "query_sec_p50": sorted(latencies)[n // 2], "misses": misses}


def run_production(args, styles, queries, thresholds) -> int:
    """本番経路でしきい値を掃引する。精度と**発火率**を同時に出す——段構えは
    「弱い問いだけ埋め込みへ落とす」形なので、全問い発火なら実質は埋め込み単独である。"""
    memory_dir = str(MEMORY_DIR)
    missing = sorted({g for _, golds in queries for g in golds}
                     - {str(p.relative_to(MEMORY_DIR)) for p in MEMORY_DIR.rglob("*.md")})
    if missing:
        print(f"正解に指定したファイルが記憶にありません: {missing}", file=sys.stderr)
        return 1
    print(f"本番経路（recall_memory.search_with_index） / 記憶 "
          f"{len(list(MEMORY_DIR.rglob('*.md')))} 件 / クエリ {len(queries)} 件 × "
          f"{len(styles)} 通り"
          f"（{memory_dir}）\n")

    report = {"mode": "production", "memory_dir": memory_dir, "k": args.k,
              "queries": len(queries), "styles": {}}
    for style in styles:
        print(f"\n########## 訊き方: {style}")
        print(f"{'しきい値':>10} | {'hit@1':>6} {'hit@' + str(args.k):>6} {'MRR':>6} | 発火")
        rows = {}
        for threshold in thresholds:
            rank = production_ranker(memory_dir, threshold)
            r = score(rank, args.k, style, queries)
            r["fired"] = rank.fired
            rows[f"{threshold:g}"] = {k: v for k, v in r.items() if k != "misses"}
            print(f"{threshold:>10g} | {r['hit@1']:>5.0%} {r[f'hit@{args.k}']:>6.0%} "
                  f"{r['MRR']:>6.3f} | {rank.fired}/{len(queries)}")
        report["styles"][style] = rows
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(f"\n結果: {args.output}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-m3", help="ollama の埋め込みモデル")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--chars", type=int, default=4000,
                    help="1 文書あたり埋め込みへ渡す最大文字数")
    ap.add_argument("--tfidf-only", action="store_true",
                    help="埋め込みを取得せず基準線だけ出す（モデル未取得でも動く）")
    ap.add_argument("--no-distractors", action="store_true",
                    help="記憶だけを対象にする（妨害文書を混ぜない）")
    ap.add_argument("--rrf-weight", type=float, default=1.0,
                    help="RRF で埋め込み側に掛ける重み（1.0 = 対等）")
    ap.add_argument("--cascade-threshold", default="0.11",
                    help="段構えのしきい値（TF-IDF 最上位コサインがこれ未満なら埋め込み）。"
                         "--production ではカンマ区切りで掃引できる（inf = 埋め込み単独）")
    ap.add_argument("--queries", help="クエリ集合の JSON（既定は組み込み）")
    ap.add_argument("--production", action="store_true",
                    help="ハーネスのコーパスを作らず、本番の recall 経路を直接測る")
    ap.add_argument("--cache", default="/tmp/agent-retrieval-eval-index.json",
                    help="埋め込み索引のキャッシュ先")
    ap.add_argument("--output", type=pathlib.Path,
                    help="全 arm の機械可読な指標を JSON でも保存する")
    args = ap.parse_args()

    styles, queries = load_queries(args.queries)
    thresholds = [float(t) for t in str(args.cascade_threshold).split(",")]

    if args.production:
        return run_production(args, styles, queries, thresholds)

    docs = load_corpus(distractors=not args.no_distractors)
    if not docs:
        print(f"コーパスが空です: {MEMORY_DIR}", file=sys.stderr)
        return 1
    gold_missing = sorted({g for _, golds in queries for g in golds}
                          - {name for name, _ in docs})
    if gold_missing:
        print(f"正解に指定したファイルがコーパスにありません: {gold_missing}", file=sys.stderr)
        return 1
    memories = sum(1 for name, _ in docs if not name.startswith("[distractor]"))
    print(f"コーパス {len(docs)} 件（記憶 {memories} + 妨害 {len(docs) - memories}） / "
          f"クエリ {len(queries)} 件 × {len(styles)} 通りの訊き方 （{MEMORY_DIR}）\n")

    arms = [("TF-IDF（現行 similarity.py）", tfidf_ranker(docs))]
    if not args.tfidf_only:
        try:
            emb = embed_ranker(docs, args.model, args.chars, pathlib.Path(args.cache))
        except (urllib.error.URLError, OSError, KeyError) as e:
            print(f"埋め込みを取得できません（{e}）。基準線だけ出します。", file=sys.stderr)
        else:
            arms.append((f"埋め込み（{args.model}）", emb))
            arms.append((f"RRF 併用（TF-IDF 1 : {args.model} {args.rrf_weight:g}）",
                         hybrid_ranker([arms[0][1], emb], weights=(1.0, args.rrf_weight))))
            arms.append((f"段構え（TF-IDF 最上位 < {thresholds[0]:g} で {args.model}）",
                         cascade_ranker(arms[0][1], emb, thresholds[0])))

    report = {"model": args.model, "k": args.k, "chars": args.chars,
              "documents": len(docs), "memories": memories, "styles": {}}
    for style in styles:
        print(f"\n########## 訊き方: {style}"
              + ("（記憶の用語をそのまま使う）" if style == "lexical"
                 else "（用語を思い出せず意味だけで探す）"))
        results = []
        for label, rank in arms:
            r = score(rank, args.k, style, queries)
            results.append((label, r))
            print(f"\n=== {label}")
            print(f"  hit@1 {r['hit@1']:.0%}  hit@{args.k} {r[f'hit@{args.k}']:.0%}  "
                  f"MRR {r['MRR']:.3f}  クエリ {r['query_sec_p50'] * 1000:.0f} ms")
            for query, top, pos in r["misses"]:
                print(f"  miss: 「{query[:34]}」 → 1 位 {top}"
                      + (f"（正解は {pos} 位）" if pos else "（正解は圏外）"))
        report["styles"][style] = {label: metrics for label, metrics in results}
        if len(results) > 1:
            base = results[0][1]
            print("\n  --- 基準線との差")
            for label, r in results[1:]:
                print(f"  {label}: hit@{args.k} "
                      f"{r[f'hit@{args.k}'] - base[f'hit@{args.k}']:+.0%} / "
                      f"MRR {r['MRR'] - base['MRR']:+.3f}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(f"\n結果: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
