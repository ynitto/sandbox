"""Run local judge experiments; never executes the selected agent or task."""
import json, sys, time
from pathlib import Path
from experiment_output import output_path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/agent-tools/agentcore"))
from agentcore import modelselect as ms, judge, route
OUT = output_path("results.jsonl")
CASES = [
 ("greeting", "こんにちは"),
 ("fact", "日本の首都はどこですか"),
 ("proposal_short", "agent-appの追加機能を提案してほしい"),
 ("proposal_grounded", "agent-appの既存コードと仕様を調査し、未実装の追加機能を3案提案してほしい。各案の根拠、影響範囲、リスク、優先順位も示して。"),
 ("debug", "agent-appで送信すると画面が崩れる。コードとログを調査し、原因を特定して修正し、回帰テストで検証して。"),
 ("translate", "「追加機能を提案してほしい」を英訳して"),
 ("architecture", "複数ノード間のジョブ重複実行を防ぐ設計を提案して。ネットワーク分断、再起動、冪等性、既存DBとの整合性を考慮して。"),
 ("proposal_en", "Suggest additional features for agent-app"),
]
candidates = ["cursor", "codex", "claude", "ollama/gemma4:e4b"]
model = route.judge_model_setting()
described = [ms.describe_candidate(c, project_dir=ROOT, quotas={}) for c in ms.normalize_candidates(candidates)]
difficulty = {"difficulty": {"type":"choice", "instructions":"How demanding is this task to complete correctly?",
 "criteria":{"simple":"A straightforward response or transformation with little reasoning and no investigation.",
 "complex":"Requires investigation, multi-step reasoning, design judgment, or evidence-based proposals."},
 "other":"The available request is insufficient to decide."}}
def write(row):
 with OUT.open("a") as f: f.write(json.dumps(row, ensure_ascii=False)+"\n")
 print(json.dumps({k:v for k,v in row.items() if k not in ["state","questions","result"]},ensure_ascii=False),flush=True)
write({"kind":"metadata","model":model,"jev_enabled":ms.jev_setting()["enabled"],"candidates":described,"threshold":ms.min_confidence_setting()})
for case,prompt in CASES:
 for mode in ("select","difficulty","route"):
  start=time.monotonic()
  try:
   if mode=="select":
    state=ms.build_state(ms.prompt_profile(prompt,purpose="work"),described)
    questions={"candidate":ms.build_question(described)}
   elif mode=="difficulty":
    state={"request":prompt};questions=difficulty
   else:
    c=route.normalize_candidates({"tasks":[],"flows":[],"skills":[],"context":{"repo":"sandbox","readonly":False}})
    state=route.build_state(prompt,c);questions=route.build_questions(c)
   result=judge.evaluate(state,questions,model=model,timeout=25)
   write({"kind":"trial","case":case,"mode":mode,"prompt":prompt,"seconds":round(time.monotonic()-start,2),
     "answers":result.get("answers"),"state":state,"questions":questions,"result":result})
  except Exception as e:
   write({"kind":"trial","case":case,"mode":mode,"seconds":round(time.monotonic()-start,2),"error":str(e)})
