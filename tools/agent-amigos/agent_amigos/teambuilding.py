"""チームビルディング — ミッションから最適なロールミッション表を設計する。

従来の入力契約（design doc ＋ ロールミッション表）はそのままに、「ミッションだけ」から
ロールと各ロールへ渡すプロンプト（ミッション文）を設計する段を **team-builder スキル**として
切り出す。ここではそのスキルの手順を agent CLI に渡して実行させ、返ってきた設計（roles 列）を
`normalize_mission` で検証してから、従来の公示経路（post）へ合流する。

- スキル本体（正典）: `.github/skills/team-builder/SKILL.md`。ここではそれを探索して
  プロンプトへ載せる（＝スキルを呼び出す）。見つからない環境（zipapp 単体・未インストール）
  向けに、手順の要点を組み込みフォールバックとして持つ。
- LLM 呼び出しは agentcli.run_agent（全 LLM 呼び出しの単一チョークポイント）を使う。
- 出力契約は `{"mission": {...任意...}, "roles": [ ... ]}`（SKILL.md「出力契約」）。
"""
from __future__ import annotations

import glob
import json
import os

from . import agentcli
from .configfile import agent_home_subdir
from .mission import normalize_mission
from .util import extract_json, now_iso, read_json

SKILL_NAME = "team-builder"
SKILL_ENV = "AGENT_AMIGOS_TEAM_BUILDER_SKILL"

# スキル本体を探索できない環境向けの最小手順。SKILL.md の「プロセス」「出力契約」の要点を
# 写したもの（正典は .github/skills/team-builder/SKILL.md）。
BUILTIN_INSTRUCTIONS = """\
# team-builder（組み込みフォールバック手順）

ミッション（ゴール）だけから、協働で仕上げるのに最適なロール構成と、各ロールへ渡す
ミッション文（＝そのノードのプロンプト）を設計する。出力は agent-amigos のロールミッション表。

## 手順
1. ゴールを最終成果物（deliverables）の集合へ分解する。
2. 各成果物に要る専門性の軸を挙げ、軸が重なるものは 1 ロールに束ねる（最小人数）。
3. 責務が直交するロールへ落とす。各ロールに id / title / mission / deliverables /
   required / requires.tags / agent_cli / approver / collaborates_with を付ける。
   integrator は書かなくてよい（オーナーが自動補充する）。
4. 各ロールの mission 文を、何を作り何を根拠にするか・完了条件・会話相手・迷う判断は
   owner へ上げること、を含めて命令口調で簡潔に書く（それ単体で着手できる粒度）。
5. 必要なら convergence.done_when / budget.execution_minutes を保守的に提案する（不確かなら省略）。
6. 取りこぼし・責務重複・必須の付けすぎ・存在しないタグ要求・承認者欠落が無いか自己検証する。

## 設計原則
最小人数 / 責務の直交 / 必須の最小化 / 能力整合（存在しないタグを要求しない） /
承認ゲートは 1 本（reviewer-approved を使うなら approver ロールを 1 つ以上）/ 予算は保守的。
"""

OUTPUT_CONTRACT = """\
# 出力契約（厳守）
次の JSON **だけ**を出力してください（前後に説明文・コードフェンス以外の地の文を付けない）:
{
  "target": "amigos",   // 既定 amigos（役割協働）。探索木・動的分解が本質なら "agent-flow"
  "pattern": "<採用したパターンの id。どれも使わなければ \\"none\\">",
  "mission": { "title": "...", "goal": "...",
               "convergence": {"done_when": "all-required-done|reviewer-approved"},
               "budget": {"execution_minutes": <int>} },
  "roles": [
    {"id": "<短い識別子。all/owner は不可、/ 不可>", "title": "...",
     "mission": "<このロールへ渡すプロンプト（何を作り何を根拠に・完了条件・会話相手）>",
     "deliverables": ["<artifacts 内の相対パス>"], "required": true|false,
     "requires": {"tags": ["..."]}, "agent_cli": "<任意>",
     "approver": true|false, "collaborates_with": ["<他ロールの id>"]}
  ]
}
- roles は 1 つ以上必須（target=amigos のとき）。mission ブロックは任意（省略時は agent-amigos の既定）。
- integrator は書かなくてよい（オーナーが自動補充する）。明示するなら builtin: "integrator"。

## agent-flow へ委譲する場合（探索木・動的分解が本質のミッション）
Tree/Graph-of-Thoughts・LATS のような「分岐 → スコア → 剪定」の探索や、実行時にデータ駆動で
タスクが増える動的分解は、役割協働（amigos）より **agent-flow（タスクグラフ）**が適する。その場合は
roles を出さず、次を出力する（target=agent-flow）:
{
  "target": "agent-flow",
  "pattern": "<tree-of-thoughts 等 / none>",
  "flow": { "goal": "<agent-flow へ渡す要求本文（; 区切りの段でもよい）>",
            "title": "<任意>", "strategy": "<任意: 分解方針のヒント>" }
}
"""


def _env_skill_path() -> "str | None":
    """環境変数 override。ファイルパスでも、スキルルート dir でも受ける。"""
    env = os.environ.get(SKILL_ENV)
    if not env:
        return None
    p = os.path.expanduser(env)
    if os.path.isdir(p):
        return os.path.join(p, SKILL_NAME, "SKILL.md")
    return p


def _repo_skill_path() -> "str | None":
    """このモジュールから見たリポジトリ内のスキル（開発ツリーで直接動かす場合）。
    tools/agent-amigos/agent_amigos/teambuilding.py の 3 つ上がリポジトリルート。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, "..", "..", "..", ".github", "skills", SKILL_NAME, "SKILL.md")
    return os.path.abspath(cand)


def _skill_search_paths() -> "list[str]":
    """team-builder/SKILL.md の探索候補（先勝ち）。install.py の配置先とリポジトリ内。"""
    paths = []
    envp = _env_skill_path()
    if envp:
        paths.append(envp)
    home = os.path.expanduser("~")
    skill_homes = [
        agent_home_subdir("", "skills"),               # ~/.agents/skills（共通ホーム）
        os.path.join(home, ".claude", "skills"),       # Claude Code
        os.path.join(home, ".codex", "skills"),        # Codex
        os.path.join(home, ".kiro", "skills"),         # Kiro
        os.path.join(home, ".copilot", "skills"),      # Copilot
    ]
    for d in skill_homes:
        paths.append(os.path.join(d, SKILL_NAME, "SKILL.md"))
    paths.append(_repo_skill_path())
    return paths


def resolve_skill_instructions() -> "tuple[str, str]":
    """team-builder スキルの手順本文と出所を返す。見つからなければ組み込みフォールバック。"""
    for p in _skill_search_paths():
        try:
            if p and os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    return f.read(), p
        except OSError:
            continue
    return BUILTIN_INSTRUCTIONS, "(builtin)"


# --- オーケストレーションパターン（論文由来のチーム設計テンプレ） -----------------
# patterns/<id>.json（スキルディレクトリ配下）。tier=high は自動選択に載せ、
# tier=medium は JSON のみ（--pattern <id> の明示指定でだけ使える）。契約は
# references/pattern.schema.json。

def _patterns_dir(skill_source: str) -> "str | None":
    """resolve_skill_instructions が返した SKILL.md の隣の patterns/ ディレクトリ。
    組み込みフォールバック（(builtin)）のときは patterns を持たない。"""
    if not skill_source or skill_source == "(builtin)" or not os.path.isfile(skill_source):
        return None
    d = os.path.join(os.path.dirname(skill_source), "patterns")
    return d if os.path.isdir(d) else None


def load_patterns(skill_source: str, tier: "str | None" = None,
                  only_id: "str | None" = None) -> "list[dict]":
    """patterns/*.json を読み込む。tier で絞り込み（None=全件）、only_id で 1 件指定。"""
    pdir = _patterns_dir(skill_source)
    if not pdir:
        return []
    out = []
    for path in sorted(glob.glob(os.path.join(pdir, "*.json"))):
        rec = read_json(path)
        if not isinstance(rec, dict) or not rec.get("id"):
            continue
        if only_id is not None and rec.get("id") != only_id:
            continue
        if tier is not None and rec.get("tier") != tier:
            continue
        out.append(rec)
    return out


def _pattern_summary(rec: dict) -> str:
    """1 パターンをプロンプト用の簡潔なブロックへ。"""
    if rec.get("target") == "agent-flow":
        flow = rec.get("flow") or {}
        lines = [f"### {rec.get('id')} — {rec.get('name')}（{rec.get('category')}・→ agent-flow 委譲）",
                 f"- 使いどころ: {rec.get('when_to_use', '')}"]
        if rec.get("signals"):
            lines.append(f"- 目印: {', '.join(rec['signals'])}")
        if flow.get("strategy"):
            lines.append(f"- 委譲方針: {flow['strategy']}")
        lines.append("- 出力: target=agent-flow ＋ flow.goal（roles は出さない）")
        return "\n".join(lines)
    team = rec.get("team") or {}
    roles = team.get("roles") or []
    role_line = "; ".join(f"{r.get('id')}: {r.get('role', '')}"
                          + ("[approver]" if r.get("approver") else "")
                          + (f"[×{r['count_hint']}]" if r.get("count_hint") else "")
                          for r in roles)
    conv = team.get("convergence") or {}
    conv_line = ", ".join(f"{k}={v}" for k, v in conv.items()) or "（既定）"
    lines = [f"### {rec.get('id')} — {rec.get('name')}（{rec.get('category')}）",
             f"- 使いどころ: {rec.get('when_to_use', '')}"]
    if rec.get("signals"):
        lines.append(f"- 目印: {', '.join(rec['signals'])}")
    lines.append(f"- ロール骨格: {role_line}")
    lines.append(f"- 収束: {conv_line}"
                 + (f" / 予算目安: {team['budget_hint']}" if team.get("budget_hint") else ""))
    if rec.get("feasibility") and rec["feasibility"] != "native":
        lines.append(f"- 実現度: {rec['feasibility']} — {rec.get('feasibility_note', '')}")
    return "\n".join(lines)


def format_patterns_block(patterns: "list[dict]", forced: bool = False) -> str:
    """パターン群をプロンプトのカタログ節へ。forced=True は「必ずこのパターンを使う」指示。"""
    if not patterns:
        return ""
    body = "\n\n".join(_pattern_summary(p) for p in patterns)
    if forced:
        head = ("===== 使用するパターン（指定・厳守） =====\n"
                "次のパターンのロール骨格・収束条件をミッションに合わせて具体化してください。")
    else:
        head = ("===== オーケストレーションパターン・カタログ（高価値・自動選択対象） =====\n"
                "まずミッションの性質に最も合うパターンを 1 つ選ぶ（複数を組み合わせても、"
                "どれも合わなければ素の設計でもよい）。選んだら、そのロール骨格と収束条件を"
                "ミッションに合わせて具体化し、出力の \"pattern\" にその id（または none）を書く。")
    return f"{head}\n\n{body}"


def brief_text(brief: dict) -> str:
    """ミッションブリーフを人間可読なプロンプト断片へ。"""
    lines = []
    if brief.get("title"):
        lines.append(f"## タイトル\n{brief['title']}")
    lines.append(f"## ゴール（完了したときの状態）\n{brief.get('goal') or '（未指定 — design から読み取ること）'}")
    if brief.get("design"):
        lines.append(f"## design doc（進め方・受入基準・制約。あれば正典として尊重）\n{brief['design']}")
    if brief.get("constraints"):
        lines.append(f"## 制約\n{brief['constraints']}")
    caps = brief.get("capabilities")
    if caps:
        if isinstance(caps, (list, tuple)):
            caps = ", ".join(str(c) for c in caps)
        lines.append(f"## 使えるノードの能力（requires.tags の候補）\n{caps}")
    if brief.get("agent_cli"):
        lines.append(f"## ロールの既定 agent_cli（指定が無いロールはこれ）\n{brief['agent_cli']}")
    return "\n\n".join(lines)


def build_prompt(brief: dict, instructions: str, patterns_block: str = "") -> str:
    """スキル手順 ＋ パターンカタログ ＋ ブリーフ ＋ 出力契約 を 1 つのプロンプトに束ねる。"""
    pat_section = f"{patterns_block}\n\n" if patterns_block else ""
    return (
        "あなたは分散協働ミッションのチーム設計者です。以下の team-builder スキルの手順に"
        "従い、与えられたミッションから最適なロール構成と各ロールへ渡すミッション文"
        "（プロンプト）を設計してください。\n\n"
        "===== team-builder スキル手順 =====\n"
        f"{instructions}\n"
        f"{pat_section}"
        "===== ミッションブリーフ =====\n"
        f"{brief_text(brief)}\n\n"
        "===== 厳守する出力形式 =====\n"
        f"{OUTPUT_CONTRACT}"
    )


def _agent_cli_default(role: dict, brief: dict) -> None:
    """ロールに agent_cli が無く、ブリーフに既定があれば補う（in-place）。"""
    if not role.get("agent_cli") and brief.get("agent_cli"):
        role["agent_cli"] = brief["agent_cli"]


def list_patterns(tier: "str | None" = None) -> "list[dict]":
    """カタログのパターン一覧（CLI --list-patterns 用）。tier=None は全件。"""
    _txt, source = resolve_skill_instructions()
    return load_patterns(source, tier=tier)


def _delegation_id() -> str:
    import time
    return f"dg-{time.strftime('%Y%m%d%H%M%S')}-{os.urandom(2).hex()}"


def build_flow_delegation(brief: dict, data: dict) -> dict:
    """target=agent-flow の設計を、エンジン非依存の委譲封筒（delegation.schema.json の
    op=post / workload=flow）へ組み立てる。ダッシュボードの委譲アダプタや agent-flow submit が
    これを受け取り、タスクグラフ分解 → 分散探索を実行する（探索木は agent-flow の領分・G4）。"""
    flow = dict(data.get("flow") or {})
    goal = str(flow.get("goal") or brief.get("goal") or brief.get("title") or "").strip()
    if not goal:
        raise RuntimeError("agent-flow 委譲には goal（flow.goal かブリーフの goal）が必要です")
    strategy = str(flow.get("strategy") or "").strip()
    if strategy:
        goal = f"{goal}\n\n戦略ヒント: {strategy}"
    env = {"op": "post", "version": 1, "id": _delegation_id(), "workload": "flow",
           "goal": goal, "requested_by": "team-builder", "requested_at": now_iso()}
    title = flow.get("title") or brief.get("title")
    if title:
        env["title"] = str(title)
    if brief.get("design"):
        env["design"] = str(brief["design"])
    return env


def build_team(brief: dict, cli: str, model: "str | None" = None,
               timeout: "float | None" = None,
               pattern: "str | None" = None) -> "tuple[dict, list, dict]":
    """ミッションブリーフからロールミッション表を設計する。

    pattern を指定するとそのパターン（tier 不問）を必ず使うよう指示する。省略時は
    高価値パターン（tier=high）をカタログとして提示し、LLM に最適なものを選ばせる。

    返り値: (mission 上書き dict, roles 列, meta)。roles は normalize_mission で検証済み。
    meta には skill_source と、採用パターン（chosen_pattern）が入る。
    設計に失敗（出力が壊れている・ロールが不正）した場合は RuntimeError。
    """
    if not (brief.get("goal") or brief.get("design")):
        raise RuntimeError("team-builder には goal か design のどちらかが必要です")
    resolved_cli = (cli or "").strip().lower()
    if not resolved_cli or resolved_cli == "stub":
        raise RuntimeError(
            "team-builder は実際の agent CLI が必要です（--agent-cli claude/codex/… を指定してください。"
            "stub / 未指定では設計できません）")

    instructions, source = resolve_skill_instructions()
    if pattern:
        chosen = load_patterns(source, only_id=pattern)
        if not chosen:
            available = ", ".join(p["id"] for p in load_patterns(source)) or "（カタログ無し）"
            raise RuntimeError(f"パターン {pattern!r} が見つかりません。利用可能: {available}")
        patterns_block = format_patterns_block(chosen, forced=True)
    else:
        patterns_block = format_patterns_block(load_patterns(source, tier="high"))

    prompt = build_prompt(brief, instructions, patterns_block)
    text = agentcli.run_agent(prompt, resolved_cli, model, timeout)
    data = extract_json(text)
    if isinstance(data, list):          # roles 配列だけ返ってきた場合は包む
        data = {"roles": data}
    if not isinstance(data, dict):
        raise RuntimeError("team-builder 出力から {\"roles\": [...]} を抽出できませんでした")

    chosen_pattern = pattern or (str(data.get("pattern")) if data.get("pattern") else None)
    if chosen_pattern in ("none", "None", ""):
        chosen_pattern = None
    if str(data.get("target") or "amigos").lower() in ("agent-flow", "flow"):
        deleg = build_flow_delegation(brief, data)
        return {}, [], {"skill_source": source, "chosen_pattern": chosen_pattern,
                        "target": "agent-flow", "delegation": deleg}

    roles = data.get("roles")
    if not isinstance(roles, list) or not roles:
        raise RuntimeError("team-builder 出力に roles（1 つ以上のロール）がありません")

    mission_over = dict(data.get("mission") or {})
    for k in ("title", "goal"):         # LLM が省いてもブリーフ値で公示できるように補完
        if not mission_over.get(k) and brief.get(k):
            mission_over[k] = brief[k]
    for role in roles:
        if isinstance(role, dict):
            _agent_cli_default(role, brief)

    spec = {"mission": mission_over, "roles": roles}
    try:
        normalize_mission(spec)         # 妥当性検証（不正は SystemExit → RuntimeError に翻訳）
    except SystemExit as e:
        raise RuntimeError(f"設計されたロールミッション表が不正です: {e}")
    return mission_over, roles, {"skill_source": source, "chosen_pattern": chosen_pattern,
                                 "target": "amigos"}


def brief_to_design_doc(brief: dict) -> str:
    """design doc が無いブリーフから、公示に使う最小の design doc を組み立てる。
    LLM に設計させた後、post_mission が design doc ファイルの存在を要求するため。"""
    parts = [f"# {brief.get('title') or 'ミッション'}"]
    if brief.get("goal"):
        parts.append(f"## ゴール\n{brief['goal']}")
    if brief.get("constraints"):
        parts.append(f"## 制約\n{brief['constraints']}")
    parts.append("## 備考\nこの design doc は team-builder（ミッションのみ）から自動生成されました。")
    return "\n\n".join(parts) + "\n"
