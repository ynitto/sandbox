"""Fixed development cases; runtime judge, no agent execution."""
import json,sys,time,copy
from pathlib import Path
from experiment_output import output_path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"tools/agent-tools/agentcore"))
from agentcore import modelselect as ms, judge,route
POLICY = (
"First assess the actual work needed for a correct, useful result, including implicit context. "
"A short request is not necessarily easy; quoting a difficult task for translation does not ask you to perform that task. "
"Compare candidates on reasoning, investigation, context and verification needs before considering cost. "
"Tool-loop describes access to tools, not reasoning ability or equal reliability. Missing capability measurements mean unknown, not equally capable. "
"Use relevant measured ratings when provided; do not invent benchmark scores. "
"For a self-contained, straightforward task, avoid paying for capability it does not need. "
"For open-ended work, account for the cost of an inadequate answer or failed investigation. "
"Only use price to break ties among candidates likely to meet the task requirements. "
"Neither local/cloud location nor candidate order is evidence of quality. "
"If none is a credible fit, choose other. Respect quota and context constraints."
)
QUESTION = "Which candidate is most likely to complete the actual task adequately, using the least cost among adequately capable candidates?"
cases=[("greeting","こんにちは","local"),("translation","「追加機能を提案してほしい」を英訳して","local"),("fact","日本の首都はどこですか","local"),("proposal","agent-appの追加機能を提案してほしい","cloud"),("debug","agent-appで送信すると画面が崩れる。コードとログを調査し、原因を特定して修正し、回帰テストで検証して。","cloud"),("architecture","複数ノード間のジョブ重複実行を防ぐ設計を提案して。ネットワーク分断、再起動、冪等性、既存DBとの整合性を考慮して。","cloud")]
def run(cases,variant,output,reverse=False):
 base=[ms.describe_candidate(c,project_dir=ROOT,quotas={}) for c in ms.normalize_candidates(["cursor","codex","claude","ollama/gemma4:e4b"])]
 if reverse:base.reverse()
 for name,prompt,expected in cases:
  state=ms.build_state(ms.prompt_profile(prompt,purpose="work"),base)
  question=ms.build_question(base)
  if variant in ("requirements","no_duplicate_cost","risk_first"):
   state["policy"]=POLICY;question["instructions"]=QUESTION
  if variant=="no_duplicate_cost":
   question["criteria"]={c["id"]: "Execution candidate; consult its evidence and constraints in the state." for c in base}
  if variant in ("risk_first","evidence"):
   state["policy"]="Select for task adequacy first, cost second. Assess the required reasoning and investigation, then compare the actual model capabilities. Tool access alone does not make models equally capable. A brief request can require substantial judgment. For routine self-contained tasks, choose a cheap adequate model. For tasks needing broad reasoning or investigation, prioritize reliable completion over token price. Do not assume any candidate is capable merely because no limitation was listed. Cost breaks ties between adequate candidates. Do not prefer a candidate because of its position."
   question["instructions"]="Which candidate offers the best balance of reliable completion and resource use for this specific request?"
   question["criteria"]={c["id"]: "Execution candidate; consult its evidence and constraints in the state." for c in base}
  if variant=="evidence":
   for c in state["candidates"]:
    if c["agent_cli"]=="ollama":
     c["capability_evidence"]={"source":"eval/results/archive/worker/ledger-2026-08-14-text-eval-gemma4-e4b.jsonl", "scope":"bounded text tasks, default evaluation harness, not an open-ended repository investigation",
       "results":[{"operation":"extract","passed":6,"samples":6},{"operation":"bounded-analysis","passed":6,"samples":6},{"operation":"bounded-proposal","passed":2,"samples":3},{"operation":"bounded-review","passed":2,"samples":6}],
       "limitations":"Small samples. Do not generalize success to tasks requiring unseen repository context or end-to-end investigation."}
    else:c["capability_evidence"]={"status":"not supplied"}
   state["policy"] += " Evaluate evidence only within its operation, harness and constraints; small or unmatched samples do not establish general capability. Unknown evidence is neither failure nor success."
  if variant in ("separated","assessed"):
   state.pop("policy")
   costs={c["id"]:{"cost":c.pop("relative_cost",None),"quota":c.pop("quota",None)} for c in state["candidates"]}
   for c in state["candidates"]:c.pop("site",None)
   state["economics"]=costs
   state["policy"]="Select the model with the reasoning capacity appropriate to the request. First distinguish routine transformations or factual replies from tasks requiring investigation, synthesis, judgment or multiple dependent steps. For routine tasks, minimize cost. For demanding tasks, prioritize reasoning quality and reliability rather than low price. Tool-loop only means tool access, not equal intelligence. Neither a short request nor a low token estimate implies a simple task. Do not choose a larger model for a quoted task when only translation is requested. Model capability may be uncertain: consider the supplied model identity and relevant evaluation evidence without inventing scores."
   question["instructions"]="Which model's reasoning capacity best matches this task?"
   question["criteria"]={c["id"]:"Use the candidate's model and evidence; execution cost is listed separately." for c in base}
  if variant=="assessed":
   assessed=judge.evaluate({"request":prompt},{"requirements":{"type":"choice",
    "instructions":"What level of reasoning does completing the user's actual request require?",
    "criteria":{"routine":"Self-contained factual reply or straightforward transformation. No substantial investigation or design judgment.",
    "demanding":"Investigation, synthesis, judgment, or multiple dependent steps."},
    "other":"The request is too ambiguous to assess."}},model=route.judge_model_setting(),timeout=25)
   state["task"]["requirements"]=assessed["answers"]["requirements"]
   question["instructions"]="Given the assessed task requirements, which candidate's reasoning capacity best matches this task? Avoid spending on unnecessary capability for routine work; avoid trading away needed reasoning quality for demanding work."
  started=time.monotonic()
  try:
   result=judge.evaluate(state,{"candidate":question},model=route.judge_model_setting(),timeout=25)
   answer=result["answers"]["candidate"]
   picked=next((c for c in base if c["id"]==answer["choice"]),{})
   row={"case":name,"prompt":prompt,"expected":expected,"variant":variant,"reverse":reverse,"actual":picked.get("site"),"answer":answer,"seconds":round(time.monotonic()-started,2),"state":state,"question":question}
  except Exception as e:row={"case":name,"variant":variant,"error":str(e)}
  with output.open("a") as f:f.write(json.dumps(row,ensure_ascii=False)+"\n")
  print(json.dumps({k:v for k,v in row.items() if k not in ["state","question"]},ensure_ascii=False),flush=True)
if __name__=="__main__":
 run(cases,sys.argv[1] if len(sys.argv)>1 else "requirements",output_path("tune.jsonl"))
