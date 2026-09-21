"""Controlled prompt contrasts, no changes to production selection."""
import copy, json, sys, time
from pathlib import Path
from experiment_output import output_path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools/agent-tools/agentcore"))
from agentcore import modelselect as ms, judge, route
OUT=output_path("contrast.jsonl")
base=[ms.describe_candidate(c,project_dir=ROOT,quotas={}) for c in ms.normalize_candidates(["cursor","codex","claude","ollama/gemma4:e4b"])]
cases=[("proposal_short","agent-appの追加機能を提案してほしい"),("debug","agent-appで送信すると画面が崩れる。コードとログを調査し、原因を特定して修正し、回帰テストで検証して。")]
for case,prompt in cases:
 for variant in ["repeat1","repeat2","reversed","capability_first","difficulty_context"]:
  fit=copy.deepcopy(base)
  if variant=="reversed": fit.reverse()
  state=ms.build_state(ms.prompt_profile(prompt,purpose="work"),fit)
  if variant=="capability_first":
   state["policy"]="Choose the candidate most likely to complete the task correctly. Consider investigation, reasoning and verification requirements before cost. Use cost only to break ties between equally capable candidates."
  if variant=="difficulty_context":
   state["task"]["difficulty_assessment"]={"choice":"complex","basis":"Independent judge assessment: requires investigation, multi-step reasoning, design judgment, or evidence-based proposals."}
  questions={"candidate":ms.build_question(fit)}
  t=time.monotonic()
  try:
   result=judge.evaluate(state,questions,model=route.judge_model_setting(),timeout=25)
   row={"case":case,"variant":variant,"answers":result["answers"],"seconds":round(time.monotonic()-t,2),"state":state,"questions":questions}
  except Exception as e: row={"case":case,"variant":variant,"error":str(e)}
  with OUT.open("a") as f:f.write(json.dumps(row,ensure_ascii=False)+"\n")
  print(json.dumps({k:v for k,v in row.items() if k not in ["state","questions"]},ensure_ascii=False),flush=True)
