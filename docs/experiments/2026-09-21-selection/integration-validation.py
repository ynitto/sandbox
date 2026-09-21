"""Direct production selection checks; never launches chosen CLIs."""
import json,time
from pathlib import Path
from experiment_output import output_path
import tune
out=output_path('integration-validation.jsonl')
out.write_text('')
for reverse in (False,True):
 candidates=['cursor','codex','claude','ollama/gemma4:e4b']
 if reverse:candidates.reverse()
 for name,prompt,expected in tune.cases:
  start=time.monotonic()
  try:
   result=tune.ms.select(prompt,candidates,purpose='work',project_dir=tune.ROOT,stages=('judge','audit'),quotas={},judge_model='gemma4:e4b')
   row={'case':name,'prompt':prompt,'expected':expected,'reverse':reverse,'seconds':round(time.monotonic()-start,2),'result':result}
  except Exception as e:row={'case':name,'reverse':reverse,'seconds':round(time.monotonic()-start,2),'error':repr(e)}
  with out.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
  print(json.dumps({k:v for k,v in row.items() if k!='result'}|{'selected':row.get('result',{}).get('selected'),'stage':row.get('result',{}).get('stage')},ensure_ascii=False),flush=True)
