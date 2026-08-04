"""agentcore.modeltier — モデル階層（strong/weak）ルーティングの 1 実装（オプトイン）。

## なぜここに 1 実装を置くか

利用枠の逼迫（柱1・C1）への対策として「判断が重い処理には強いモデル、大量に走る機械的な
処理には安価なモデル」という使い分けは、既存の役割別上書き（設定 `agents:`）で今日でも
表現できる。ただしそのためには、各エンジンの purpose 語彙（project: plan/review/…、
flow: planner/`<kind>`…、audit: extract/…）と「どの処理が強いモデルに値するか」という
判断の両方を、ユーザーが複数の設定ファイルへ手書きする必要があり、実際には使われない。

ここは、その判断（purpose → 階層の既定分類）と展開規則を 1 実装に集約する（C7）。
各エンジンは設定 `model_tiers:` を受け取ったとき、**既存の `agents:` マップへ展開してから**
従来どおりの解決（agent-control > agents[purpose] > グローバル）に流す——解決経路そのものは
1 本も増やさない。`model_tiers:` が無ければ何もしない（完全オプトイン・挙動不変）。

## 優先順位（変えない）

    agent-control > CLI > agents:（明示） > model_tiers（この展開） > グローバル agent_cli/model

node-budget の soft 縮退（control の degraded）も従来どおり最上位に重なる。「予算が減って
きたら弱いモデルへ」は degraded の仕事、ここは「平常時から処理の性格で使い分ける」層。

## 既定分類の方針

- strong … 判断が重く、失敗が高くつく処理（計画・レビュー・裁定・検証ゲート）。弱くすると
  差し戻しと再実行が増え、人へ倒れる回数（C3 が減らしたいもの）まで増えて本末転倒。
- weak  … 大量に走る・機械的・失敗が安全側に倒れる処理（後段に閾値ゲートか人の目がある）。
- 成果物を直接作る処理（flow の worker/work/generate/synthesize/map/reduce）は既定で
  触らない——弱いモデルの成果物は verify で落ちて作り直しになり、かえって高くつく
  （リトライも財布から出る）。`purposes:` で明示したときだけ対象になる。

分類は各エンジンの purpose 語彙の**写し**なので、各エンジンのテストが正典
（AGENT_PURPOSES / VALID_KINDS 等）と突き合わせて縛る（C7）。

設計: docs/plans/2026-08-04-model-tier-routing-design.md
"""
from __future__ import annotations

TIERS = ("strong", "weak")

# workload → {purpose: tier}。ここに無い purpose は model_tiers の対象外（グローバルのまま）。
DEFAULT_PURPOSE_TIERS: "dict[str, dict[str, str]]" = {
    "project": {
        "plan": "strong", "review": "strong", "adjudicate": "strong", "verify": "strong",
        "prioritize": "weak", "route": "weak", "distill": "weak", "assess": "weak",
        "repo_map": "weak", "doctor": "weak",
    },
    "flow": {
        "planner": "strong", "evaluator": "strong", "judge": "strong", "verify": "strong",
        "classify": "weak", "filter": "weak", "split": "weak",
    },
    "audit": {
        "review": "strong",
        "extract": "weak", "distill": "weak",
    },
}


def _entry(raw) -> "dict | None":
    """{agent_cli, model} を正規化する。どちらも無ければ None（そのエントリは効かない）。"""
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    if isinstance(raw.get("agent_cli"), str) and raw["agent_cli"].strip():
        out["agent_cli"] = raw["agent_cli"].strip().lower()
    if isinstance(raw.get("model"), str) and raw["model"].strip():
        out["model"] = raw["model"].strip()
    return out or None


def normalize(raw) -> dict:
    """設定 `model_tiers:` を正規化する。不正値は黙って落とす（既存 `agents:` と同じ流儀
    ——設定ミスでループを殺さない。効いているかは control の status / doctor の実効値で見る）。

    返り値: {"strong": {...}?, "weak": {...}?, "purposes": {purpose: tier|"off"}?}。
    strong / weak がどちらも無ければ {}（＝無効。展開は何もしない）。"""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for tier in TIERS:
        spec = _entry(raw.get(tier))
        if spec:
            out[tier] = spec
    if not out:
        return {}
    purposes: dict = {}
    p_raw = raw.get("purposes")
    if isinstance(p_raw, dict):
        for k, v in p_raw.items():
            key, val = str(k).strip().lower(), str(v or "").strip().lower()
            if key and val in TIERS + ("off",):
                purposes[key] = val
    if purposes:
        out["purposes"] = purposes
    return out


def assignments(workload: str, tiers: dict) -> "dict[str, str]":
    """workload の purpose → tier 割当（既定分類 + `purposes:` 上書き。off は除外）。
    tiers は normalize 済みを渡す。"""
    assign = dict(DEFAULT_PURPOSE_TIERS.get(str(workload or "")) or {})
    for key, val in (tiers.get("purposes") or {}).items():
        if val == "off":
            assign.pop(key, None)
        else:
            assign[key] = val
    return assign


def expand(workload: str, raw_tiers, explicit=None, valid=None) -> "dict[str, dict]":
    """`model_tiers:` を `agents:` 相当のマップへ展開して返す（決定的・副作用なし）。

    - explicit（明示の `agents:` 上書き）は **purpose 単位で丸ごと勝つ**（部分マージしない
      ——「agent_cli だけ明示したら model は階層から来る」という遠隔作用を作らない）。
    - valid を渡すと、階層由来のキーはその語彙に絞る（explicit のキーには触れない——
      そちらの検証は呼び出し側の既存規則のまま）。
    - `model_tiers:` 未設定なら explicit をそのまま返す（オプトイン: 挙動不変）。
    - 割り当てられた tier のエントリ（strong / weak）が宣言されていなければ、その purpose は
      触らない（片方だけ宣言する運用——例: weak だけ差し替え——を許す）。
    """
    out = {k: dict(v) for k, v in (explicit or {}).items() if isinstance(v, dict)}
    tiers = normalize(raw_tiers)
    if not tiers:
        return out
    for purpose, tier in assignments(workload, tiers).items():
        if valid is not None and purpose not in valid:
            continue
        if purpose in out:
            continue
        spec = tiers.get(tier)
        if spec:
            out[purpose] = dict(spec)
    return out
