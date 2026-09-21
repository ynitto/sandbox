"""Independent held-out and reverse-order assessed validation; no CLI execution."""
import json,time
from pathlib import Path
from experiment_output import output_path
import tune
ms=tune.ms
OUT=output_path("assessed-validation-summary.json").parent
holdout=json.loads((Path(__file__).parent / "independent-holdout-input.json").read_text())
cases=[(c["id"],c["prompt"],"local" if c["group"]=="simple" else "cloud") for c in holdout]
summaries=[]
for name,group,reverse in [("holdout-normal",cases,False),("holdout-reverse",cases,True),("development-reverse",tune.cases,True)]:
 output=OUT/("assessed-validation-"+name+".jsonl")
 output.write_text("")
 started=time.monotonic()
 tune.run(group,"assessed",output,reverse=reverse)
 elapsed=round(time.monotonic()-started,2)
 base=[ms.describe_candidate(c,project_dir=tune.ROOT,quotas={}) for c in ms.normalize_candidates(["cursor","codex","claude","ollama/gemma4:e4b"])]
 if reverse:base.reverse()
 rows=[]
 for line in output.read_text().splitlines():
  row=json.loads(line)
  if "error" in row:rows.append(row);continue
  picked,reason=ms._pick(row["answer"],base,min_confidence=0.6)
  stage="judge" if picked else "audit"
  if picked is None:picked=ms.audit_order(base)[0]
  rows.append({"case":row["case"],"expected":row["expected"],"raw":row["actual"],"confidence":row["answer"].get("confidence"),"final":picked["site"],"final_id":picked["id"],"stage":stage,"pick_reason":reason,"requirements":row["state"]["task"]["requirements"]})
 summary={"round":name,"count":len(rows),"wall_seconds":elapsed,"matches":sum(r.get("final")==r.get("expected") for r in rows),"judge_accepted":sum(r.get("stage")=="judge" for r in rows),"rows":rows}
 summaries.append(summary)
 print(json.dumps(summary,ensure_ascii=False),flush=True)
 (OUT/"assessed-validation-summary.json").write_text(json.dumps(summaries,ensure_ascii=False,indent=2))
