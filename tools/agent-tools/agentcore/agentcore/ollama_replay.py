"""記録済みプロンプトのオフライン再生 — 品質を測るための口。

設計: docs/plans/2026-08-10-agent-ollama-quality-and-role-refit-proposals.md §3
（適用拡大設計が「検証はオフライン再生で行う（§10.3）」と指名しながら、その節が
存在しなかった箇所を埋める）。

## なぜ要るのか

段 0〜3 で本番へ入っている役割について、**測れているのは完走性だけ**だった——
「時間内に終わる」「壊れずに返る」は測ったが、「返した中身が正しいか」は 1 件も
測っていない。理由は測定手段の不在で、ライブ運用の台帳から測ろうとすると 1 件あたり
数十分の実行を焼くため、結局測られないまま来た。

そこで、**既に走った実行の JSONL から入力を取り出して再生する**。入力は記録済みなので
タスクを組み直す必要がなく、モデル・think・format を変えた腕（arm）を並べて同じ入力へ
当てられる。ライブ実行を焼かずに済むぶん、以降の判断が全部安くなる。

## 再生は道具を持たない（安全性の不変条件）

`--tools` の実行ログも入力源にできるが、**再生は常に道具なし（`run_plain`）で行う**。
記録されたコマンドを再実行しない——再生は測定であって、副作用の再現ではない。
道具ありの実行を「再生」と称してコマンドを撃ち直すと、測定のたびにワークスペースが
変わる。ここは設定で緩めない（腕の指定にツールの口を作らない）。

## 何が出るか

1 行 1 JSON の記録（case × arm）。`text` / `empty` / `tokens_*` / `duration_sec` を持つ。
集計 (`summarize`) は腕ごとの空応答率・失敗率と、**腕をまたいだ一致率**を出す。
同一設定の腕を複数並べれば自己一貫性（同じ入力を N 回引いたときのばらつき）が、
違う設定を並べれば設定間の一致が、同じ口で測れる。

正解ラベルとの一致率はここでは出さない——ラベルは人が付けるものであり、
本モジュールは「同じ入力に対する出力」を再現可能な形で並べるところまでを引き受ける。

標準ライブラリのみ（pip 依存なし）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from agentcore import ollama_events, ollama_loop

# 再生対象として拾う会話の役割。system は指示の一部で、assistant は出力なので入力ではない。
_PROMPT_ROLE = "user"


def new_record_path(now: "float | None" = None) -> Path:
    """再生記録の既定の置き場。実行ログと同じ場所へ、名前で見分けが付く形で置く。"""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(now if now is not None else time.time()))
    return ollama_events.log_dir() / f"replay-{stamp}.jsonl"


def _iter_log_files(target: "str | Path | None") -> "list[Path]":
    """再生元の JSONL を集める（ファイル 1 本・ディレクトリ・既定のログ置き場）。"""
    if target:
        path = Path(target).expanduser()
    else:
        path = ollama_events.log_dir()
    if path.is_file():
        return [path]
    try:
        return sorted(p for p in path.iterdir() if p.is_file() and p.suffix == ".jsonl")
    except OSError:
        return []


def _read_events(path: Path) -> "list[dict]":
    """JSONL を頭から全部読む（`read_status` と違い、末尾だけでは入力に届かない）。"""
    events: "list[dict]" = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except OSError:
        return []
    return events


def case_of(path: "str | Path") -> "dict | None":
    """1 本のログから再生できる 1 件を取り出す（取り出せなければ None）。

    入力は「最初の user メッセージ」= その実行へ渡されたプロンプト。道具ありの実行では
    後続の user メッセージがツール結果の差し戻しなので、**最初の 1 通だけ**を使う。
    """
    path = Path(path)
    events = _read_events(path)
    if not events:
        return None
    case: dict = {"log": str(path), "origin_model": "", "origin_mode": "",
                  "origin_think": "", "origin_format": "", "origin_status": "",
                  "started_at": 0.0, "prompt": ""}
    for event in events:
        kind = str(event.get("kind") or "")
        if kind == "run_start":
            case["origin_model"] = str(event.get("model") or "")
            case["origin_mode"] = str(event.get("mode") or "")
            case["origin_think"] = str(event.get("think") or "")
            case["origin_format"] = str(event.get("format") or "")
            case["started_at"] = float(event.get("ts") or 0.0)
        elif kind == "run_end":
            case["origin_status"] = str(event.get("status") or "")
        elif kind == "message" and not case["prompt"]:
            if str(event.get("role") or "") == _PROMPT_ROLE:
                case["prompt"] = str(event.get("content") or "")
    if not case["prompt"].strip():
        return None
    return case


def collect_cases(target: "str | Path | None" = None, *, limit: int = 0,
                  dedupe: bool = True) -> "list[dict]":
    """再生できる入力を集める。新しい実行から順に返す。

    同じプロンプトは既定でまとめる（`occurrences` に本数を持つ）。同一の planner
    プロンプトが何十本も残っているログ置き場を素直に舐めると、同じ測定を繰り返す
    だけで再生予算が溶けるため。
    """
    cases: "list[dict]" = []
    seen: "dict[str, dict]" = {}
    for path in _iter_log_files(target):
        case = case_of(path)
        if case is None:
            continue
        if dedupe:
            key = case["prompt"]
            if key in seen:
                seen[key]["occurrences"] += 1
                continue
            case["occurrences"] = 1
            seen[key] = case
        cases.append(case)
    cases.sort(key=lambda c: c.get("started_at") or 0.0, reverse=True)
    return cases[:limit] if limit and limit > 0 else cases


# ---------------------------------------------------------------------------
# 腕（arm）— 同じ入力へ当てる設定の 1 つ
# ---------------------------------------------------------------------------
class ArmError(ValueError):
    """腕の指定の誤り。"""


_ARM_KEYS = {"model", "think", "format", "label", "repeat"}


def parse_arm(spec: str) -> dict:
    """`model=qwen3,think=off,format=json,repeat=3` を腕へ。

    `think` は on/off だけ（既定に任せたいなら書かない）。`format` は json/array/text。
    `repeat` は同じ設定を何回引くか（自己一貫性の測定に使う）。
    """
    arm: dict = {"model": "", "think": None, "format": None, "label": "", "repeat": 1}
    for chunk in str(spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ArmError(f"腕の指定は key=value です: {chunk}")
        key, value = (part.strip() for part in chunk.split("=", 1))
        if key not in _ARM_KEYS:
            raise ArmError(f"腕の知らないキーです: {key}（{'/'.join(sorted(_ARM_KEYS))}）")
        if key == "think":
            lowered = value.lower()
            if lowered not in ("on", "off"):
                raise ArmError(f"think は on か off です: {value}")
            arm["think"] = lowered == "on"
        elif key == "format":
            lowered = value.lower()
            if lowered not in ("json", "array", "text"):
                raise ArmError(f"format は json か array か text です: {value}")
            # text は「強制しない」（API へフィールドを送らない）。アダプタ側と同じ意味。
            arm["format"] = lowered if lowered in ("json", "array") else None
        elif key == "repeat":
            try:
                arm["repeat"] = max(1, int(value))
            except ValueError:
                raise ArmError(f"repeat は整数です: {value}") from None
        else:
            arm[key] = value
    if not arm["label"]:
        arm["label"] = label_of(arm)
    return arm


def label_of(arm: dict) -> str:
    """腕の既定の名前（記録と集計の見出しになる）。"""
    parts = [arm.get("model") or "既定"]
    think = arm.get("think")
    parts.append("think=既定" if think is None else f"think={'on' if think else 'off'}")
    parts.append(f"format={arm.get('format') or 'text'}")
    return " ".join(parts)


def _default_generate(model: str, prompt: str, *, think, fmt) -> dict:
    """既定の 1 発生成。**道具は持たない**（このモジュールの不変条件）。"""
    return ollama_loop.run_plain(model, prompt, think=think, fmt=fmt)


def replay_case(case: dict, arm: dict, *, generate=None, attempt: int = 1) -> dict:
    """1 件 × 1 腕を再生して記録を作る。失敗は記録に残して次へ進む。

    失敗で全体を止めないのは、再生が**測定**だからである——1 件の env 失敗で
    残り全部の測定を捨てると、測るために測り直す羽目になる。
    """
    generate = generate or _default_generate
    model = arm.get("model") or case.get("origin_model") or ""
    record = {
        "log": case.get("log", ""),
        "arm": arm.get("label") or label_of(arm),
        "attempt": attempt,
        "model": model,
        "think": arm.get("think"),
        "format": arm.get("format") or "",
        "origin_mode": case.get("origin_mode", ""),
        "origin_status": case.get("origin_status", ""),
        "prompt_chars": len(case.get("prompt") or ""),
    }
    started = time.monotonic()
    try:
        result = generate(model, case.get("prompt") or "",
                          think=arm.get("think"), fmt=arm.get("format"))
    except Exception as exc:  # 測定は止めない（理由だけ残す）
        record.update({"ok": False, "error": str(exc), "kind_of": type(exc).__name__,
                       "text": "", "empty": True,
                       "duration_sec": round(time.monotonic() - started, 2)})
        return record
    text = str((result or {}).get("text") or "")
    record.update({
        "ok": True,
        "text": text,
        # 空応答は最初に潰した症状であり、設定を変えたときに**最初に再発する**ところ。
        # 一致率より前に、まずこれが 0 のままかを見る。
        "empty": not text.strip(),
        "tokens_in": int((result or {}).get("tokens_in") or 0),
        "tokens_out": int((result or {}).get("tokens_out") or 0),
        "duration_sec": round(time.monotonic() - started, 2),
    })
    return record


def replay(cases: "list[dict]", arms: "list[dict]", *, generate=None, on_record=None
           ) -> "list[dict]":
    """全件 × 全腕を再生する。`on_record` があれば 1 件ごとに渡す（逐次書き出し用）。"""
    records: "list[dict]" = []
    for case in cases:
        for arm in arms:
            for attempt in range(1, int(arm.get("repeat") or 1) + 1):
                record = replay_case(case, arm, generate=generate, attempt=attempt)
                records.append(record)
                if on_record is not None:
                    on_record(record)
    return records


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    """一致判定の正規形。JSON として読めるならキー順まで揃える。

    JSON 契約の役割では、キーの順や空白の差は**同じ答え**である。素の文字列比較だと
    それを不一致に数えてしまい、一致率が実態より低く出る。
    """
    stripped = str(text or "").strip()
    if not stripped:
        return ""
    try:
        return json.dumps(json.loads(stripped), ensure_ascii=False, sort_keys=True)
    except ValueError:
        return stripped


def summarize(records: "list[dict]") -> dict:
    """腕ごとの成績と、腕をまたいだ一致率。

    - `arms`: 腕ごとの 件数 / 失敗 / 空応答 / 中央値の所要秒 / 平均 tokens_out。
      空応答率と tokens の母数は**返ってきた呼び出し**（ok）の数で、失敗は混ぜない。
      中央値は偶数件なら上側を採る（診断用の目安であって統計値ではない）。
    - `agreement`: 同じ入力に対して**全腕・全試行の出力が一致した件数**の割合。
      同一設定の腕を repeat で並べれば自己一貫性、違う設定を並べれば設定間の一致になる。
    """
    arms: "dict[str, dict]" = {}
    for record in records:
        name = str(record.get("arm") or "")
        bucket = arms.setdefault(name, {"arm": name, "n": 0, "ok": 0, "failed": 0, "empty": 0,
                                        "durations": [], "tokens_out": 0})
        bucket["n"] += 1
        if not record.get("ok"):
            # 接続できなかった呼び出しは「空応答」ではない。混ぜると、サーバが落ちて
            # いるだけの日に空応答率が跳ね上がり、設定の良し悪しに見えてしまう。
            bucket["failed"] += 1
            continue
        bucket["ok"] += 1
        if record.get("empty"):
            bucket["empty"] += 1
        if record.get("duration_sec") is not None:
            bucket["durations"].append(float(record["duration_sec"]))
        bucket["tokens_out"] += int(record.get("tokens_out") or 0)

    summary_arms = []
    for bucket in arms.values():
        durations = sorted(bucket.pop("durations"))
        median = durations[len(durations) // 2] if durations else 0.0
        n = bucket["n"] or 1
        # 空応答率と tokens は**返ってきた呼び出しの中で**数える（母数は ok の数）。
        ok_n = bucket["ok"] or 1
        summary_arms.append(dict(
            bucket, duration_median_sec=round(median, 2),
            empty_pct=round(100.0 * bucket["empty"] / ok_n, 1),
            failed_pct=round(100.0 * bucket["failed"] / n, 1),
            tokens_out_avg=round(bucket["tokens_out"] / ok_n, 1)))
    summary_arms.sort(key=lambda item: item["arm"])

    by_case: "dict[str, list]" = {}
    for record in records:
        by_case.setdefault(str(record.get("log") or ""), []).append(record)
    # 1 通りしか出力が無い（= 比較対象が無い）件は一致率の母数に入れない。
    # 失敗した呼び出しは比較に使えない（出力が無い）。母数から外さないと、
    # 落ちた腕の分だけ「不一致」が積まれて一致率が実態より低く出る。
    usable = {log: [r for r in group if r.get("ok")] for log, group in by_case.items()}
    comparable = [group for group in usable.values() if len(group) > 1]
    agreed = sum(1 for group in comparable
                 if len({normalize(str(r.get("text") or "")) for r in group}) == 1)
    return {
        "cases": len(by_case),
        "records": len(records),
        "failed": sum(1 for r in records if not r.get("ok")),
        "arms": summary_arms,
        "agreement": {
            "comparable_cases": len(comparable),
            "agreed_cases": agreed,
            "agreement_pct": round(100.0 * agreed / len(comparable), 1) if comparable else 0.0,
        },
    }
