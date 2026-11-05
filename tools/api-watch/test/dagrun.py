"""例の Dagu のワークフロー（*.example.yaml）を、Dagu と同じ順で動かす小さな実行役（試験用・標準ライブラリ + PyYAML）。

例の DAG が本物の api-watch で動くことを確かめるためのもの。Dagu の次の動きだけをまねる:

- steps を depends の順に 1 つずつ動かす（type: graph。例はどれも一本道）
- output: VAR はそのステップの標準出力（前後の空白を除く）を ${VAR} にする。${<id>.exit_code} はそのステップの終了コード
- preconditions の condition を展開し、expected（re: なら正規表現）に合わなければそのステップを飛ばす
- retry_policy は exit_code に当たる終了コードのとき limit 回まで（待たずに）やり直す
- 失敗したら、continue_on.failure が無ければそこで止める

api-watch のステップ（command が例の api-watch のパス）は aw の argv で呼び、args の中の例のパスは paths で置き換え、
先頭に head を足す（--config・--state-dir など）。exec でないステップ（handle の harness.run など）は handle(step, vars) を呼ぶ。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

EXAMPLE_COMMAND = "/home/you/.local/bin/api-watch"


def load(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def check_shape(test, dag: dict, where: str) -> None:
    """例の DAG の形: depends があれば type: graph・名前が重ならない・depends が前のステップを指す・取得は終了コード 3 だけ再試行する。"""
    if any(s.get("depends") for s in dag["steps"]):
        test.assertEqual(dag.get("type"), "graph", where)
    seen: set[str] = set()
    for s in dag["steps"]:
        test.assertNotIn(s["name"], seen, where)
        for d in s.get("depends") or []:
            test.assertIn(d, seen, f"{where}: {s['name']} の depends {d} が前に無い")
        seen.add(s["name"])
        args = (s.get("with") or {}).get("args") or []
        if s.get("action") == "exec" and s["with"]["command"] == EXAMPLE_COMMAND and "fetch" in args:
            test.assertEqual(s["retry_policy"]["exit_code"], [3], f"{where}: fetch は一時的な失敗（3）だけ再試行する")


def _expand(value: str, vars: dict[str, str]) -> str:
    return re.sub(r"\$\{([A-Za-z0-9_.]+)\}", lambda m: vars.get(m.group(1), ""), str(value))


def _ok(pre: list | None, vars: dict[str, str]) -> bool:
    for p in pre or []:
        got = _expand(p["condition"], vars)
        want = str(p.get("expected", ""))
        if want.startswith("re:"):
            if not re.search(want[3:], got):
                return False
        elif got != want:
            return False
    return True


def run(dag_path: Path, *, aw: list[str], head: list[str], paths: dict[str, str] | None = None,
        handle: Callable[[dict, dict], int] | None = None, env: dict | None = None) -> dict[str, int | None]:
    """DAG を 1 回動かし、ステップごとの終了コード（飛ばしたら None）を返す。止まったところより後は入らない。"""
    dag = load(dag_path)
    vars: dict[str, str] = {}
    result: dict[str, int | None] = {}
    for s in dag["steps"]:
        if not _ok(s.get("preconditions"), vars):
            result[s["name"]] = None
            continue
        tries = 1 + int((s.get("retry_policy") or {}).get("limit", 0))
        codes = (s.get("retry_policy") or {}).get("exit_code")
        for _ in range(tries):
            out = ""
            if s.get("action") == "exec":
                w = s["with"]
                args = [_expand((paths or {}).get(a, a), vars) for a in w.get("args") or []]
                if w["command"] == EXAMPLE_COMMAND:
                    argv = aw + head + args
                else:
                    argv = [w["command"]] + args
                p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                                   env={**os.environ, **(env or {})}, cwd=s.get("working_dir"))
                code, out = p.returncode, p.stdout
                if code and not (codes and code in codes):
                    print(f"[{dag_path.name}:{s['name']}] 終了コード {code}\n{p.stdout}{p.stderr}")
            else:
                if handle is None:
                    raise AssertionError(f"{dag_path.name}: {s['name']} は exec ではないので handle を渡してください")
                code = handle(s, dict(vars))
            if code == 0 or not codes or code not in codes:
                break
        result[s["name"]] = code
        if s.get("output"):
            vars[s["output"]] = out.strip()
        vars[f"{s.get('id', s['name'])}.exit_code"] = str(code)
        if code != 0 and not (s.get("continue_on") or {}).get("failure"):
            break
    return result
