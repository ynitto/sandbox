"""agentcore.judge — 型付きの判断を確率つきで返す判断 AI（System One 型）の 1 実装。

## 何をするモジュールか

TypeSafe AI の Jev（2026-09-15 公開）が示した形——**文章を生成せず、決まった選択肢の上の
確率分布を返す**——を、LAN 上の ollama（既定 `gemma4:e4b`）で真似る。入力は
「状態（state）」と「名前付きの問い（questions）」、出力は問いごとの型付きの答え:

| 型 | 問いの形 | 答え |
|---|---|---|
| `choice` | `criteria`（選択肢 → 説明） | `choice` と `probabilities` |
| `boolean` | 問いだけ | `value` と `probability`（yes の確率） |
| `score` | 順序つきの `criteria` | `score`（確率加重）と `bucket`（最頻） |

## どうやって確率を出すか

生成させるのではなく、**最初の 1 トークンの確率分布を読む**（parallel readout over
fixed labels）。選択肢に A / B / C … の 1 文字ラベルを振り、「ラベルを 1 文字だけ書け」と
頼んで `logprobs` / `top_logprobs` を受け取る。ラベルに落ちた質量を正規化したものが
確率で、ラベル以外に漏れた質量は `coverage` として残す（1 − coverage が「どれでもない」
の目安。Jev の noul に相当する明示の選択肢は `other` で宣言できる）。

生成トークンは高々 4 つなので、走行時間は prefill でほぼ決まる。同じ状態への複数の問いは
**状態を先・問いを後**に並べて、ollama の接頭辞キャッシュに乗せる（案 D と同じ理屈）。

## どの実行で judge を使うか（設定ファイル `~/.agents/agent-herd.yaml` の `judge.model`）

既定（`auto`）では**ローカルの定義**（`relative_cost` が 0 の aider / ollama）で回している実行
だけが judge を使う——judge は LAN の ollama を直に叩くので、ollama の無い環境で勝手に叩かない。
クラウド CLI（Claude Code など）で回している実行では、判定（遷移条件・route・filter・assess）を
そのクラウドの生成経路に訊いていて、そこがトークンの出どころになる。

`judge.model: <モデル名>` を置くと、実行の定義に関係なく判定はその ollama モデルの judge へ
行く。判定は yes/no や選択肢の 1 文字で済むので、クラウドの高価なトークンを使う理由が無い。
`judge.model: off` なら judge を一切使わず、従来の生成経路に留まる。設定は
`agent-herd config set judge.model …`（agent-app の「設定 > 実行制御」も同じ口）で書く。
読み方は `agentcore.herdconfig`。

## ollama が logprobs を返さないとき

古い ollama は `logprobs` を知らない。応答に入っていても、こちらが読める形でないこと
がある（OpenAI 互換の `{"content": [...]}` など）。どちらも「読めなかった」として同じ
扱いにする——黙って「確率 1.0」を作らない。`samples` が 2 以上なら structured outputs
（`format` の enum）で複数回引いて票数を確率にする（`method: "vote"`）。1 回だけなら本文
のラベルを読み、`method: "text"` と `coverage: 0`、そして **`confidence: 0`** を返す
（読めたラベルは `choice` / `value` / `bucket` に残すが、確度は名乗らない）。`text` の答えは
`abstained()` がしきい値に関わらず棄権に入れる。

設計: docs/plans/2026-09-19-agent-herd-system-one-judge-design.md。
仕様: docs/specs/agent-herd-spec.md §5.5。
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request

from agentcore import herdconfig, ollama_loop

QUESTION_TYPES = ("choice", "boolean", "score")
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_OPTIONS = len(LABELS)
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_TOP_LOGPROBS = 20
# ラベル 1 文字の前に空白や改行が来ることがあるので、読む位置に少し余裕を持たせる。
DEFAULT_NUM_PREDICT = 4
BOOLEAN_CRITERIA = (("yes", "The answer is yes."), ("no", "The answer is no."))
OTHER_KEY = "other"

# `method` の語彙（呼び出し側が確率の信頼度を決めるための印）。
METHOD_LOGPROBS = "logprobs"   # 1 トークン目の分布を読んだ（本来の形）
METHOD_VOTE = "vote"           # 複数回引いた票数（logprobs 非対応の ollama）
METHOD_TEXT = "text"           # 本文のラベルを読んだだけ（確度は無い＝confidence 0.0）


class JudgeError(RuntimeError):
    """問いの形が違う・サーバへ届かない・答えを読めない。"""


# ---------------------------------------------------------------------------
# 問いの正規化
# ---------------------------------------------------------------------------
def _criteria_pairs(criteria) -> "list[tuple[str, str]]":
    """`criteria` を (キー, 説明) の並びへ。dict は挿入順、list は文字列か {key, description}。"""
    pairs: "list[tuple[str, str]]" = []
    if isinstance(criteria, dict):
        for key, desc in criteria.items():
            pairs.append((str(key), "" if desc is None else str(desc)))
    elif isinstance(criteria, list):
        for item in criteria:
            if isinstance(item, dict):
                key = item.get("key", item.get("name", item.get("label")))
                if key is None:
                    continue
                pairs.append((str(key), str(item.get("description") or "")))
            else:
                pairs.append((str(item), ""))
    return pairs


def _score_values(pairs: "list[tuple[str, str]]") -> "list[float]":
    """score の各選択肢の数値。キーが全部数なら数、そうでなければ順位 0..n-1。"""
    values: "list[float]" = []
    for key, _ in pairs:
        try:
            values.append(float(key))
        except ValueError:
            return [float(i) for i in range(len(pairs))]
    return values


def normalize_question(name: str, question) -> dict:
    """問いを 1 つ正規化する。形が違えば JudgeError。"""
    if not isinstance(question, dict):
        raise JudgeError(f"問い {name!r} はオブジェクトで書きます")
    qtype = str(question.get("type") or "").strip()
    if qtype not in QUESTION_TYPES:
        raise JudgeError(f"問い {name!r} の type は {', '.join(QUESTION_TYPES)} のどれかです")
    instructions = str(question.get("instructions") or question.get("question") or "").strip()
    if not instructions:
        raise JudgeError(f"問い {name!r} に instructions（問いの文）が必要です")
    if qtype == "boolean":
        pairs = list(BOOLEAN_CRITERIA)
    else:
        pairs = _criteria_pairs(question.get("criteria"))
        if len(pairs) < 2:
            raise JudgeError(f"問い {name!r} の criteria には選択肢が 2 つ以上必要です")
    other = question.get(OTHER_KEY)
    if other:
        if any(key == OTHER_KEY for key, _ in pairs):
            raise JudgeError(f"問い {name!r} の criteria に {OTHER_KEY!r} が既にあります")
        pairs.append((OTHER_KEY, other if isinstance(other, str)
                      else "None of the options above applies."))
    if len({key for key, _ in pairs}) != len(pairs):
        raise JudgeError(f"問い {name!r} の選択肢のキーが重複しています")
    if len(pairs) > MAX_OPTIONS:
        raise JudgeError(f"問い {name!r} の選択肢は {MAX_OPTIONS} 個までです")
    out = {"name": str(name), "type": qtype, "instructions": instructions,
           "options": pairs, "has_other": bool(other)}
    if qtype == "score":
        scored = pairs[:-1] if other else pairs
        out["values"] = _score_values(scored)
    return out


def question_errors(questions) -> "list[str]":
    """問いの集合の不備を並べる（空なら妥当）。"""
    if not isinstance(questions, dict) or not questions:
        return ["questions は 1 つ以上の問いを持つオブジェクトです"]
    errors: "list[str]" = []
    for name, question in questions.items():
        try:
            normalize_question(str(name), question)
        except JudgeError as exc:
            errors.append(str(exc))
    return errors


# ---------------------------------------------------------------------------
# プロンプト
# ---------------------------------------------------------------------------
def render_state(state) -> str:
    """状態は文字列ならそのまま、そうでなければ JSON（人が読める形）。"""
    if isinstance(state, str):
        return state
    try:
        return json.dumps(state, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as exc:
        raise JudgeError(f"state を JSON にできません: {exc}") from exc


def build_prompt(state_text: str, question: dict) -> str:
    """状態を先、問いを後——複数の問いで接頭辞キャッシュに乗せるため。"""
    lines = [
        "You are a decision function, not a writer. Read the state, then answer the "
        "question by writing exactly one option letter. No words, no punctuation, "
        "no explanation.",
        "",
        "State:",
        "<<<",
        state_text,
        ">>>",
        "",
        f"Question: {question['instructions']}",
        "Options:",
    ]
    for label, (key, desc) in zip(LABELS, question["options"]):
        lines.append(f"{label}. {key}: {desc}" if desc else f"{label}. {key}")
    lines.extend(["", "Answer (one letter):"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 分布の読み出し
# ---------------------------------------------------------------------------
def _label_of(token: str, count: int) -> "str | None":
    """トークンをラベル 1 文字へ寄せる（前後の空白・句読点は落とす）。"""
    text = str(token or "").strip().strip(".:)]}>\"'`").strip().upper()
    if len(text) == 1 and text in LABELS[:count]:
        return text
    return None


def _positions(logprobs) -> "list[list[dict]]":
    """ollama の `logprobs` を位置ごとの候補リストへ。形が違えば空。"""
    out: "list[list[dict]]" = []
    if not isinstance(logprobs, list):
        return out
    for pos in logprobs:
        if not isinstance(pos, dict):
            continue
        top = pos.get("top_logprobs")
        entries = [e for e in top if isinstance(e, dict)] if isinstance(top, list) else []
        if not entries and "token" in pos:
            entries = [pos]
        out.append(entries)
    return out


def readout(logprobs, count: int) -> "tuple[list[float], float] | None":
    """位置ごとの分布から、ラベルに最も質量が落ちた位置を選び (質量の並び, coverage) を返す。

    どの位置にもラベルが現れなければ None（呼び出し側が本文へ倒す）。
    """
    best: "tuple[list[float], float] | None" = None
    for entries in _positions(logprobs):
        masses = [0.0] * count
        for entry in entries:
            label = _label_of(entry.get("token"), count)
            if label is None:
                continue
            try:
                masses[LABELS.index(label)] += math.exp(float(entry.get("logprob")))
            except (TypeError, ValueError, OverflowError):
                continue
        total = sum(masses)
        if total > 0 and (best is None or total > best[1]):
            best = (masses, min(total, 1.0))
    return best


def _normalize(masses: "list[float]") -> "list[float]":
    total = sum(masses)
    if total <= 0:
        return [0.0] * len(masses)
    return [m / total for m in masses]


def _text_label(text: str, count: int) -> "str | None":
    """本文からラベルを 1 つ読む（logprobs が無いときの最後の手段）。"""
    for chunk in str(text or "").replace("\n", " ").split(" "):
        label = _label_of(chunk, count)
        if label:
            return label
    head = str(text or "").strip()[:1]
    return _label_of(head, count) if head else None


def shape_answer(question: dict, probs: "list[float]", *, method: str, coverage: float) -> dict:
    """正規化した確率を、問いの型に応じた答えへ。"""
    keys = [key for key, _ in question["options"]]
    by_key = {key: round(p, 4) for key, p in zip(keys, probs)}
    top = max(range(len(probs)), key=lambda i: probs[i]) if probs else 0
    # 案 B（2026-09-20）: `text` は本文から読み取れた事実（choice / value / bucket）は残し、
    # 確度だけを 0.0 にする——質量を均す案 A だと probabilities まで潰れて事実が消える。
    confidence = 0.0 if method == METHOD_TEXT else (round(probs[top], 4) if probs else 0.0)
    answer: dict = {"type": question["type"], "probabilities": by_key,
                    "confidence": confidence,
                    "coverage": round(coverage, 4), "method": method}
    if question["has_other"]:
        answer["other"] = by_key.get(OTHER_KEY, 0.0)
    if question["type"] == "choice":
        answer["choice"] = keys[top]
    elif question["type"] == "boolean":
        yes = by_key.get("yes", 0.0)
        answer["probability"] = yes
        answer["value"] = yes >= 0.5
    else:
        values = question["values"]
        scored = probs[:len(values)]
        weight = sum(scored)
        answer["score"] = (round(sum(p * v for p, v in zip(scored, values)) / weight, 4)
                           if weight > 0 else None)
        answer["bucket"] = keys[top]
    return answer


# ---------------------------------------------------------------------------
# ollama 呼び出し
# ---------------------------------------------------------------------------
def _request_timeout_sec() -> float:
    raw = os.environ.get("OLLAMA_TIMEOUT", "600")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 600.0
    return value if value > 0 else 600.0


def post_chat(payload: dict, *, host: "str | None" = None, timeout: "float | None" = None) -> dict:
    """`/api/chat` を非ストリーミングで 1 回叩く。"""
    base = (host or ollama_loop.host_url()).rstrip("/")
    req = urllib.request.Request(
        f"{base}/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout or _request_timeout_sec()) as res:
            data = json.load(res)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise JudgeError(f"ollama API error ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise JudgeError(f"ollama に接続できません: {exc.reason}") from exc
    except TimeoutError as exc:
        raise JudgeError("ollama API がタイムアウトしました") from exc
    if not isinstance(data, dict):
        raise JudgeError("ollama API がオブジェクト以外を返しました")
    return data


def _payload(model: str, prompt: str, *, think, options: "dict | None",
             readout_mode: bool, labels: "list[str] | None" = None,
             temperature: "float | None" = None) -> dict:
    merged = ollama_loop.load_options()
    if options:
        merged.update(options)
    merged["num_predict"] = DEFAULT_NUM_PREDICT if readout_mode else 16
    merged["temperature"] = 0 if temperature is None else temperature
    body: dict = {"model": model, "stream": False,
                  "messages": [{"role": "user", "content": prompt}], "options": merged}
    if think is not None:
        body["think"] = bool(think)
    if readout_mode:
        body["logprobs"] = True
        body["top_logprobs"] = DEFAULT_TOP_LOGPROBS
    else:
        body["format"] = {"type": "object",
                          "properties": {"answer": {"type": "string", "enum": list(labels or [])}},
                          "required": ["answer"]}
    keep_alive = os.environ.get("AGENT_OLLAMA_KEEP_ALIVE", "").strip()
    if keep_alive:
        body["keep_alive"] = keep_alive
    return body


def _content(data: dict) -> str:
    message = data.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(data.get("response") or "")


def _usage_add(usage: dict, data: dict) -> None:
    usage["tokens_in"] += int(data.get("prompt_eval_count") or 0)
    usage["tokens_out"] += int(data.get("eval_count") or 0)


def _vote(question: dict, prompt: str, *, model, think, options, samples, request, usage) -> dict:
    """structured outputs でラベルを N 回引き、票数を確率にする。"""
    count = len(question["options"])
    labels = list(LABELS[:count])
    masses = [0.0] * count
    for _ in range(samples):
        data = request(_payload(model, prompt, think=think, options=options,
                                readout_mode=False, labels=labels, temperature=0.7))
        _usage_add(usage, data)
        try:
            picked = json.loads(_content(data)).get("answer")
        except (ValueError, AttributeError):
            picked = None
        label = _label_of(picked, count) if picked is not None else None
        if label:
            masses[LABELS.index(label)] += 1.0
    read = sum(masses)
    if read <= 0:
        raise JudgeError(f"問い {question['name']!r} の答えを {samples} 回引いても読めませんでした")
    return shape_answer(question, _normalize(masses), method=METHOD_VOTE,
                        coverage=read / samples)


def evaluate(state, questions: dict, *, model: str = DEFAULT_MODEL, think=False,
             options: "dict | None" = None, samples: int = 1, request=None,
             host: "str | None" = None, timeout: "float | None" = None) -> dict:
    """状態と問いの集合から、問いごとの型付きの答えを返す。

    戻り値: {"answers": {名前: 答え}, "usage": {"tokens_in", "tokens_out"}, "model": …}。
    `request` は `/api/chat` の payload を受けて応答 dict を返す関数（テストと差し替え用）。
    """
    errors = question_errors(questions)
    if errors:
        raise JudgeError(" / ".join(errors))
    state_text = render_state(state)
    send = request or (lambda payload: post_chat(payload, host=host, timeout=timeout))
    usage = {"tokens_in": 0, "tokens_out": 0}
    answers: dict = {}
    for name, raw in questions.items():
        question = normalize_question(str(name), raw)
        prompt = build_prompt(state_text, question)
        count = len(question["options"])
        data = send(_payload(model, prompt, think=think, options=options, readout_mode=True))
        _usage_add(usage, data)
        read = readout(data.get("logprobs"), count)
        if read is not None:
            masses, coverage = read
            answers[name] = shape_answer(question, _normalize(masses),
                                         method=METHOD_LOGPROBS, coverage=coverage)
            continue
        # ここから先は「分布を読めなかった」——`logprobs` が無い場合と、あっても形が違って
        # 読めない場合の両方。どちらも票で確率を作り直せるので `--samples` を効かせる。
        if samples > 1:
            answers[name] = _vote(question, prompt, model=model, think=think, options=options,
                                  samples=samples, request=send, usage=usage)
            continue
        label = _text_label(_content(data), count)
        if label is None:
            raise JudgeError(f"問い {name!r} の答えを読めませんでした"
                             f"（本文: {_content(data)[:80]!r}）")
        masses = [0.0] * count
        masses[LABELS.index(label)] = 1.0
        answers[name] = shape_answer(question, masses, method=METHOD_TEXT, coverage=0.0)
    return {"answers": answers, "usage": usage, "model": model}


def setting() -> dict:
    """設定ファイルの `judge` の解決結果（`herdconfig.judge_setting`）: mode は auto / pinned / off。"""
    return herdconfig.judge_setting()


def pinned_model() -> "str | None":
    """設定で指名されたモデル名。`auto` と `off` は None。"""
    current = setting()
    return current["model"] if current["mode"] == "pinned" else None


def disabled() -> bool:
    """設定が `judge.model: off`——judge をどの実行でも使わない。"""
    return setting()["mode"] == "off"


def model_for_spec(spec, model: "str | None" = None) -> "str | None":
    """定義（agents/<name>.json の正規化済み dict）で judge を使えるなら、そのモデル名。

    解決の順（設定は `~/.agents/agent-herd.yaml` の `judge.model`）:

    1. `off` なら None（judge を使わない）。
    2. モデル名なら、定義がクラウド CLI でもそのモデル。判定を実行のモデルから切り離して
       LAN の ollama に固定する口で、クラウド CLI の実行で判定に使っていたトークンがここで消える。
    3. `auto`（未設定）なら**ローカルの定義**（`relative_cost` が 0 の aider / ollama）だけ。
       judge は ollama を直に叩くので、指名なしにクラウド CLI の定義で叩きに行かない。
       モデルは呼び出し側の指定を持ち越し、無ければ定義の既定。
    """
    current = setting()
    if current["mode"] == "off":
        return None
    if current["mode"] == "pinned":
        return current["model"]
    if not isinstance(spec, dict) or spec.get("relative_cost") != 0:
        return None
    name = str(model or spec.get("default_model") or "").strip()
    return name or DEFAULT_MODEL


def local_model(cli: str, model: "str | None" = None, *, project_dir=None) -> "str | None":
    """定義名（`ollama-json` のような profile 綴りも可）から `model_for_spec` を引く。
    定義を解決できなければ None（設定ミスで実行を殺さない——agentcli の方針と同じ）。
    設定でモデルを指名してあれば定義を解決せずにそれを返す（定義に依らない）。"""
    current = setting()
    if current["mode"] == "off":
        return None
    if current["mode"] == "pinned":
        return current["model"]
    from agentcore import agentcli
    try:
        spec = agentcli.load_cli(str(cli or ""), project_dir=project_dir)
    except Exception:                       # noqa: BLE001  解決できない＝judge を使わない
        return None
    return model_for_spec(spec, model)


def abstained(answers: dict, min_confidence: float, *, allow_text: bool = False) -> "list[str]":
    """確度がしきい値に届かない問いの名前（呼び出し側が「決めない」へ倒すため）。

    `method` が `text` の答えは、しきい値に関わらず棄権に入れる。本文からラベルを 1 つ
    読んだだけで確度の材料が無く、`min_confidence` が 0.0（「実測してから決める」の置き値）
    の呼び出しでは `confidence` の比較だけでは素通りするため。本文のラベルで足りる
    呼び出しは `allow_text=True` を渡す。
    """
    return [name for name, answer in answers.items()
            if (not allow_text and answer.get("method") == METHOD_TEXT)
            or float(answer.get("confidence") or 0.0) < min_confidence]


def calibrated_abstained(answers: dict, min_confidence: float, *, purpose: str,
                         model: str) -> "list[str]":
    """Apply an explicitly configured, model/method-specific consumer gate.

    Absent policy preserves existing behavior. Invalid policy, mismatched model,
    unmeasured purpose/method and low coverage fail closed to the consumer's
    existing fallback. The pure abstained API and standalone judge stay unchanged.
    """
    try:
        policy = herdconfig.calibration_setting()
    except herdconfig.ConfigError:
        return list(answers)
    if policy is None:
        return abstained(answers, min_confidence)
    threshold = policy["thresholds"].get(purpose)
    if model != policy["model"] or threshold is None:
        return list(answers)
    held = set(abstained(answers, max(min_confidence, threshold)))
    for name, answer in answers.items():
        coverage = answer.get("coverage")
        confidence = answer.get("confidence")
        if (answer.get("method") != policy["method"]
                or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                       or not 0 <= v <= 1 for v in (coverage, confidence))
                or coverage < policy["min_coverage"]):
            held.add(name)
    return [name for name in answers if name in held]
