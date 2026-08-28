#!/usr/bin/env python3
"""評価 archive → `agent-recommendation`（読み取り専用のおすすめ構成）。

**なぜ要るか。** ローカル主体で回すために人が踏む手順は 8 つ・3 面にまたがり、うち
3 つは GUI に口が無い。そのうち 6 つは**端末に依存しない定数**で、この archive から
機械的に決まる。人が本当に決めるのは「どのクラウド CLI を持っているか」だけである。

**新しい機構は足さない。** 適格性の生成は :mod:`qualification_seed` の 1 実装のままで、
ここはその出力へ「実行レベルの構成・実行方針・同時実行数・前提条件・根拠」を添えて
1 個のデータ資産にするだけである。制御面（profiles / control / qualifications）へは
**書かない**——配るのは読み取り専用の推奨までで、適用は管理面（agent-dashboard）と
agent-audit が行う。

**実行レベルの候補は `herd` の 1 語。** `aider` / `ollama` のどれを、
どのモデルで使うかは用途ごとに違い、それを知っているのは実測である。人に 1 つ書かせると
どれかの用途で必ず外れるので、推奨も具体名を書かない。クラウドだけは実測できないので
**枠（slot）**として宣言し、値は適用時に人が選ぶ。

出力は決定的である（同じ archive・revision・生成時刻なら同じ JSON）。

    python3 tools/agent-tools/eval/recommend.py --output ~/.agents/recommendation.json
    python3 tools/agent-tools/eval/recommend.py --print-diff --control-dir ~/.agents/control

正典: schemas/agent-recommendation.schema.json
設計: docs/plans/2026-08-26-agent-tools-recommended-setup-simplification-design.md §3.2 / §3.3
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import qualification_seed

_REPO = Path(__file__).resolve().parents[3]
_AGENTCORE = _REPO / "tools" / "agent-tools" / "agentcore"
if str(_AGENTCORE) not in sys.path:
    sys.path.insert(0, str(_AGENTCORE))

# 一族の入口。`agents/<name>.json` の command[0] がこれなら herd 一族である
# （agent-herd 仕様 §1: クラウド CLI はこの入口を通らない）。
HERD_ENTRYPOINT = "agent-herd"
HERD = "herd"

# 実行レベルの標準語彙（agent-profiles 契約）。UI 表示は 単純作業 / 軽量 / 標準 / 高性能。
TIER_ORDER = ("basic", "small", "medium", "large")
TIER_LABELS = {"basic": "単純作業", "small": "軽量", "medium": "標準", "large": "高性能"}

# クラウド枠。実測が無いので値を持たず、適用時に人が選ぶ。
CLOUD_SLOTS = {
    "medium": "cloud-standard",
    "large": "cloud-premium",
}

# 根拠に添える台帳（qualification_seed が読むもの。パスは同モジュールの正典を使う）。
_LEDGERS = {
    "aider": str(qualification_seed.CODE_E4B_LEDGER),
    "ollama": "worker/ledger-2026-08-14-text-eval-{model}.jsonl",
}


def _stamp(value: dt.datetime) -> str:
    return qualification_seed._stamp(value)  # 時刻の綴りは 1 実装のまま


def herd_members(project_dir=None) -> "list[str]":
    """この木で解決できる herd 一族の定義名。

    綴りで判定せず、定義の `command[0]` を見る——一族に足された定義（ユーザー定義等）が
    自動で入り、クラウド CLI は自動で外れる。
    """
    from agentcore import agentcli

    names: list[str] = []
    for directory in agentcli.plugin_dirs(project_dir):
        try:
            entries = sorted(Path(directory).glob("*.json"))
        except OSError:
            continue
        for path in entries:
            name = path.stem
            if name in names:
                continue
            try:
                spec = agentcli.load_cli(name, project_dir)
            except Exception:
                continue
            command = spec.get("command") or []
            if command and str(command[0]).strip() == HERD_ENTRYPOINT:
                names.append(name)
    return sorted(names)


def _herd_expansion(seed: dict, members: "list[str]") -> "list[dict]":
    """推奨の `herd` が、この archive の実測でどこまで広がるか（画面の説明用）。

    **選択の順位ではない。** 順位は用途ごとに Compiler が決めるので、ここは
    「一族のうち実測を持つ (agent_cli, model) の一覧」に留める。
    """
    allowed = set(members)
    rows = []
    for candidate in seed.get("candidates") or []:
        cli = str(candidate.get("agent_cli") or "")
        model = str(candidate.get("model") or "")
        if cli not in allowed or not model:
            continue
        usable = sorted(
            operation for operation, qualification in (candidate.get("qualifications") or {}).items()
            if isinstance(qualification, dict) and qualification.get("status") in ("qualified", "trial")
        )
        rows.append({
            "agent_cli": cli,
            "model": model,
            "qualified_for": usable,
            # 裏付けが 1 つも無い候補も残す——「入っているのに使われない」理由が
            # 画面で分かるようにする（黙って消すと端末差の切り分けができない）。
            "usable": bool(usable),
        })
    return rows


def _required_models(expansion: "list[dict]") -> "list[str]":
    return sorted({row["model"] for row in expansion})


def _evidence(seed: dict, members: "list[str]") -> "list[dict]":
    """1 行ずつの根拠。画面にそのまま出せる粒度にする。"""
    allowed = set(members)
    rows = []
    for candidate in seed.get("candidates") or []:
        cli = str(candidate.get("agent_cli") or "")
        model = str(candidate.get("model") or "")
        scope = "herd" if cli in allowed else "cloud"
        for operation, qualification in sorted((candidate.get("qualifications") or {}).items()):
            if not isinstance(qualification, dict):
                continue
            samples = qualification.get("samples") or 0
            passed = qualification.get("passed") or 0
            rows.append({
                "scope": scope,
                "agent_cli": cli,
                "model": model,
                "operation_class": operation,
                "status": qualification.get("status"),
                "why": f"{passed}/{samples}",
                "qualification_id": qualification.get("qualification_id"),
                "ledger": _LEDGERS.get(cli, "").format(model=model.replace(":", "-")) or None,
            })
    return rows


def build_recommendation(archive: Path, *, generated_at: dt.datetime, revision: int = 1,
                         project_dir=None) -> dict:
    archive = Path(archive)
    generated_at = qualification_seed._utc(generated_at)
    seed = qualification_seed.build_seed(archive, generated_at=generated_at, revision=revision)
    members = herd_members(project_dir)
    expansion = _herd_expansion(seed, members)

    tiers = {}
    for index, tier in enumerate(TIER_ORDER):
        entry = {"order": index, "label": TIER_LABELS[tier]}
        if tier in CLOUD_SLOTS:
            # 実測できない面は値を持たず枠だけ宣言する。人が決めるのはここだけ。
            entry["slots"] = [{"requires": CLOUD_SLOTS[tier]}]
            entry["candidates"] = []
        else:
            entry["candidates"] = [{"agent_cli": HERD}]
        tiers[tier] = entry

    return {
        "version": 1,
        "revision": int(revision),
        "generated_at": _stamp(generated_at),
        "source": {
            "kind": "eval-archive",
            "archive": archive.name,
            "qualification_revision": int(seed.get("revision") or revision),
        },
        "tiers": tiers,
        # 「おまかせ」のまま。ローカル主体の実体は方針プリセットではなく tiers 構成が作る
        # （2026-08-23 提案 §2.2: 節約にすると昇格受けが tier 検査で常に落ちる）。
        "execution_policy": {"mode": "auto"},
        # ローカル LLM は resource_group=local-llm 同時 1 が前提。herd 一族に実測が
        # 1 つも無い端末（＝クラウドだけ）では宣言しない。
        "control": ({"workloads": {"flow": {"concurrency": {"max_runs": 1, "workers": 1}}}}
                    if any(row["usable"] for row in expansion) else {}),
        "qualifications": seed,
        "herd": {"members": members, "expansion": expansion},
        "requires": {
            "entrypoint": HERD_ENTRYPOINT,
            "models": _required_models([row for row in expansion if row["usable"]]),
        },
        "evidence": _evidence(seed, members),
    }


# --- 差分（--print-diff）------------------------------------------------------


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _candidate_text(candidates) -> str:
    if not candidates:
        return "（未設定）"
    return " / ".join(
        f"{c.get('agent_cli') or '?'}{':' + c['model'] if c.get('model') else ''}"
        for c in candidates if isinstance(c, dict))


def diff_lines(recommendation: dict, control_dir: Path) -> "list[str]":
    """現在 → 推奨 の差分を人が読める行で返す（書き込みはしない）。"""
    control_dir = Path(control_dir)
    profiles = _read_json(control_dir / "profiles.json") or {}
    control = _read_json(control_dir / "control.json") or {}
    qualifications = _read_json(control_dir / "qualifications.json")

    lines = []
    current_tiers = profiles.get("tiers") or {}
    for tier in TIER_ORDER:
        want = recommendation["tiers"][tier]
        have = current_tiers.get(tier) or {}
        have_text = _candidate_text(have.get("candidates"))
        slots = want.get("slots") or []
        if slots:
            # 枠は「人が選ぶ場所」であって推奨値ではない。**既に埋まっていれば満たされている**
            # ——ここを差分として出すと、正しく設定済みの端末に毎回「変更あり」が出る。
            requires = "・".join(slot["requires"] for slot in slots)
            if have.get("candidates"):
                mark, want_text = " ", f"枠 {requires} は充足"
            else:
                mark, want_text = "*", f"枠 {requires} を選んでください"
        else:
            want_text = _candidate_text(want.get("candidates"))
            mark = " " if have_text == want_text else "*"
        lines.append(f"{mark} 実行レベル {TIER_LABELS[tier]}: {have_text} → {want_text}")

    have_mode = ((profiles.get("execution_policy") or {}).get("mode")) or "（未設定）"
    want_mode = recommendation["execution_policy"]["mode"]
    lines.append(f"{' ' if have_mode == want_mode else '*'} 実行方針            {have_mode} → {want_mode}")

    want_conc = ((recommendation.get("control") or {}).get("workloads") or {}) \
        .get("flow", {}).get("concurrency")
    if want_conc:
        have_conc = ((control.get("workloads") or {}).get("flow") or {}).get("concurrency") or {}
        same = all(have_conc.get(k) == v for k, v in want_conc.items())
        lines.append(f"{' ' if same else '*'} 同時実行数          "
                     f"{have_conc or '（未設定）'} → {want_conc}")

    want_rev = recommendation["qualifications"]["revision"]
    have_rev = qualifications.get("revision") if isinstance(qualifications, dict) else None
    lines.append(f"{' ' if have_rev == want_rev else '*'} 適格性              "
                 f"revision {have_rev if have_rev is not None else '（未設定）'} → {want_rev}"
                 f"（{len(recommendation['qualifications']['candidates'])} 候補）")

    lines.append("")
    lines.append("herd の展開（実測が知っている一族の候補）:")
    for row in recommendation["herd"]["expansion"]:
        state = "・".join(row["qualified_for"]) if row["usable"] else "裏付けなし（選ばれません）"
        lines.append(f"    {row['agent_cli']}/{row['model']}: {state}")
    models = recommendation["requires"]["models"]
    if models:
        lines.append("")
        lines.append(f"必要なモデル: ollama pull {' '.join(models)}")
    return lines


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=Path,
                        default=Path(__file__).parent / "results" / "archive")
    parser.add_argument("--output", type=Path,
                        help="書き出し先。省略すると stdout（--print-diff のときは差分だけ）")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--generated-at", help="ISO8601 UTC（省略時は現在時刻）")
    parser.add_argument("--project-dir", default=None,
                        help="定義の探索起点（省略時は現在地）")
    parser.add_argument("--print-diff", action="store_true",
                        help="制御面の現在値との差分を出す（書き込みはしない）")
    parser.add_argument("--control-dir", type=Path,
                        default=Path(os.environ.get("AGENT_CONTROL_DIR")
                                     or Path.home() / ".agents" / "control"),
                        help="--print-diff が読む制御面（既定 $AGENT_CONTROL_DIR）")
    args = parser.parse_args(argv)

    generated_at = (dt.datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
                    if args.generated_at else dt.datetime.now(dt.timezone.utc))
    document = build_recommendation(args.archive, generated_at=generated_at,
                                    revision=args.revision, project_dir=args.project_dir)

    if args.print_diff:
        for line in diff_lines(document, args.control_dir):
            print(line)
        if not args.output:
            return 0

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.output.with_name(f".{args.output.name}.tmp")
        tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(args.output)
    else:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
