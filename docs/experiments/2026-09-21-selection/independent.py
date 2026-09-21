"""Price-blind independent fit probes; no selected task is run."""
import sys,json,time
from pathlib import Path
from experiment_output import output_path
import tune
ms,judge,route=tune.ms,tune.judge,tune.route
REQ={"type":"choice","instructions":"Assess the reasoning needed to perform the actual request, not the task mentioned inside quoted text. Translation, extraction and reformatting of supplied text are routine even when that text discusses complex work.",
"criteria":{"routine":"Self-contained factual reply or straightforward transformation of supplied content.",
"demanding":"Requires investigation, synthesis, design judgment, or multiple dependent steps."},
"other":"Insufficient information."}
FIT={"type":"boolean","instructions":"Is this execution candidate a good capability match for completing the actual request correctly? Assess the required reasoning, investigation and verification against the model and relevant evidence. Tool access does not establish reasoning quality. Do not treat missing evaluations as proof of either competence or failure. Evaluate only this candidate; ignore its brand prestige and deployment location. Answer yes for a credible adequate match, no if the task needs capabilities or reliability it is unlikely to supply."}
def evaluate(prompt,candidates):
 usage={"tokens_in":0,"tokens_out":0}
 req=judge.evaluate({"request":prompt},{"requirements":REQ},model=route.judge_model_setting(),timeout=25)
 fits={}
 for c in candidates:
  material={"request":prompt,"requirements":req["answers"]["requirements"],"candidate":{k:v for k,v in c.items() if k not in ("site","relative_cost","quota","rank")}}
  material["candidate"]["model_identity"]={"model":c["model"] or None,"source":"explicit-or-definition-default" if c["model"] else "provider-default-not-resolved"}
  material["candidate"]["measurement_status"]="available" if (c.get("rating") or {}).get("runs",0) else "not measured"
  result=judge.evaluate(material,{"fit":FIT},model=route.judge_model_setting(),timeout=25)
  fits[c["id"]]=result["answers"]["fit"]
 return req["answers"]["requirements"],fits
if __name__=="__main__":
 base=[ms.describe_candidate(c,project_dir=tune.ROOT,quotas={}) for c in ms.normalize_candidates(["cursor","codex","claude","ollama/gemma4:e4b"])]
 output=output_path("independent.jsonl")
 for name,prompt,expected in tune.cases:
  t=time.monotonic();req,fits=evaluate(prompt,base)
  best=max(a["probability"] for a in fits.values())
  eligible=[c for c in base if fits[c["id"]]["probability"]>=max(.6,best-.05)]
  picked=ms.audit_order(eligible)[0] if eligible else None
  row={"case":name,"prompt":prompt,"expected":expected,"requirements":req,"fits":fits,"selected":picked["id"] if picked else None,"actual":picked["site"] if picked else None,"seconds":round(time.monotonic()-t,2)}
  with output.open("a") as f:f.write(json.dumps(row,ensure_ascii=False)+"\n")
  print(json.dumps(row,ensure_ascii=False),flush=True)
