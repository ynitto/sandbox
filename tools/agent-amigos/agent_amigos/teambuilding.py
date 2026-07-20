"""チームビルディング — ミッションから最適なロールミッション表を設計する。

従来の入力契約（design doc ＋ ロールミッション表）はそのままに、「ミッションだけ」から
ロールと各ロールへ渡すプロンプト（ミッション文）を設計する段を **team-building スキル**として
切り出す。ここではそのスキルの手順を agent CLI に渡して実行させ、返ってきた設計（roles 列）を
`normalize_mission` で検証してから、従来の公示経路（post）へ合流する。

- スキル本体（正典）: `.github/skills/team-building/SKILL.md`。ここではそれを探索して
  プロンプトへ載せる（＝スキルを呼び出す）。見つからない環境（zipapp 単体・未インストール）
  向けに、手順の要点を組み込みフォールバックとして持つ。
- LLM 呼び出しは agentcli.run_agent（全 LLM 呼び出しの単一チョークポイント）を使う。
- 出力契約は `{"mission": {...任意...}, "roles": [ ... ]}`（SKILL.md「出力契約」）。
"""
from __future__ import annotations

import os

from . import agentcli
from .configfile import agent_home_subdir
from .mission import normalize_mission
from .util import extract_json

SKILL_NAME = "team-building"
SKILL_ENV = "AGENT_AMIGOS_TEAM_BUILDING_SKILL"

# スキル本体を探索できない環境向けの最小手順。SKILL.md の「プロセス」「出力契約」の要点を
# 写したもの（正典は .github/skills/team-building/SKILL.md）。
BUILTIN_INSTRUCTIONS = """\
# team-building（組み込みフォールバック手順）

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
- roles は 1 つ以上必須。mission ブロックは任意（省略時は agent-amigos の既定）。
- integrator は書かなくてよい（オーナーが自動補充する）。明示するなら builtin: "integrator"。
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
    """team-building/SKILL.md の探索候補（先勝ち）。install.py の配置先とリポジトリ内。"""
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
    """team-building スキルの手順本文と出所を返す。見つからなければ組み込みフォールバック。"""
    for p in _skill_search_paths():
        try:
            if p and os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    return f.read(), p
        except OSError:
            continue
    return BUILTIN_INSTRUCTIONS, "(builtin)"


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


def build_prompt(brief: dict, instructions: str) -> str:
    """スキル手順 ＋ ブリーフ ＋ 出力契約 を 1 つのプロンプトに束ねる。"""
    return (
        "あなたは分散協働ミッションのチーム設計者です。以下の team-building スキルの手順に"
        "従い、与えられたミッションから最適なロール構成と各ロールへ渡すミッション文"
        "（プロンプト）を設計してください。\n\n"
        "===== team-building スキル手順 =====\n"
        f"{instructions}\n"
        "===== ミッションブリーフ =====\n"
        f"{brief_text(brief)}\n\n"
        "===== 厳守する出力形式 =====\n"
        f"{OUTPUT_CONTRACT}"
    )


def _agent_cli_default(role: dict, brief: dict) -> None:
    """ロールに agent_cli が無く、ブリーフに既定があれば補う（in-place）。"""
    if not role.get("agent_cli") and brief.get("agent_cli"):
        role["agent_cli"] = brief["agent_cli"]


def build_team(brief: dict, cli: str, model: "str | None" = None,
               timeout: "float | None" = None) -> "tuple[dict, list, dict]":
    """ミッションブリーフからロールミッション表を設計する。

    返り値: (mission 上書き dict, roles 列, meta)。roles は normalize_mission で検証済み。
    設計に失敗（出力が壊れている・ロールが不正）した場合は RuntimeError。
    """
    if not (brief.get("goal") or brief.get("design")):
        raise RuntimeError("team-building には goal か design のどちらかが必要です")
    resolved_cli = (cli or "").strip().lower()
    if not resolved_cli or resolved_cli == "stub":
        raise RuntimeError(
            "team-building は実際の agent CLI が必要です（--agent-cli claude/codex/… を指定してください。"
            "stub / 未指定では設計できません）")

    instructions, source = resolve_skill_instructions()
    prompt = build_prompt(brief, instructions)
    text = agentcli.run_agent(prompt, resolved_cli, model, timeout)
    data = extract_json(text)
    if isinstance(data, list):          # roles 配列だけ返ってきた場合は包む
        data = {"roles": data}
    if not isinstance(data, dict):
        raise RuntimeError("team-building 出力から {\"roles\": [...]} を抽出できませんでした")
    roles = data.get("roles")
    if not isinstance(roles, list) or not roles:
        raise RuntimeError("team-building 出力に roles（1 つ以上のロール）がありません")

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
    return mission_over, roles, {"skill_source": source}


def brief_to_design_doc(brief: dict) -> str:
    """design doc が無いブリーフから、公示に使う最小の design doc を組み立てる。
    LLM に設計させた後、post_mission が design doc ファイルの存在を要求するため。"""
    parts = [f"# {brief.get('title') or 'ミッション'}"]
    if brief.get("goal"):
        parts.append(f"## ゴール\n{brief['goal']}")
    if brief.get("constraints"):
        parts.append(f"## 制約\n{brief['constraints']}")
    parts.append("## 備考\nこの design doc は team-building（ミッションのみ）から自動生成されました。")
    return "\n\n".join(parts) + "\n"
