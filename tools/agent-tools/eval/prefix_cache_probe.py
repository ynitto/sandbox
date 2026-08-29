#!/usr/bin/env python3
"""接頭辞キャッシュが効くかを、スケジューラを触る前に確かめる（計画 2026-08-22 案 3 の前提）。

案 3（同役割の直列バッチ化）は「役割が交互に来ると system 接頭辞が毎回入れ替わり、
固定 policy や役割骨格の prefill を毎回払い直す。同役割で束ねれば接頭辞一致の連続が増える」
という**前提**に立っている。前提が成り立たなければスケジューラを触る意味は無いので、
消化順を実装する前にここで測る。

**測るのは 1 つだけ**——同じ接頭辞を連続で送ると prefill が安くなるか。
判定材料は ollama が返す `prompt_eval_count`（実際に評価したプロンプトトークン数）と
`prompt_eval_duration`。キャッシュに当たれば評価済みトークンは数から落ちる。

    python3 tools/agent-tools/eval/prefix_cache_probe.py --model gemma4:e4b

腕は 2 つ。**同じ本数・同じ接頭辞集合・同じ本文**で、消化順だけが違う。

    batched      A A A B B B     … 案 3 が作ろうとしている順
    interleaved  A B A B A B     … 役割が交互に来る現状

LLM の判断は使わない。生成は `num_predict=1` に切る——見たいのは prefill だけで、
decode を混ぜると腕の差が decode の揺れに埋もれる。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_API = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434")
# 役割ごとの system 接頭辞。**長さが要る**——数十トークンでは prefill が測定誤差に埋もれる。
# 本番の役割骨格 + reliability policy はおよそ数 KB なので、その帯に合わせる。
PREFIX_UNIT = (
    "あなたは分散 Dynamic Workflow の{role}です。次の規約に従ってください。"
    "出力は指定された形式のみ。前置きも後書きも書かない。"
    "根拠のない断定をしない。与えられた材料の外を推測しない。"
    "作業は 1 手ずつ進め、1 回の応答で 1 つの操作だけを行う。"
)


def build_prefix(role: str, repeat: int) -> str:
    """役割ごとに**異なる**長い接頭辞。役割名を全文に散らすので、頭から不一致になる。"""
    return "\n".join(PREFIX_UNIT.format(role=role) for _ in range(repeat))


def chat_once(api: str, model: str, system: str, user: str, timeout: float) -> dict:
    """1 回だけ叩いて、ollama が返した実測値を取り出す（生成は 1 トークンで切る）。"""
    payload = {
        "model": model, "stream": False, "think": False,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "options": {"num_predict": 1, "temperature": 0},
    }
    request = urllib.request.Request(
        f"{api.rstrip('/')}/api/chat", method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {
        "wall_sec": round(time.time() - started, 3),
        "prompt_eval_count": int(body.get("prompt_eval_count") or 0),
        "prompt_eval_sec": round(int(body.get("prompt_eval_duration") or 0) / 1e9, 3),
        "load_sec": round(int(body.get("load_duration") or 0) / 1e9, 3),
    }


def run_arm(api: str, model: str, order: "list[str]", prefixes: dict,
            timeout: float) -> "list[dict]":
    rows = []
    for index, role in enumerate(order, 1):
        # 本文は毎回変える（同一本文だと応答そのものがキャッシュされうる。測りたいのは
        # **接頭辞**の再利用であって、応答の再利用ではない）。
        row = chat_once(api, model, prefixes[role], f"{index} 番目の依頼です。OK とだけ返す。",
                        timeout)
        rows.append({"seq": index, "role": role, **row})
    return rows


def summarize(name: str, rows: "list[dict]") -> dict:
    prefill = [r["prompt_eval_sec"] for r in rows]
    counts = [r["prompt_eval_count"] for r in rows]
    return {"arm": name, "runs": len(rows),
            "prefill_total_sec": round(sum(prefill), 2),
            "prefill_median_sec": round(statistics.median(prefill), 3),
            "prompt_eval_count_first": counts[0] if counts else 0,
            "prompt_eval_count_min": min(counts) if counts else 0,
            "prompt_eval_count_max": max(counts) if counts else 0,
            "wall_total_sec": round(sum(r["wall_sec"] for r in rows), 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--per-role", type=int, default=3, help="役割あたりの本数")
    ap.add_argument("--prefix-repeat", type=int, default=40,
                    help="接頭辞の反復数（長さのつまみ。既定で数千トークン級）")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    roles = ("ワーカー", "評価役")
    prefixes = {role: build_prefix(role, args.prefix_repeat) for role in roles}
    batched = [role for role in roles for _ in range(args.per_role)]
    interleaved = [roles[i % 2] for i in range(args.per_role * 2)]

    print(f"model={args.model} api={args.api} 役割={len(roles)} "
          f"本数/役割={args.per_role} 接頭辞={len(prefixes[roles[0]])} 文字", flush=True)
    # モデルのロードと**全接頭辞の初回 prefill** を腕の外へ出す。
    # 初版は 1 つしか温めておらず、batched の中で「もう一方の初回 prefill」（実測 13.9 秒）を
    # 払っていた——腕の差ではなく初回コストの置き場所を測っていたことになる。
    # 初回は 40 倍以上高い（13.9s 対 0.33s）ので、これが窓の中に入るかどうかで結論が反転する。
    for role in roles:
        warm = chat_once(args.api, args.model, prefixes[role], "準備", args.timeout)
        print(f"warmup[{role}]: load={warm['load_sec']}s prefill={warm['prompt_eval_sec']}s",
              flush=True)

    # 腕は 2 巡し、順序を入れ替える（batched → interleaved → interleaved → batched）。
    # 1 巡だけだと、後の腕がキャッシュの状態を前の腕から受け継いだ分を腕の差として読む。
    results = []
    plan = (("batched", batched), ("interleaved", interleaved),
            ("interleaved", interleaved), ("batched", batched))
    collected: "dict[str, list]" = {"batched": [], "interleaved": []}
    for name, order in plan:
        collected[name] += run_arm(args.api, args.model, order, prefixes, args.timeout)
    for name in ("batched", "interleaved"):
        rows = collected[name]
        order = batched if name == "batched" else interleaved
        summary = summarize(name, rows)
        results.append({**summary, "rows": rows})
        print(f"\n{name}: 順={' '.join(r[0] for r in order)}", flush=True)
        for row in rows:
            print(f"  #{row['seq']} {row['role']}: prefill {row['prompt_eval_sec']:6.3f}s "
                  f"（評価 {row['prompt_eval_count']} tok）壁時計 {row['wall_sec']:6.3f}s",
                  flush=True)
        print(f"  合計 prefill {summary['prefill_total_sec']}s / "
              f"中央値 {summary['prefill_median_sec']}s", flush=True)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return
    a, b = results[0], results[1]
    gain = b["prefill_total_sec"] - a["prefill_total_sec"]
    print(f"\n=== 判定\n  batched の prefill 合計 {a['prefill_total_sec']}s / "
          f"interleaved {b['prefill_total_sec']}s（差 {gain:+.2f}s）")
    # 判定は**時間**で行う。`prompt_eval_count` は当たっても落ちない（実測: 全リクエストで
    # 2904 のまま）ので、キャッシュの有無を数で判定してはいけない——初版はここを間違えて
    # 「痕跡なし」と出していた。落ちるのは `prompt_eval_duration` のほうである。
    ratio = (b["prefill_median_sec"] / a["prefill_median_sec"]
             if a["prefill_median_sec"] else 0.0)
    print(f"  中央値 batched {a['prefill_median_sec']}s / "
          f"interleaved {b['prefill_median_sec']}s（{ratio:.1f} 倍）")
    if ratio >= 2.0:
        print("  → 同じ接頭辞を連続で送ると prefill が落ちる。消化順を役割で束ねる案"
              "（08-22 案 3）の前提は成り立つ。**ただし採否は 1 呼び出しの重さ次第**"
              "——節約は接頭辞ぶんの秒数で固定なので、1 周が数百秒のコード仕事では誤差、"
              "1 呼び出しが十数秒の判定・抽出系では効く。")
    else:
        print("  → 順序を変えても prefill が変わらない。この環境では案 3 の前提が"
              "成り立たない（消化順を触る意味は無い）。")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        print(f"ollama へ届きません: {exc}", file=sys.stderr)
        raise SystemExit(2)
