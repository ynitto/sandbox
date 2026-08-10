#!/usr/bin/env python3
"""既存ログだけから「その入力長・出力長なら何秒か」を出す（LLM を 1 回も呼ばない）。

`~/.agents/logs/ollama/*.jsonl`（`AGENT_OLLAMA_LOG_DIR` で移せる）を読み、

- 役割別の本番プロンプト長と実測 tokens_in / tokens_out
- mode × think × format 別の壁時計
- 入力長別の TTFT（最初のトークンまでの秒）と decode の実効 tok/s

を表にする。測る前に「その候補は時間内に終わるか」を机上で落とすための道具で、
ハーネス（worker_eval.py / 判定系）の入力長を本番に合わせるのにも使う。

    python3 tools/agent-tools/eval/log_stats.py [ログディレクトリ]
"""
import collections
import json
import os
import pathlib
import sys

# 役割は本番プロンプトの書き出しで決まる（呼び出し側が固定文で始める）
ROLES = [
    ("flow:generate", "分散 Dynamic Workflow の生成役"),
    ("flow:worker", "分散 Dynamic Workflow のワーカー"),
    ("flow:evaluator", "分散 Dynamic Workflow の評価役"),
    ("flow:planner", "分散 Dynamic Workflow の計画役"),
    ("flow:analyst", "分散 Dynamic Workflow の計画アナリスト"),
    ("flow:split", "分散 Dynamic Workflow の分解役"),
    ("flow:verify", "分散 Dynamic Workflow の検証役"),
    ("flow:merge", "分散 Dynamic Workflow の統合役"),
    ("flow:filter", "分散 Dynamic Workflow の選別役"),
    ("flow:adjudicate", '{"decision":"done"|"replan"'),
    ("project:verifier", "成果物の検証エージェント"),
]
SIZES = ("<1k", "1-2k", "2-4k", "4-8k", "8-16k", ">=16k")


def size_of(tokens: int) -> str:
    for edge, name in zip((1000, 2000, 4000, 8000, 16000), SIZES):
        if tokens < edge:
            return name
    return SIZES[-1]


def role_of(prompt: str) -> str:
    head = " ".join(prompt.split())[:400]
    for name, marker in ROLES:
        if marker in head:
            return name
    return "other" if prompt else "other(no-user-msg)"


def read_run(path: pathlib.Path) -> "dict | None":
    """1 ログ = 1 run。llm_start → 最初の llm_progress → llm_end を 1 ラウンドとして畳む。"""
    run = {"name": path.name, "start": None, "prompt": "", "end": None, "rounds": []}
    start = first = None
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("kind")
        if kind == "run_start":
            run["start"] = event
        elif kind == "run_end":
            run["end"] = event
        elif kind == "message" and event.get("role") == "user" and not run["prompt"]:
            run["prompt"] = event.get("content") or ""
        elif kind == "llm_start":
            start, first = event["ts"], None
        elif kind == "llm_progress" and first is None:
            # 進捗は最初のトークン到着で 1 発目が出る（PROGRESS_INTERVAL_SEC より前）ので、
            # これを TTFT の代理にできる。llm_end との差が decode の実時間になる。
            first = event["ts"]
        elif kind == "llm_end":
            run["rounds"].append({
                "tokens_in": int(event.get("tokens_in") or 0),
                "tokens_out": int(event.get("tokens_out") or 0),
                "duration_sec": float(event.get("duration_sec") or 0),
                "ttft": (first - start) if (start and first) else None,
                "decode_sec": (event["ts"] - first) if first else None,
            })
            start = first = None
    return run if run["start"] else None


def q(values, pct):
    values = sorted(values)
    if not values:
        return 0
    return values[min(len(values) - 1, int(round((len(values) - 1) * pct)))]


def main(argv):
    log_dir = pathlib.Path(argv[1]) if len(argv) > 1 else pathlib.Path(
        os.environ.get("AGENT_OLLAMA_LOG_DIR") or (pathlib.Path.home() / ".agents/logs/ollama"))
    runs = [r for r in (read_run(p) for p in sorted(log_dir.glob("*.jsonl"))) if r]
    if not runs:
        print(f"ログがありません: {log_dir}", file=sys.stderr)
        return 1
    print(f"# ログ {len(runs)} 本 ({log_dir})")

    by_role = collections.defaultdict(list)
    for run in runs:
        by_role[role_of(run["prompt"])].append(run)

    print("\n## 役割別 — 本番プロンプトの実寸")
    print(f"{'role':<20}{'n':>4} {'chars p50':>10} {'in p50':>8} {'in max':>8} "
          f"{'out p50':>8} {'run sec p50':>12} {'p90':>7}")
    for role, group in sorted(by_role.items(), key=lambda kv: -q([
            int(r["start"].get("prompt_chars") or 0) for r in kv[1]], .5)):
        chars = [int(r["start"].get("prompt_chars") or 0) for r in group]
        first = [r["rounds"][0] for r in group if r["rounds"]]
        out = [sum(x["tokens_out"] for x in r["rounds"]) for r in group]
        secs = [float((r["end"] or {}).get("duration_sec") or 0) for r in group]
        print(f"{role:<20}{len(group):>4} {q(chars,.5):>10,} "
              f"{q([f['tokens_in'] for f in first],.5):>8,} "
              f"{max([f['tokens_in'] for f in first] or [0]):>8,} "
              f"{q(out,.5):>8,} {q(secs,.5):>12.1f} {q(secs,.9):>7.1f}")

    print("\n## mode × think × format 別 — 壁時計は出力長で決まる")
    by_mode = collections.defaultdict(list)
    for run in runs:
        s = run["start"]
        by_mode[(s.get("mode"), str(s.get("think")), s.get("format") or "-")].append(run)
    print(f"{'mode':<7}{'think':<7}{'format':<7}{'n':>4} {'chars p50':>10} "
          f"{'out p50':>8} {'sec p50':>8} {'sec p90':>8}")
    for key, group in sorted(by_mode.items(), key=lambda kv: -len(kv[1])):
        chars = [int(r["start"].get("prompt_chars") or 0) for r in group]
        out = [sum(x["tokens_out"] for x in r["rounds"]) for r in group]
        secs = [float((r["end"] or {}).get("duration_sec") or 0) for r in group]
        print(f"{key[0]:<7}{key[1]:<7}{key[2]:<7}{len(group):>4} {q(chars,.5):>10,} "
              f"{q(out,.5):>8,} {q(secs,.5):>8.1f} {q(secs,.9):>8.1f}")

    print("\n## 入力長別 — TTFT と decode（机上計算の係数）")
    ttft = collections.defaultdict(list)
    decode = collections.defaultdict(list)
    for run in runs:
        for rnd in run["rounds"]:
            if rnd["tokens_in"] <= 0 or rnd["ttft"] is None:
                continue
            bucket = size_of(rnd["tokens_in"])
            ttft[bucket].append(rnd["ttft"])
            if rnd["decode_sec"] and rnd["decode_sec"] > 0.5 and rnd["tokens_out"] > 5:
                decode[bucket].append(rnd["tokens_out"] / rnd["decode_sec"])
    print(f"{'tokens_in':<10}{'n':>5} {'TTFT p50':>9} {'p90':>7} {'max':>7} "
          f"{'decode p50':>11} {'p10':>7} tok/s")
    for bucket in SIZES:
        if not ttft[bucket]:
            continue
        print(f"{bucket:<10}{len(ttft[bucket]):>5} {q(ttft[bucket],.5):>9.1f} "
              f"{q(ttft[bucket],.9):>7.1f} {max(ttft[bucket]):>7.1f} "
              f"{q(decode[bucket],.5):>11.1f} {q(decode[bucket],.1):>7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
