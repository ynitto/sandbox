#!/usr/bin/env python3
"""qwen3.5:9b を **worker 専任**で置いたときの受入率を測る。

判定役は一切使わない（クラウドへ戻す決定に従い、測定対象から外す）。合否は決定的な
チェッカーが出すので、実行のたびに揺れない。agent-project も agent-flow も bus も
通さず、agent-ollama を本番と同じ argv・同じプロンプトで直接叩く。

本番との同一性で担保するもの:
  - argv: agents/ollama.json の write_args そのまま
  - プロンプト: flow-worker スキルの prompt.py（agent-flow が使う正典のビルダー）
  - 上限: agent-flow の agent_timeout 既定 600 秒。超過は fail

使い方: python3 worker_eval.py [--model qwen3.5:9b] [--repeat 3]
        [--tasks T1,T2,T3] [--wall 600]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VENV_PY = REPO / ".venv/bin/python"
PROMPT_BUILDER = Path(os.environ.get(
    "FLOW_WORKER_PROMPT",
    str(Path.home() / ".claude/skills/flow-worker/scripts/prompt.py")))
# worktree はリポジトリの**外**に置く。中に作ると評価の残骸が作業ツリーを汚す。
WORK = Path(os.environ.get("WORKER_EVAL_DIR",
                           str(Path(tempfile.gettempdir()) / "agent-worker-eval")))
MODEL = "qwen3.5:9b"        # --model で上書き
CLI = "agent-ollama"        # --cli で上書き（agent-ollama | aider）
NUM_PREDICT = 0             # --num-predict で上書き（0 = 上限なし。aider 経路のみ）
OLLAMA_API_BASE = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434")
WALL_LIMIT = 600.0          # agent-flow の agent_timeout 既定
# 本番の argv は agents/ollama.json の write_args。**書き写さずに読む**——初版は
# ここへ literal を写していたが、定義側が予算を 30 → 12 へ絞った当日にずれた。
# 「ここを変えたら本番を測っていない」を人の注意力で守らせない（ずれても静かに
# 測定が別物になるだけで、誰も気づけない形の失敗だった）。
_FALLBACK_WRITE_ARGS = ["--think", "off", "--tools", "bash",
                        "--max-rounds", "12", "--command-timeout", "900"]


def load_write_args() -> "tuple[list[str], str]":
    """`agents/ollama.json` の `write_args` と、その出所を返す。

    定義が読めないときに黙って止めないのは、ハーネスが測定の道具だから——ただし
    どちらを使ったかは起動時に必ず表示する（測定条件を隠さない）。
    """
    try:
        spec = json.loads((REPO / "agents/ollama.json").read_text(encoding="utf-8"))
        args = spec.get("write_args")
        if isinstance(args, list) and args:
            return [str(a) for a in args], "agents/ollama.json"
    except (OSError, ValueError):
        pass
    return list(_FALLBACK_WRITE_ARGS), "fallback（定義を読めませんでした）"


WRITE_ARGS, WRITE_ARGS_SOURCE = load_write_args()

REPO_INSTRUCTION = (
    "作業ディレクトリはこのタスク専用の worktree。テストの実行には "
    f"`{VENV_PY} -m pytest` を使うこと（この worktree に .venv は無い）。"
)

# ---------------------------------------------------------------- タスク定義
# 各タスクは seed（実行前の仕込み）と check（決定的な合否）を持つ。
# check は (ok: bool, note: str) を返す。


def _pytest(cwd: Path, *targets: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(VENV_PY), "-m", "pytest", "-q", *targets],
                          cwd=cwd, capture_output=True, text=True, timeout=300)


def seed_t1(wt: Path) -> None:
    (wt / "eval").mkdir(exist_ok=True)


def check_t1min(wt: Path) -> tuple[bool, str]:
    """T1 からテスト追加の契約を外した版。**実装だけ**できるかを見る。

    「粒度を極小化すれば使えるのか」を切り分けるための最小セル。ここも落ちるなら
    タスクの割り方では救えない。"""
    ok, note = check_t1(wt, require_tests=False)
    return ok, note


def check_t1(wt: Path, require_tests: bool = True) -> tuple[bool, str]:
    """仕様どおりの human_bytes があるか。実装は見ず、振る舞いだけ突き合わせる。"""
    mod = wt / "eval" / "humansize.py"
    if not mod.exists():
        return False, "eval/humansize.py が無い"
    probe = wt / "_probe.py"
    probe.write_text(
        "import sys; sys.path.insert(0, 'eval')\n"
        "from humansize import human_bytes as h\n"
        "cases = [(0,'0 B'),(512,'512 B'),(1024,'1.0 KiB'),(1536,'1.5 KiB'),\n"
        "         (1048576,'1.0 MiB'),(1073741824,'1.0 GiB')]\n"
        "bad = [(n, h(n), want) for n, want in cases if h(n) != want]\n"
        "print('BAD' if bad else 'OK', bad)\n", encoding="utf-8")
    r = subprocess.run([str(VENV_PY), "_probe.py"], cwd=wt,
                       capture_output=True, text=True, timeout=60)
    probe.unlink(missing_ok=True)
    if r.returncode != 0:
        return False, f"import/実行が失敗: {r.stderr.strip().splitlines()[-1:] }"
    if not r.stdout.startswith("OK"):
        return False, f"振る舞いが仕様と違う: {r.stdout.strip()[:200]}"
    if not require_tests:
        return True, "仕様 6 ケース pass（テスト契約は課していない）"
    tests = [p for p in (wt / "eval").glob("test_*.py")
             if "human_bytes" in p.read_text(encoding="utf-8", errors="replace")]
    if not tests:
        return False, "テストが追加されていない（仕様どおり動くが契約は未達）"
    t = _pytest(wt, *[str(p.relative_to(wt)) for p in tests])
    if t.returncode != 0:
        return False, "追加されたテスト自体が落ちる"
    return True, f"仕様 6 ケース pass / テスト {len(tests)} ファイル pass"


BILLING_BUGGY = '''"""日割り課金の計算。"""


def prorate(monthly_fee: int, days_used: int, days_in_month: int) -> int:
    """日割り額を出す。端数は**切り上げ**（事業者側の仕様）。"""
    return monthly_fee * days_used // days_in_month


def invoice_total(items, discount_pct: int = 0) -> int:
    """明細の合計に割引率を **1 度だけ** 適用する。端数は切り捨て。"""
    total = 0
    for it in items:
        line = it["unit_price"] * it["qty"]
        total += line - line * discount_pct // 100
    return total
'''

BILLING_TEST = '''from billing import invoice_total, prorate


def test_prorate_full_month():
    assert prorate(3000, 30, 30) == 3000


def test_prorate_rounds_up():
    # 3000 * 10 / 31 = 967.74... → 切り上げて 968
    assert prorate(3000, 10, 31) == 968


def test_invoice_applies_discount_once_to_the_total():
    """割引は明細ごとではなく合計に 1 度だけ掛ける（明細ごとに丸めない）。"""
    items = [{"unit_price": 105, "qty": 1} for _ in range(3)]
    # 小計 315 に 10% を 1 度 → 315 - 31 = 284（明細ごとだと 285 になる）
    assert invoice_total(items, 10) == 284
'''


def seed_t2(wt: Path) -> None:
    d = wt / "eval"
    d.mkdir(exist_ok=True)
    (d / "billing.py").write_text(BILLING_BUGGY, encoding="utf-8")
    (d / "test_billing.py").write_text(BILLING_TEST, encoding="utf-8")


def check_t2(wt: Path) -> tuple[bool, str]:
    """テストを通したか。ただし**テストを書き換えて通したらズル**として落とす。"""
    tf = wt / "eval" / "test_billing.py"
    if not tf.exists():
        return False, "テストファイルが消えている"
    if hashlib.sha256(tf.read_bytes()).hexdigest() != hashlib.sha256(
            BILLING_TEST.encode("utf-8")).hexdigest():
        return False, "テストを書き換えて通した（ズル）"
    r = _pytest(wt / "eval", "test_billing.py")
    if r.returncode != 0:
        tail = [l for l in r.stdout.splitlines() if l.strip()][-1:]
        return False, f"テストが落ちたまま: {tail}"
    return True, "テスト無改変で 3 件 pass"


def seed_t3(wt: Path) -> None:
    pass


def check_t3(wt: Path) -> tuple[bool, str]:
    """本番で 4/4 fail した実タスク。receipt の C1・C3 に対応する部分を機械判定する。"""
    schema = wt / "schemas" / "node-budget-summary.schema.json"
    if not schema.exists():
        return False, "C1 fail: schemas/node-budget-summary.schema.json が無い"
    try:
        doc = json.loads(schema.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, f"C1 fail: schema が JSON として壊れている ({e})"
    if not isinstance(doc, dict) or "properties" not in doc:
        return False, "C1 fail: properties を持たない（JSON Schema になっていない）"
    tests = [p for p in (wt / "tools/agent-project").rglob("test_*.py")
             if _is_new(wt, p)]
    if not tests:
        return False, "C3 fail: 契約テストが追加されていない"
    r = _pytest(wt / "tools/agent-project", *[str(p) for p in tests])
    if r.returncode != 0:
        return False, "C3 fail: 追加された契約テストが落ちる"
    return True, f"C1 pass / C3 pass（テスト {len(tests)} ファイル）"


def _is_new(wt: Path, p: Path) -> bool:
    r = subprocess.run(["git", "ls-files", "--error-unmatch", str(p.relative_to(wt))],
                       cwd=wt, capture_output=True, text=True)
    return r.returncode != 0


TASKS = {
    "T1": dict(
        seed=seed_t1, check=check_t1,
        files=("eval/humansize.py", "eval/test_humansize.py"),
        test_cmd=f"{VENV_PY} -m pytest -q eval",
        request="agentcore の周辺ユーティリティを整える",
        goal=("eval/humansize.py に関数 human_bytes(n: int) -> str を実装する。"
              "1024 未満は '512 B' のようにバイト表記、以降は KiB / MiB / GiB へ丸め、"
              "小数第 1 位まで出す（例: 1024 -> '1.0 KiB'、1536 -> '1.5 KiB'、"
              "1048576 -> '1.0 MiB'）。あわせて eval/test_*.py に単体テストを追加し、"
              "テストが通ることを確認する。eval/ 以外は変更しない。"),
    ),
    "T1min": dict(
        seed=seed_t1, check=check_t1min,
        files=("eval/humansize.py",),
        request="agentcore の周辺ユーティリティを整える",
        goal=("eval/humansize.py というファイルを 1 つだけ作り、その中に関数 "
              "human_bytes(n: int) -> str を実装する。仕様は次のとおり: "
              "1024 未満は '512 B' のようにバイト表記、1024 以上は KiB / MiB / GiB へ"
              "1024 ごとに繰り上げ、小数第 1 位まで出す。"
              "例: human_bytes(0)=='0 B'、human_bytes(512)=='512 B'、"
              "human_bytes(1024)=='1.0 KiB'、human_bytes(1536)=='1.5 KiB'、"
              "human_bytes(1048576)=='1.0 MiB'、human_bytes(1073741824)=='1.0 GiB'。"
              "テストは書かなくてよい。このファイル以外は一切変更しない。"),
    ),
    "T2": dict(
        seed=seed_t2, check=check_t2,
        # テストは仕様の正なので読み取り専用で渡す（書き換えはチェッカーがズルとして落とす）。
        files=("eval/billing.py",), read=("eval/test_billing.py",),
        # aider 経路でだけ使う（--test-cmd + --auto-test）。agent-ollama 経路は
        # プロンプトでテスト実行を指示しており、道具の作法がそれぞれ違う。
        test_cmd=f"{VENV_PY} -m pytest -q eval/test_billing.py",
        request="課金計算の不具合を直す",
        goal=("eval/test_billing.py が失敗している。eval/billing.py の実装を直して "
              "3 件すべて通るようにする。**テストファイルは変更しないこと**"
              "（テストが仕様の正）。eval/ 以外は変更しない。"),
    ),
    "T3": dict(
        seed=seed_t3, check=check_t3,
        # 実タスクなので置き場所の探索が要る。ここだけリポジトリマップに予算を与える。
        files=("schemas/node-budget-summary.schema.json",), map_tokens=1024,
        test_cmd=f"{VENV_PY} -m pytest -q tools/agent-project",
        request=("node-budget-summary スキーマを追加し status/<node>.json へ埋め込む。"
                 "Phase1 の前提である射影 schema を先に固定し、互換性テストで安全に出すため。"),
        goal=("schemas/node-budget-summary.schema.json を追加し、status/<node>.json の "
              "budget block に additive に埋める仕様を定義する。reader が optional で"
              "壊れない互換性を担保する契約テストを tools/agent-project 配下に追加する。"
              "変更してよいのは tools/agent-project 配下とリポジトリルートの schemas/ のみ。"),
    ),
}

# ---------------------------------------------------------------- 実行


def build_prompt(task: dict) -> str:
    payload = {
        "role": "worker", "kind": "work", "goal": task["goal"],
        "request": task["request"], "deps": {},
        "repo_instruction": REPO_INSTRUCTION, "artifact_note": "",
        "workspace": {}, "references": [], "instructions": "",
        "repair_note": "", "read_note": "",
    }
    r = subprocess.run([sys.executable, str(PROMPT_BUILDER)],
                       input=json.dumps(payload, ensure_ascii=False),
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"prompt.py が失敗: {r.stderr[:300]}")
    return r.stdout.strip()


def _load_aider_spec():
    """aider の起動形は `agents/aider.json` を**読む**（写さない）。

    argv を写すと定義側の変更に静かに置いていかれる——agent-ollama 経路で実際に起きた。
    ファイルの受け渡し（--file / --read）も定義の `file_flag` / `read_flag` に従う。
    """
    sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))
    from agentcore import agentcli
    return agentcli, agentcli.load_cli("aider")


def aider_settings(model: str, num_ctx: int = 32768, num_predict: int = 0) -> Path:
    """aider へ渡すモデル設定（文脈と 1 ターンの生成上限）。

    aider の直し直しは 3 回で止まる（`max_reflections`・CLI フラグは無い）ので、壁時計を
    焼くのは回数ではなく **1 ターンの生成の長さ**である——実測で最後のターンが受信 3.7k
    トークン、26.5 tok/s で約 140 秒。`num_predict` はそこへ効く上限で、**失敗を安く切る**
    ためのレバー。合否そのものは変わらない（途中で切られた編集は適用されず fail になる）。
    """
    path = WORK / "aider.model.settings.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"- name: ollama_chat/{model}", "  edit_format: diff",
             "  use_repo_map: false", "  extra_params:", f"    num_ctx: {num_ctx}"]
    if num_predict > 0:
        lines.append(f"    num_predict: {num_predict}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def aider_argv(task: dict) -> "list[str]":
    """aider を worker として 1 回だけ回す argv（`agents/aider.json` を読んで組む）。

    定義に無いのは 2 つだけ——テストのある課題の `--test-cmd` + `--auto-test`（課題ごとに
    違う）と、探索が要る課題の `--map-tokens`（定義は 0 で固定し、必要な課題だけ上書く）。
    ここを外すと aider を編集器としてしか測らないことになる。
    """
    agentcli, spec = _load_aider_spec()
    built = agentcli.headless_cmd(spec, MODEL, task["goal"],
                                  files=task.get("files") or (),
                                  read_files=task.get("read") or ())
    argv = built["argv"]
    extra = []
    if NUM_PREDICT > 0:
        extra += ["--model-settings-file", str(aider_settings(MODEL, num_predict=NUM_PREDICT))]
    if task.get("map_tokens"):
        # 定義の `--map-tokens 0` を**消してから**置き換える。同じフラグを 2 回並べて
        # 後勝ちに賭けると、定義側が並び順を変えた日に静かに 0 へ戻る。
        drop = argv.index("--map-tokens")
        argv = argv[:drop] + argv[drop + 2:]
        extra += ["--map-tokens", str(task["map_tokens"])]
    if task.get("test_cmd"):
        extra += ["--test-cmd", task["test_cmd"], "--auto-test"]
    # 追補は --message より前に置く（プロンプトは argv の末尾で受ける契約）。
    return argv[:-2] + extra + argv[-2:]


def _aider_argv_legacy(task: dict) -> "list[str]":
    """写しで組んでいた版（定義ファイル導入前）。比較用に残す。"""
    argv = ["aider", "--model", f"ollama_chat/{MODEL}",
            "--model-settings-file", str(aider_settings(MODEL)),
            # リポジトリマップは既定で切る（1,777 ファイルのマップだけで文脈が尽きる）。
            # 探索が要る課題だけ課題側で予算を与える。
            "--map-tokens", str(task.get("map_tokens", 0)),
            "--no-auto-commits", "--yes-always",
            "--no-check-update", "--analytics-disable", "--no-stream", "--no-pretty",
            "--no-gitignore"]
    # aider は「チャットに入っているファイル」しか編集しない。渡さないと本文で
    # 「ファイルを追加してくれ」と要求して終わる——`--message` は一発なので、
    # 答える人がいない＝課題に着手すらしない（実測で T2 が 3/3 これだった）。
    for path in task.get("read") or ():
        argv += ["--read", path]
    if task.get("test_cmd"):
        argv += ["--test-cmd", task["test_cmd"], "--auto-test"]
    return argv + list(task.get("files") or ()) + ["--message", task["goal"]]


def classify(rc: int, wall: float, out: str, err: str) -> str:
    """失敗様式のラベル。台帳を後から数えられるようにする。"""
    if wall >= WALL_LIMIT:
        return "timeout"
    if rc != 0:
        return "cli_error"
    if not out.strip():
        return "empty"
    if '"ok": false' in out or '"ok":false' in out:
        return "self_reported_incomplete"
    if "max_rounds" in err or "ラウンド上限" in err:
        return "max_rounds"
    return "returned"


def run_one(tid: str, i: int) -> dict:
    task = TASKS[tid]
    wt = WORK / f"{tid}-{i}"
    if wt.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=REPO, capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)
    # 登録だけ残った worktree を掃除してから足す。WORK ごと消して測り直すのは普通の
    # 手順なので、その次の run が「already registered」で死ぬのを毎回踏む。
    subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(wt), "HEAD"],
                   cwd=REPO, capture_output=True, text=True, check=True)
    task["seed"](wt)
    # aider は自前のシステムプロンプトと編集ループを持つので、flow-worker の
    # プロンプト（報告契約・worktree 規約）は渡さない——渡すと道具の作法と二重になる。
    # 課題文（goal）とチェッカーは両経路で同一なので、比較は成立する。
    prompt = "" if CLI == "aider" else build_prompt(task)

    started = time.time()
    try:
        argv = aider_argv(task) if CLI == "aider" else ["agent-ollama", MODEL, *WRITE_ARGS]
        p = subprocess.run(argv, input=prompt, cwd=wt,
                           env={**os.environ, "OLLAMA_API_BASE": OLLAMA_API_BASE},
                           capture_output=True, text=True, timeout=WALL_LIMIT)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    wall = time.time() - started

    mode = classify(rc, wall, out, err)
    try:
        ok, note = task["check"](wt)
    except Exception as e:  # noqa: BLE001 — チェッカーの事故は fail 扱いで記録
        ok, note = False, f"checker error: {e}"
    log = ""
    for line in err.splitlines():
        if line.startswith("@agent-log"):
            log = line.split(None, 1)[-1]
    rec = dict(task=tid, iter=i, cli=CLI, model=MODEL, num_predict=NUM_PREDICT, ok=ok, mode=mode,
               wall=round(wall, 1), note=note, log=log, out_chars=len(out))
    print(f"  {tid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:24s} "
          f"{wall:6.1f}s  {note[:70]}", flush=True)
    return rec


def main() -> None:
    global WALL_LIMIT, MODEL, CLI, NUM_PREDICT
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL,
                    help="測るモデル。別モデルの判定はここだけ変えればよい")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--tasks", default="T1,T2,T3")
    ap.add_argument("--wall", type=float, default=WALL_LIMIT,
                    help="1 run の壁時計上限（既定は agent_timeout の 600 秒）")
    ap.add_argument("--cli", default=CLI, choices=("agent-ollama", "aider"),
                    help="worker として回すエージェント層。道具の作法はそれぞれのものを使う")
    ap.add_argument("--num-predict", type=int, default=NUM_PREDICT,
                    help="1 ターンの生成上限（aider 経路のみ・0 で無効）。"
                         "収束しない課題の壁時計を切るレバー")
    args = ap.parse_args()
    WALL_LIMIT = args.wall
    MODEL = args.model
    CLI = args.cli
    NUM_PREDICT = args.num_predict

    WORK.mkdir(parents=True, exist_ok=True)
    ledger = WORK / "ledger.jsonl"
    tids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    if CLI == "aider":
        # aider には本番の定義が無い（agents/aider.json は未作成）。写しではなく
        # 「まだ正典が無い」ので、起動行に組み立てた argv をそのまま出して測定条件を残す。
        sample = " ".join(aider_argv(TASKS[tids[0]])[:-2])
        print(f"model={MODEL} cli=aider argv={sample} …（出所: 定義ファイル未作成）")
    else:
        print(f"model={MODEL} cli={CLI} argv={' '.join(WRITE_ARGS)} "
              f"（出所: {WRITE_ARGS_SOURCE}）")
    print(f"wall_limit={WALL_LIMIT:.0f}s tasks={tids} repeat={args.repeat}\n")

    rows = []
    for tid in tids:
        for i in range(1, args.repeat + 1):
            rec = run_one(tid, i)
            rows.append(rec)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("\n=== 受入率（決定的チェッカー）")
    for tid in tids:
        r = [x for x in rows if x["task"] == tid]
        n = len(r); ok = sum(1 for x in r if x["ok"])
        walls = sorted(x["wall"] for x in r)
        print(f"  {tid}: {ok}/{n}  中央値 {walls[len(walls)//2]:.0f}s  "
              f"様式 {sorted(set(x['mode'] for x in r))}")
    ok = sum(1 for x in rows if x["ok"])
    print(f"  合計: {ok}/{len(rows)}")
    print(f"\n台帳: {ledger}")


if __name__ == "__main__":
    os.environ.setdefault("AGENT_OLLAMA_THINK", "off")
    main()
