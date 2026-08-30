#!/usr/bin/env python3
"""読解・分析系タスク（要約・抽出・分析・提案・評価レビュー）の実力を測る。

worker_eval.py / judge_eval.py の型を継ぐ——決定的チェッカーだけで合否を出し、
走行中に判定役（LLM）を呼ばない。正解は入力を作る規則から従う（構成的ラベル。
ログの ERROR 行・植え込んだバグ行・一意最適の組合せなど、入力を組んだ時点で
正解が決まっているものだけを問う）。`--model` の差し替えだけで別モデルを測れる。

コード系（worker_eval）との違いは対象だけで、規律は同じ:
  - argv: agents/ollama-json.json の command（起動時に**読む**。写さない）
  - プロンプト: flow-worker スキルの prompt.py（role=worker, kind=work）
  - 出力契約: すべて JSON **オブジェクト**（ollama の JSON モードは配列を返せない）
  - チェッカーは selfcheck で LLM 抜きに検証できる

使い方: python3 text_eval.py [--model gemma4:12b] [--repeat 3] [--cases EX1,SM1]
        [--selfcheck]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402

extract_json = engine.extract_json

PROMPT_BUILDER = Path(os.environ.get(
    "FLOW_WORKER_PROMPT",
    str(Path.home() / ".claude/skills/flow-worker/scripts/prompt.py")))
LEDGER_DIR = Path(os.environ.get("TEXT_EVAL_DIR", "/tmp/agent-text-eval"))
MODEL = "gemma4:12b"
WALL_LIMIT = 600.0
THINK_OVERRIDE = ""         # --think で上書き（空 = 定義の値のまま）
REPAIR = False              # --repair: 機械検査の診断を付けた 1 回再投入（P5 / E6）
_FALLBACK_CMD = ["agent-ollama", "--think", "off", "--format", "json", "{model}"]

CMD, CMD_SOURCE = engine.load_cmd("ollama-json", _FALLBACK_CMD)
CMD_ENV = engine.load_env("ollama-json")

# ------------------------------------------------------------------ 素材
# 正解が構成から従うように、正解トークンはここで 1 度だけ定義して素材へ流し込む。

EX1_ERRORS = [("09:14:22", "ingest", "E102"), ("09:31:05", "aggregate", "E217"),
              ("10:02:48", "ingest", "E102"), ("10:44:11", "notify", "E509")]

EX1_LOG = """09:02:10 INFO  boot    サービス起動 (rev 8f31c02)
09:05:33 INFO  ingest  取り込み開始 batch=771
09:14:22 ERROR ingest  code=E102 入力スキーマの不一致で 12 行を破棄
09:14:23 INFO  ingest  リトライはしない方針 (no error retry policy)
09:20:41 WARN  quota   使用率 81% に到達
09:31:05 ERROR aggregate code=E217 日次集計で桁あふれ。partition=2026-08-12
09:31:06 INFO  aggregate 該当 partition を隔離した
09:47:52 INFO  render  レポート生成 3 件
10:02:48 ERROR ingest  code=E102 入力スキーマの不一致で 4 行を破棄
10:15:09 WARN  notify  Webhook 応答が 2.9 秒 (しきい値 3.0)
10:30:00 INFO  audit   error 件数の定期監査を実行（この行は ERROR ではない）
10:44:11 ERROR notify  code=E509 Webhook が 404 を返した
10:44:12 INFO  notify  代替チャネルへ切り替えた
11:00:00 INFO  boot    定期ヘルスチェック OK"""

EX2_TICKET = """[移行チケット #4821]
現行の collector は v2 系 (2.14.1) で稼働している。前回 2.13.4 へ切り戻した障害の
再発防止を踏まえ、今回は段階移行とする。移行先は 3.0.2。旧エンドポイント
https://api.example.com/v2/ingest は 12 月に廃止予定のため、新エンドポイント
https://api.example.com/v3/ingest へ向け先を変更する。切り替え期限は 2026-09-30。
検証環境では 3.1.0-rc1 も試したが、本番へは持ち込まない。"""

SM1_INCIDENT = """[インシデント記録 INC-2093]
14:00 監視が billing-api の 5xx 急増を検知。エラーレート 0.2% -> 38%
14:06 影響範囲の特定開始。checkout-web と invoice-batch にも波及
14:11 billing-api の TLS 証明書が 14:00 に期限切れとなっていたことを確認
14:18 証明書を再発行しローリング再起動を開始
14:33 全ポッド入れ替え完了。エラーレート平常値へ復帰
14:42 クローズ。ユーザー影響時間は 14:00-14:42 のうちサービス断だった 42 分
振り返り: (1) 証明書期限の監視アラートを 30 日前に設定する
(2) 再発行手順を runbook 化する (3) ローリング再起動の所要 15 分を短縮する"""

SM2_CHANGELOG = """v4.2.0 の変更点 (10 件):
1. 取り込みバッファを 64MB へ拡大
2. render の並列度を 4 へ変更
3. 【破壊的変更】Python 3.9 サポートを終了。3.10 以上が必須
4. notify の Webhook リトライを 3 回へ
5. config の環境変数展開に対応
6. aggregate の桁あふれを修正 (#217)
7. ログ形式を JSON Lines へ統一
8. quota 超過時のエラーメッセージを改善
9. 依存ライブラリ requests を 2.32 へ更新
10. ドキュメントの誤記修正"""

AN1_TABLE = """変種ごとの実測 (受入=受入数/実行数, wall=中央値秒, cost=1 run の推定クレジット):
v1: 受入 12/20, wall 88, cost 0.0
v2: 受入 18/20, wall 140, cost 4.2
v3: 受入 25/30, wall 95, cost 2.1
v4: 受入 27/30, wall 210, cost 1.4
v5: 受入 13/20, wall 60, cost 0.0"""

AN2_TIMELINE = """[デプロイ記録]
10:02 svc-auth 1.4.0 をデプロイ
10:17 svc-payment 2.1.3 をデプロイ
10:31 svc-search 0.9.9 をデプロイ
[依存関係] checkout は svc-payment と svc-auth を呼ぶ。svc-search は checkout から独立。
[メトリクス] checkout のエラーレート: 10:00 0.1% / 10:10 0.1% / 10:19 9% / 10:25 14% /
10:35 15%。svc-auth 単体の直接呼び出しは 10:00-10:40 を通じて正常 (0.1%)。"""

PR1_MENU = """施策一覧 (cost=工数人日, effect=期待削減時間/月):
p1: 取り込みの重複排除     cost 4, effect 50
p2: レポートのキャッシュ   cost 5, effect 45
p3: 通知のバッチ化         cost 3, effect 30
p4: 集計の増分化           cost 6, effect 70
p5: 実行環境のプール化     cost 7, effect 60"""

# 行番号は植え込みバグの正解。コードを変えたら必ず追従させる（selfcheck が守る）。
RV1_BUG_LINES = {1, 4, 15}
RV1_CODE = """ 1: def load_scores(path, cache=[]):
 2:     with open(path) as f:
 3:         rows = [line.split(",") for line in f]
 4:     for i in range(len(rows) - 1):
 5:         cache.append((rows[i][0], int(rows[i][1])))
 6:     return cache
 7:
 8: def average(scores):
 9:     total = 0
10:     for _, v in scores:
11:         total += v
12:     return total / len(scores)
13:
14: def top_n(scores, n):
15:     ordered = sorted(scores, key=lambda kv: kv[1])
16:     return ordered[:n]"""
# 1: 可変デフォルト引数 cache=[] が呼び出し間で蓄積 / 4: 末尾行を取りこぼす
# off-by-one / 15: 昇順 sort で「下位 n 件」を返す。12 の空リスト除算も実バグだが
# 植え込みではないので加点しない（±1 許容と件数上限 6 の範囲で無害）。

RV2_SPEC = """[仕様]
S1: 取り込み API のタイムアウトは 30 秒
S2: 認証トークンの有効期限は 24 時間
S3: レコード削除は論理削除 (deleted_at を立てる)
S4: レポートの既定の並び順は日付降順
S5: リトライは最大 3 回で指数バックオフ
S6: 監査ログは 90 日保持
[実装メモ]
取り込み API は 30 秒でタイムアウトする。認証トークンは発行から 12 時間で失効する。
削除 API は行を DELETE 文で物理削除する。レポートは日付降順で返す。リトライは
最大 3 回・指数バックオフ。監査ログは 90 日保持する。"""

# ------------------------------------------------------------------ チェッカー
# すべて (ok, note) を返す。data は本番と同じ extract_json の結果（None = 抽出不能）。


def _digits(text: str) -> set:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _as_int(value):
    """数値フィールドの型寛容（"42" も 42 も受ける）。測るのは読解であって型付けではない。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def check_ex1(data):
    if not isinstance(data, dict) or not isinstance(data.get("errors"), list):
        return False, "errors 配列が無い"
    got = set()
    for e in data["errors"]:
        if isinstance(e, dict):
            got.add((str(e.get("time", "")).strip(), str(e.get("module", "")).strip(),
                     str(e.get("code", "")).strip()))
    want = set(EX1_ERRORS)
    if got == want:
        return True, f"ERROR {len(want)} 件を過不足なく抽出"
    return False, f"不足 {sorted(want - got)} 余分 {sorted(got - want)}"[:200]


def check_ex2(data):
    if not isinstance(data, dict):
        return False, "オブジェクトでない"
    want = {"current_version": "2.14.1", "target_version": "3.0.2",
            "endpoint": "https://api.example.com/v3/ingest", "deadline": "2026-09-30"}
    bad = [k for k, v in want.items() if str(data.get(k, "")).strip() != v]
    if not bad:
        return True, "4 フィールド一致"
    return False, f"不一致: {', '.join(f'{k}={data.get(k)!r}' for k in bad)}"[:200]


def check_sm1(data):
    if not isinstance(data, dict):
        return False, "オブジェクトでない"
    root = str(data.get("root_cause", ""))
    if "証明書" not in root and "TLS" not in root.upper():
        return False, f"root_cause が証明書に触れていない: {root!r}"[:200]
    if str(data.get("affected_service", "")).strip() != "billing-api":
        return False, f"affected_service={data.get('affected_service')!r}"
    if _as_int(data.get("impact_minutes")) != 42:
        return False, f"impact_minutes={data.get('impact_minutes')!r}（期待 42）"
    items = data.get("action_items")
    if not isinstance(items, list) or not (1 <= len(items) <= 5) or \
            not all(isinstance(x, str) and x.strip() for x in items):
        return False, "action_items が 1〜5 件の文字列リストでない"
    return True, "根本原因・影響サービス・影響時間・後続アクションを充足"


def check_sm2(data):
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return False, "summary が無い"
    s = data["summary"]
    if len(s) > 220:
        return False, f"長すぎる（{len(s)} 文字 > 220）"
    if "3.9" not in s:
        return False, "唯一の破壊的変更（Python 3.9 終了）に触れていない"
    ghost = _digits(s) - _digits(SM2_CHANGELOG)
    if ghost:
        return False, f"素材に無い数値が混入: {sorted(ghost)}"
    return True, f"{len(s)} 文字・破壊的変更に言及・数値の捏造なし"


def check_an1(data):
    got = str((data or {}).get("winner", "")).strip().lower() if isinstance(data, dict) else ""
    if got == "v4":
        return True, "winner=v4（受入率 0.90 同率 → cost で v2 を下す）"
    return False, f"winner={got!r}（期待 v4）"


def check_an2(data):
    cause = str((data or {}).get("cause", "")) if isinstance(data, dict) else ""
    if "svc-payment" in cause and "2.1.3" in cause:
        return True, f"cause={cause!r}"
    return False, f"cause={cause!r}（期待 svc-payment@2.1.3）"


def check_pr1(data):
    if not isinstance(data, dict) or not isinstance(data.get("picks"), list):
        return False, "picks 配列が無い"
    picks = {str(p).strip().lower() for p in data["picks"]}
    if picks != {"p1", "p4"}:
        return False, f"picks={sorted(picks)}（期待 p1+p4=効果 120/予算 10）"
    if _as_int(data.get("total_cost")) != 10:
        return False, f"total_cost={data.get('total_cost')!r}（期待 10）"
    return True, "一意最適 p1+p4 を選定・コスト計算も一致"


def check_rv1(data):
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return False, "findings 配列が無い"
    finds = data["findings"]
    if len(finds) > 6:
        return False, f"指摘 {len(finds)} 件は多すぎる（乱れ撃ちを疑う）"
    lines = set()
    for f in finds:
        if isinstance(f, dict) and str(f.get("line", "")).strip().isdigit():
            lines.add(int(str(f["line"]).strip()))
    hit = {b for b in RV1_BUG_LINES if any(abs(b - x) <= 1 for x in lines)}
    if len(hit) >= 2:
        return True, f"植え込み 3 件中 {len(hit)} 件を検出（指摘 {len(finds)} 件）"
    return False, f"検出 {len(hit)}/3 件のみ（指摘行 {sorted(lines)}）"


def check_rv2(data):
    if not isinstance(data, dict) or not isinstance(data.get("violations"), list):
        return False, "violations 配列が無い"
    got = {str(v).strip().upper() for v in data["violations"]}
    if got == {"S2", "S3"}:
        return True, "違反 S2/S3 を過不足なく特定"
    return False, f"violations={sorted(got)}（期待 S2,S3）"


# ------------------------------------------------------------------ ケース定義

REQUEST = "運用ログ・チケット・実測データを読み解き、後続の判断に使える形へ整理する。"
_JSON_ONLY = "出力は指定した JSON オブジェクトだけとし、他のテキストを含めない。"

CASES = {
    # --- 抽出系
    "EX1": dict(genre="extract", expect="ERROR 4 件の (time,module,code)",
                goal=("次のサービスログから ERROR 行だけをすべて抜き出す。"
                      '出力は JSON {"errors":[{"time":"HH:MM:SS","module":"...",'
                      '"code":"E999"},...]}。ERROR 以外の行を含めないこと。'
                      + _JSON_ONLY + "\n\n" + EX1_LOG),
                check=check_ex1),
    "EX2": dict(genre="extract", expect="4 フィールドの正確な値",
                goal=("次の移行チケットから、現行バージョン・移行先バージョン・"
                      "新エンドポイント・切り替え期限を抜き出す。出力は JSON "
                      '{"current_version":"...","target_version":"...",'
                      '"endpoint":"...","deadline":"YYYY-MM-DD"}。'
                      + _JSON_ONLY + "\n\n" + EX2_TICKET),
                check=check_ex2),
    # --- 要約系
    "SM1": dict(genre="summarize", expect="根本原因/影響サービス/42 分/アクション",
                goal=("次のインシデント記録を構造化して要約する。出力は JSON "
                      '{"root_cause":"...","affected_service":"...",'
                      '"impact_minutes":<整数>,"action_items":["..."]}。'
                      "affected_service は最初に障害が起きたサービス名 1 つ。"
                      "impact_minutes はユーザー影響時間（分・整数）。"
                      + _JSON_ONLY + "\n\n" + SM1_INCIDENT),
                check=check_sm1),
    "SM2": dict(genre="summarize", expect="220 字以内・破壊的変更に言及・数値捏造なし",
                goal=("次の変更点一覧を 220 文字以内で要約する。利用者が読んで"
                      "アップグレード可否を判断できることを最優先とする。出力は JSON "
                      '{"summary":"..."}。' + _JSON_ONLY + "\n\n" + SM2_CHANGELOG),
                check=check_sm2),
    # --- 分析系
    "AN1": dict(genre="analyze", expect="v4（受入率同率→コストで判定）",
                goal=("次の実測から最良の変種を 1 つ選ぶ。基準は (1) 受入率が最も高いこと "
                      "(2) 同率のときは cost が低いこと、の順で適用する。出力は JSON "
                      '{"winner":"v?","reason":"..."}。'
                      + _JSON_ONLY + "\n\n" + AN1_TABLE),
                check=check_an1),
    "AN2": dict(genre="analyze", expect="svc-payment@2.1.3",
                goal=("次の記録から checkout のエラー急増の原因になったデプロイを特定する。"
                      '出力は JSON {"cause":"<サービス名>@<バージョン>","reason":"..."}。'
                      + _JSON_ONLY + "\n\n" + AN2_TIMELINE),
                check=check_an2),
    # --- 提案系
    "PR1": dict(genre="propose", expect="p1+p4（予算 10 で効果 120 の一意最適）",
                goal=("工数予算 10 人日で効果（削減時間）の合計が最大になる施策の組合せを"
                      "提案する。出力は JSON "
                      '{"picks":["p?",...],"total_cost":<整数>,"reason":"..."}。'
                      + _JSON_ONLY + "\n\n" + PR1_MENU),
                check=check_pr1),
    # --- 評価・レビュー系
    "RV1": dict(genre="review", expect="植え込み 3 バグ中 2 件以上・乱れ撃ちなし",
                goal=("次の Python コードをレビューし、動作上のバグだけを指摘する。"
                      "スタイルの指摘は書かない。出力は JSON "
                      '{"findings":[{"line":<行番号>,"issue":"..."},...]}。'
                      + _JSON_ONLY + "\n\n" + RV1_CODE),
                check=check_rv1),
    "RV2": dict(genre="review", expect="違反 S2/S3 の特定",
                goal=("次の仕様と実装メモを突き合わせ、実装が仕様に違反している項目を"
                      '特定する。出力は JSON {"violations":["S?",...],"reason":"..."}。'
                      "違反していない項目を含めないこと。"
                      + _JSON_ONLY + "\n\n" + RV2_SPEC),
                check=check_rv2),
}

# ------------------------------------------------------------------ 実行


def build_prompt(case: dict) -> str:
    payload = {"role": "worker", "kind": "work", "goal": case["goal"],
               "request": REQUEST, "deps": {}, "repo_instruction": "",
               "artifact_note": "", "workspace": {}, "references": [],
               "instructions": "", "repair_note": "", "read_note": ""}
    r = subprocess.run([sys.executable, str(PROMPT_BUILDER)],
                       input=json.dumps(payload, ensure_ascii=False),
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"prompt.py が失敗: {r.stderr[:300]}")
    return r.stdout.strip()


def call(prompt: str) -> "tuple[int, str, str, float]":
    raw = list(CMD)
    if THINK_OVERRIDE and "--think" in raw:
        raw[raw.index("--think") + 1] = THINK_OVERRIDE
    argv = [a.replace("{model}", MODEL) for a in raw]
    # 上限は group ごと（engine.run_process）。孫（エージェント CLI・推論クライアント）を
    # 残すと次の実行が順番待ちになる。経過は monotonic——壁時計はマシンのスリープを含む
    # ので、上限内に終わった実行を timeout と記録していた（実測 2026-08-29〜30）。
    started = time.monotonic()
    try:
        p = engine.run_process(argv, input=prompt, capture_output=True, text=True,
                               timeout=WALL_LIMIT, env={**os.environ, **CMD_ENV})
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.monotonic() - started


def _judge_output(case: dict, rc: int, out: str, err: str, wall: float):
    """1 回の応答を (data, mode, ok, note) へ判定する（再投入の前後で同じ規則）。"""
    data = None
    # 上限超過は**打ち切った事実**（rc と TIMEOUT マーカー）で判定する。壁時計との比較は
    # マシンのスリープを含むので、上限内に終わった実行を timeout と記録していた
    # （実測 2026-08-30: 受入 PASS のまま mode=timeout）。
    if rc == -1 and "TIMEOUT" in (err or ""):
        mode, ok, note = "timeout", False, f"上限超過（{WALL_LIMIT:.0f}s で打ち切り）"
    elif rc != 0:
        mode, ok, note = "cli_error", False, (err.strip()[-120:] or f"rc={rc}")
    elif not out.strip():
        mode, ok, note = "empty", False, "本文が空"
    else:
        try:
            data = extract_json(out)
        except Exception as e:  # noqa: BLE001 — 本番も同じ失敗をする（そこが測定対象）
            mode, ok, note = "unparsable", False, f"JSON を抽出できない: {e}"
        else:
            ok, note = case["check"](data)
            mode = "correct" if ok else "wrong"
    return data, mode, ok, note


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    prompt = build_prompt(case)
    rc, out, err, wall = call(prompt)
    data, mode, ok, note = _judge_output(case, rc, out, err, wall)
    repaired = False
    # 制約つき生成のゲート（P5 / 実装計画 E6）: 機械検査の不合格理由（note＝チェッカーの
    # 決定的診断）を付けて 1 回だけ再投入する。statemachine の check 再投入と同じ運び方で、
    # 「決定的に測れる制約は機械検査 + 再投入で受ける」を同じハーネスで確かめる。
    if REPAIR and not ok and mode in ("wrong", "unparsable") and wall < WALL_LIMIT:
        fix = (f"{prompt}\n\n[機械検査の結果、前回の出力は不合格でした]\n"
               f"前回の出力（先頭 400 文字）: {out[:400]}\n"
               f"不合格の理由: {note}\n"
               "理由を解消し、指示された JSON だけを再出力してください。")
        r_rc, r_out, r_err, r_wall = call(fix)
        wall += r_wall
        repaired = True
        data, mode, ok, note = _judge_output(case, r_rc, r_out, r_err, r_wall)
        mode += "_after_repair"
        if r_rc == 0 and r_out.strip():
            out = r_out
    rec = dict(case=cid, genre=case["genre"], iter=i, model=MODEL, ok=ok, mode=mode,
               repaired=repaired,
               # 実効条件を台帳へ。空 = 定義の値のまま（判定系の過去の数字はすべて
               # think off で取られているので、腕を混ぜないためにここが要る）。
               think_override=THINK_OVERRIDE or None,
               wall=round(wall, 1), note=note, prompt_chars=len(prompt),
               out_chars=len(out),
               answer=json.dumps(data, ensure_ascii=False, default=str)[:200])
    if engine.missing():
        rec["engine_missing"] = engine.missing()
    print(f"  {cid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:10s} {wall:6.1f}s  {note[:66]}",
          flush=True)
    return rec


# ------------------------------------------------------------------ selfcheck


def selfcheck() -> int:
    """チェッカーを LLM 抜きで検証する（正解は通り、典型的な外し方は落ちる）。"""
    good = {
        "EX1": {"errors": [{"time": t, "module": m, "code": c} for t, m, c in EX1_ERRORS]},
        "EX2": {"current_version": "2.14.1", "target_version": "3.0.2",
                "endpoint": "https://api.example.com/v3/ingest", "deadline": "2026-09-30"},
        "SM1": {"root_cause": "TLS 証明書の期限切れ", "affected_service": "billing-api",
                "impact_minutes": 42, "action_items": ["期限監視を 30 日前に設定",
                                                       "再発行手順の runbook 化"]},
        "SM2": {"summary": "v4.2.0 の要点: Python 3.9 サポート終了（3.10 以上必須）が"
                           "唯一の破壊的変更。ほかは取り込み・並列度・リトライ等の改善。"},
        "AN1": {"winner": "v4", "reason": "0.90 同率のうち cost 最小"},
        "AN2": {"cause": "svc-payment@2.1.3", "reason": "依存とタイミングが一致"},
        "PR1": {"picks": ["p1", "p4"], "total_cost": 10, "reason": "効果 120 で最大"},
        "RV1": {"findings": [{"line": 4, "issue": "off-by-one"},
                             {"line": 1, "issue": "可変デフォルト引数"},
                             {"line": 15, "issue": "昇順 sort で下位を返す"}]},
        "RV2": {"violations": ["S2", "S3"], "reason": "12h と物理削除"},
    }
    bad = {
        "EX1": {"errors": [{"time": "10:30:00", "module": "audit", "code": "E000"}]},
        "EX2": {"current_version": "2.13.4", "target_version": "3.1.0-rc1",
                "endpoint": "https://api.example.com/v2/ingest", "deadline": "2026-12-01"},
        "SM1": {"root_cause": "デプロイ失敗", "affected_service": "checkout-web",
                "impact_minutes": 33, "action_items": []},
        "SM2": {"summary": "v5.0 で 128MB へ拡大し Python 3.8 を終了。" * 10},
        "AN1": {"winner": "v2", "reason": "受入数がまず多い"},
        "AN2": {"cause": "svc-search@0.9.9", "reason": "直近のデプロイ"},
        "PR1": {"picks": ["p4", "p5"], "total_cost": 13, "reason": "効果の大きい 2 つ"},
        "RV1": {"findings": [{"line": 10, "issue": "総和"}, {"line": 2, "issue": "open"},
                             {"line": 6, "issue": "return"}, {"line": 11, "issue": "+="},
                             {"line": 16, "issue": "slice"}, {"line": 3, "issue": "split"},
                             {"line": 12, "issue": "除算"}]},
        "RV2": {"violations": ["S2", "S3", "S6"], "reason": "アーカイブ移動も違反とみなした"},
    }
    # PR1 の「一意最適」を総当たりで検算する（素材を変えたら正解が変わるため）。
    menu = {"p1": (4, 50), "p2": (5, 45), "p3": (3, 30), "p4": (6, 70), "p5": (7, 60)}
    best = max((combo for combo in _powerset(menu)
                if sum(menu[p][0] for p in combo) <= 10),
               key=lambda c: sum(menu[p][1] for p in c))
    assert set(best) == {"p1", "p4"}, f"PR1 の最適が仕込みとずれている: {best}"

    failed = 0
    for cid, case in CASES.items():
        ok, note = case["check"](good[cid])
        if not ok:
            print(f"  NG {cid}: 正解が落ちた — {note}")
            failed += 1
        ok, note = case["check"](bad[cid])
        if ok:
            print(f"  NG {cid}: 典型的な外し方が通った — {note}")
            failed += 1
    print(f"selfcheck: {'OK' if not failed else f'{failed} 件 NG'}（{len(CASES)} ケース×正誤）")
    return 1 if failed else 0


def _powerset(menu):
    items = list(menu)
    for mask in range(1, 1 << len(items)):
        yield [p for i, p in enumerate(items) if mask >> i & 1]


# ------------------------------------------------------------------ main


def main() -> None:
    global MODEL, WALL_LIMIT, THINK_OVERRIDE, REPAIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--wall", type=float, default=WALL_LIMIT)
    ap.add_argument("--repair", action="store_true",
                    help="不合格時に機械検査の診断を付けて 1 回だけ再投入する（P5 のゲート形）")
    ap.add_argument("--selfcheck", action="store_true",
                    help="チェッカーの自己検証だけを行う（LLM を呼ばない）")
    ap.add_argument("--think", choices=("on", "off", "prompt"), default="",
                    help="評価専用: 定義中の --think 値を上書きする。"
                         "prompt は system prompt 先頭の <|think|> 方式（Gemma 4 系の作法）で、"
                         "API フィールドとは経路が違うため --format と併用できる。"
                         "レビュー（RV1/RV2）の網羅性がここで動くかを測る（計画 P10）")
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL = args.model
    WALL_LIMIT = args.wall
    THINK_OVERRIDE = args.think
    REPAIR = args.repair

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    cids = [c.strip() for c in args.cases.split(",") if c.strip()]
    print(f"model={MODEL} argv={' '.join(CMD)}（出所: {CMD_SOURCE}）")
    if engine.missing():
        print(f"engine 欠落: {engine.missing()}")
    if THINK_OVERRIDE:
        print(f"think={THINK_OVERRIDE}（評価専用の上書き。定義の値ではない）")
    print(f"wall_limit={WALL_LIMIT:.0f}s cases={cids} repeat={args.repeat}\n")

    rows = []
    for cid in cids:
        for i in range(1, args.repeat + 1):
            rec = run_one(cid, i)
            rows.append(rec)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("\n=== 正解率（決定的チェッカー）")
    for cid in cids:
        r = [x for x in rows if x["case"] == cid]
        n = len(r); ok = sum(1 for x in r if x["ok"])
        walls = sorted(x["wall"] for x in r)
        print(f"  {cid} ({CASES[cid]['genre']}): {ok}/{n}  "
              f"中央値 {walls[len(walls)//2]:.0f}s  様式 {sorted(set(x['mode'] for x in r))}")
    by_genre = {}
    for x in rows:
        by_genre.setdefault(x["genre"], []).append(x["ok"])
    for g, oks in sorted(by_genre.items()):
        print(f"  [{g}] {sum(oks)}/{len(oks)}")
    print(f"  合計: {sum(1 for x in rows if x['ok'])}/{len(rows)}")
    print(f"\n台帳: {ledger}")


if __name__ == "__main__":
    os.environ.setdefault("AGENT_OLLAMA_THINK", "off")
    main()
