"""偽の dagu（enqueue / retry だけ）。記録は環境変数 FAKE_DAGU_LOG の JSON Lines。

- 同じ DAG・同じ実行 ID の enqueue は、本物と同じく "already exists" で失敗する
- FAKE_DAGU_LOG と同じフォルダの fail_dags に書いた DAG への enqueue は失敗する
"""
import json
import os
import sys


def main() -> int:
    log = os.environ["FAKE_DAGU_LOG"]
    args = sys.argv[1:]
    cmd = args[0]
    run_id = next(a.split("=", 1)[1] for a in args if a.startswith("--run-id="))
    if cmd == "enqueue":
        rest = args[1:]
        i = rest.index("--")
        dag = rest[i - 1]
        params = dict(p.split("=", 1) for p in rest[i + 1:])
        queue = next((a.split("=", 1)[1] for a in rest[:i] if a.startswith("--queue=")), None)
        fail = os.path.join(os.path.dirname(log), "fail_dags")
        if os.path.exists(fail) and dag in open(fail, encoding="utf-8").read().split():
            print("Error: failed to enqueue: queue store unavailable", file=sys.stderr)
            return 1
        if os.path.exists(log):
            for line in open(log, encoding="utf-8"):
                r = json.loads(line)
                if r["cmd"] == "enqueue" and r["dag"] == dag and r["run_id"] == run_id:
                    print(f'Error: DAG "{dag}" with ID "{run_id}" already exists', file=sys.stderr)
                    return 1
        rec = {"cmd": "enqueue", "dag": dag, "run_id": run_id, "params": params, "queue": queue}
    elif cmd == "retry":
        rec = {"cmd": "retry", "dag": args[-1], "run_id": run_id}
    else:
        print(f"unknown command {cmd}", file=sys.stderr)
        return 2
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"dag-run {run_id} enqueued")
    return 0


if __name__ == "__main__":
    sys.exit(main())
