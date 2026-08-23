#!/usr/bin/env python3
"""P11（B2）: MoE 候補が物理 RAM に収まるかの実測プローブ。LLM の判定は使わない。

計画 2026-08-22 §4.2 B2 の「RAM が先。収まらなければここで終わり」を、対象マシン
（想定: RAM 32 GB の推論機）で数分で判定するための道具。**モデルの pull はしない**
（16.75 GiB のダウンロードは人が承認してから `ollama pull gemma4:26b`）。

測ること（すべて ollama の API と OS の数字。推測しない）:
  1. モデルの重みサイズ（/api/show）と、ホストの物理 RAM・空き RAM
  2. num_ctx ごとの常駐サイズ（/api/ps の size——重み + KV。ロード直後に読む）
  3. ロード時間・prefill / decode 速度（/api/generate の *_duration / *_count）
  4. 判定: 常駐サイズが「空き RAM − 安全率」を超えたら不成立（スワップは遅いではなく停滞）

使い方（対象マシンで）:
  ollama pull gemma4:26b                      # 人が承認してから
  python3 tools/agent-tools/eval/moe_ram_probe.py --model gemma4:26b
  python3 tools/agent-tools/eval/moe_ram_probe.py --model gemma4:26b --ctx 8192,32768
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not HOST.startswith("http"):
    HOST = f"http://{HOST}"
GIB = 1024 ** 3
# 常駐がこの余白を残せなければ不成立と判定する。OS・ファイルキャッシュ・エージェント側の
# プロセス（git / pytest / CLI）が要る分で、スワップへ落ちてからでは遅い。
SAFETY_GIB = 3.0


def _api(path: str, payload: "dict | None" = None, timeout: float = 1800):
    req = urllib.request.Request(
        f"{HOST}{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def host_memory() -> "tuple[int, int | None]":
    """(物理 RAM, 目安の空き) をバイトで。空きが取れない OS では None。"""
    system = platform.system()
    if system == "Linux":
        info = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = int(rest.split()[0]) * 1024
        return info.get("MemTotal", 0), info.get("MemAvailable")
    if system == "Darwin":
        total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                   text=True).stdout.strip())
        # macOS の「空き」は圧縮とファイルキャッシュで実態が読みにくい。free+inactive を目安に。
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
        page = 16384 if "page size of 16384" in vm else 4096
        pages = {}
        for line in vm.splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            val = val.strip().rstrip(".")
            if val.isdigit():
                pages[key.strip()] = int(val)
        avail = (pages.get("Pages free", 0) + pages.get("Pages inactive", 0)) * page
        return total, avail
    return 0, None


def probe(model: str, ctx: int, prompt: str) -> dict:
    started = time.time()
    r = _api("/api/generate", {"model": model, "prompt": prompt, "stream": False,
                               "keep_alive": "2m",
                               "options": {"num_ctx": ctx, "num_predict": 64}})
    wall = time.time() - started
    resident = {}
    for _ in range(3):        # 他モデルとの入れ替わり直後は載っていないことがある（対象機ではアイドルで実行）
        ps = _api("/api/ps")
        resident = next((m for m in ps.get("models", [])
                         if m.get("name", "").startswith(model)), {})
        if resident:
            break
        time.sleep(1)
    out = {
        "num_ctx": ctx,
        "wall_sec": round(wall, 1),
        "load_sec": round(r.get("load_duration", 0) / 1e9, 1),
        "prompt_tokens": r.get("prompt_eval_count"),
        "prefill_tok_s": round(r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9), 1)
        if r.get("prompt_eval_duration") else None,
        "decode_tok_s": round(r["eval_count"] / (r["eval_duration"] / 1e9), 1)
        if r.get("eval_duration") else None,
        "resident_bytes": resident.get("size"),
        "resident_vram_bytes": resident.get("size_vram"),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True, help="例: gemma4:26b（pull は人が先に承認して行う）")
    ap.add_argument("--ctx", default="8192,32768",
                    help="測る num_ctx のカンマ区切り（KV はここで伸びる）")
    ap.add_argument("--safety-gib", type=float, default=SAFETY_GIB,
                    help=f"残すべき余白 GiB（既定 {SAFETY_GIB}）")
    ap.add_argument("--output", help="機械可読な JSON の保存先")
    args = ap.parse_args()

    try:
        show = _api("/api/show", {"model": args.model})
    except urllib.error.HTTPError as e:
        print(f"モデル {args.model} を読めません（{e}）。pull がまだなら人の承認を得てから "
              f"`ollama pull {args.model}` を実行してください。", file=sys.stderr)
        return 2
    except (urllib.error.URLError, OSError) as e:
        print(f"ollama に接続できません（{HOST}: {e}）。ollama serve を起動してください。",
              file=sys.stderr)
        return 2

    total, avail = host_memory()
    weights = None
    details = show.get("details") or {}
    # /api/show はサイズを返さない版があるので tags からも引く
    try:
        tags = _api("/api/tags")
        weights = next((m.get("size") for m in tags.get("models", [])
                        if m.get("name", "").startswith(args.model)), None)
    except Exception:  # noqa: BLE001
        pass
    print(f"host: {platform.system()} RAM {total / GIB:.1f} GiB"
          + (f" / 空きの目安 {avail / GIB:.1f} GiB" if avail else ""))
    print(f"model: {args.model} 重み {weights / GIB:.2f} GiB" if weights else
          f"model: {args.model}（重みサイズ不明）",
          f"quant={details.get('quantization_level')} params={details.get('parameter_size')}")

    # 短い prompt でロード + 常駐、長い prompt（約 2k token）で prefill 速度の目安
    long_prompt = ("次の文章を 1 行で要約してください。\n" + "課金の日割りと請求合計の話。" * 400)
    report = {"host_total_bytes": total, "host_available_bytes": avail,
              "model": args.model, "weights_bytes": weights, "runs": []}
    verdict_fail = None
    for i, ctx in enumerate(int(c) for c in args.ctx.split(",") if c.strip()):
        prompt = long_prompt if i else "1+1="
        try:
            run = probe(args.model, ctx, prompt)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            print(f"  num_ctx={ctx}: 実行できません（{e}）——RAM 不足でロードに失敗した可能性。"
                  "dmesg / ollama serve のログを確認", file=sys.stderr)
            report["runs"].append({"num_ctx": ctx, "error": str(e)})
            verdict_fail = f"num_ctx={ctx} でロード失敗"
            break
        report["runs"].append(run)
        resident = run.get("resident_bytes") or 0
        line = (f"  num_ctx={ctx}: 常駐 {resident / GIB:.2f} GiB  load {run['load_sec']}s  "
                f"prefill {run['prefill_tok_s']} tok/s  decode {run['decode_tok_s']} tok/s")
        print(line, flush=True)
        if total and resident and resident > total - args.safety_gib * GIB:
            verdict_fail = (f"num_ctx={ctx} の常駐 {resident / GIB:.2f} GiB が "
                            f"物理 {total / GIB:.1f} GiB − 余白 {args.safety_gib} GiB を超える")
            break

    ok = verdict_fail is None
    report["ok"] = ok
    report["verdict"] = "成立（物理 RAM に収まる）" if ok else f"不成立: {verdict_fail}"
    print(f"\n判定: {report['verdict']}")
    if ok:
        print("次: judge_eval / text_eval / worker_eval の既存セルへ --model 差し替えで基準線"
              "（e4b・12b）と並べる（計画 P11 の手順 2。ベンチマーク数値は採用根拠にしない）")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"報告: {args.output}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
