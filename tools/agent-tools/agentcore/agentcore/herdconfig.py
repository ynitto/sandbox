"""agentcore.herdconfig — agent-herd の各 PC の設定ファイル（`~/.agents/agent-herd.yaml`）。

## 何を置く設定か

環境変数では届かない・残らない設定を置く。最初の項目は `judge`（判定 AI）のモデル:

```yaml
judge:
  model: gemma4:e4b     # 判定はいつもこのローカルモデルの judge へ（クラウド CLI の実行でも）
  # model: off          # judge をどの実行でも使わない
  # 省略 / auto         # ローカル定義（aider / ollama）で回しているときだけ judge
```

agent-app の「設定 > 実行制御」はこのファイルを直接は触らず、`agent-herd config set judge.model …`
で書く——python が動く側（WSL なら WSL の home）の `~/.agents` に置くため。

2 つ目の項目は `select`（呼び出し先の選択。`agentcore.modelselect`）:

```yaml
select:
  jev:
    api_key: sk-…          # 本家 Jev（TypeSafe AI）の API キー。無ければ環境変数 TYPESAFE_API_KEY
    endpoint: https://…    # 省略時は https://api.typesafe.ai/v1/systemone（ゲートウェイ経由なら差し替え）
    model: jev-latest      # 省略時は jev-latest
  min_confidence: 0.6      # jev / judge の答えを採る確度の下限（0〜1）
```

3 つ目は `route`（依頼の振り分け。`agentcore.route`）:

```yaml
route:
  min_confidence: 0.6       # 省略時は select.min_confidence と同じ
  hold_min_confidence: 0.75 # 会話を止めてタスク / ワークフローの流用を勧める確度の下限（省略時 0.75）
```

## 場所と形

`~/.agents/agent-herd.yaml` / `.yml` / `.json` のうち見つかった最初の 1 つを読む
（agent-loop の共通設定と同じ作法）。無ければ空。書くときは既存のファイルがあればその形式、
無ければ YAML（pyyaml が無ければ JSON）で `~/.agents/agent-herd.yaml|json` を作る。
`AGENT_PROJECT_AGENTS_HOME` で `~/.agents` を差し替えられる（定義の探索と同じ変数。テスト用）。

壊れたファイルは黙って空として扱わない——`load()` は `ConfigError` を上げ、`judge` 側は
それを「設定なし」に倒しつつ理由を持ち帰れるよう `judge_setting()` で吸収する。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

_AGENTS_HOME_ENV = "AGENT_PROJECT_AGENTS_HOME"
_AGENTS_HOME_DIR = ".agents"
CONFIG_NAMES = ("agent-herd.yaml", "agent-herd.yml", "agent-herd.json")

# judge.model の値の語彙。モデル名以外はこの 2 語だけ（`off` の同義語は増やさない——
# 設定ファイルに書く言葉は 1 つで足りる）。
JUDGE_AUTO = "auto"
JUDGE_OFF = "off"
# 設定の項目名（`agent-herd config set` が受け付ける鍵）。増やすならここと `describe()`。
SELECT_KEYS = ("select.jev.api_key", "select.jev.endpoint", "select.jev.model",
               "select.min_confidence")
# `route`（依頼の振り分け。`agentcore.route`）の確度の下限。min_confidence を省くと select と同じ値。
ROUTE_KEYS = ("route.min_confidence", "route.hold_min_confidence")
KNOWN_KEYS = ("judge.model", "judge.calibration", "judge.rotations", "judge.keep_alive",
              *SELECT_KEYS, *ROUTE_KEYS)


class ConfigError(RuntimeError):
    """設定ファイルを読めない・書けない・鍵や値の形が違う。"""


def agents_home() -> Path:
    override = os.environ.get(_AGENTS_HOME_ENV)
    return Path(override).expanduser() if override else (Path.home() / _AGENTS_HOME_DIR)


def find_path() -> "Path | None":
    """既存の設定ファイル（見つかった最初の 1 つ）。無ければ None。"""
    home = agents_home()
    for name in CONFIG_NAMES:
        candidate = home / name
        if candidate.is_file():
            return candidate
    return None


def _parse(text: str, path: Path) -> dict:
    if not text.strip():
        return {}
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ConfigError(f"{path} を JSON として読めません: {exc}") from exc
    else:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - 環境依存
            raise ConfigError(f"{path} を読むには pyyaml が要ります") from exc
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} を YAML として読めません: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} の最上位はオブジェクト（鍵と値）で書きます")
    return data


def load() -> dict:
    """設定ファイルの中身（無ければ空 dict）。読めなければ ConfigError。"""
    path = find_path()
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path} を読めません: {exc}") from exc
    return _parse(text, path)


def _dump(data: dict, path: Path) -> str:
    if path.suffix == ".json":
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    import yaml  # type: ignore
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def save(data: dict) -> Path:
    """設定を書き戻す。既存ファイルがあればその形式、無ければ YAML（pyyaml 無しなら JSON）。"""
    path = find_path()
    if path is None:
        try:
            import yaml  # type: ignore  # noqa: F401
            path = agents_home() / CONFIG_NAMES[0]
        except ImportError:
            path = agents_home() / CONFIG_NAMES[2]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(_dump(data, path), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise ConfigError(f"{path} に書けません: {exc}") from exc
    return path


# ---------------------------------------------------------------------------
# judge の設定
# ---------------------------------------------------------------------------
def normalize_judge_model(value) -> str:
    """`judge.model` の値を `auto` / `off` / モデル名 のどれかへ。空は `auto`。

    YAML は裸の `off` を真偽値 False として読む（`model: off` と手で書く人のため）。
    True は「モデル名でも off でもない」ので auto に倒す。
    """
    if value is False:
        return JUDGE_OFF
    if value is True:
        return JUDGE_AUTO
    text = str(value if value is not None else "").strip()
    if not text or text.lower() == JUDGE_AUTO:
        return JUDGE_AUTO
    if text.lower() == JUDGE_OFF:
        return JUDGE_OFF
    return text


def judge_setting() -> dict:
    """judge の設定を 1 つの dict で: {"mode": auto|pinned|off, "model": str|None, "error": str|None}。

    設定ファイルが壊れていれば mode は `auto`（従来どおり）で、`error` に理由を残す。
    呼び出し側（`judge.model_for_spec` 等）は mode だけを見ればよく、`agent-herd config` は
    error を人に見せる。
    """
    try:
        data = load()
    except ConfigError as exc:
        return {"mode": JUDGE_AUTO, "model": None, "error": str(exc)}
    section = data.get("judge") if isinstance(data, dict) else None
    raw = section.get("model") if isinstance(section, dict) else None
    value = normalize_judge_model(raw)
    if value == JUDGE_AUTO:
        return {"mode": "auto", "model": None, "error": None}
    if value == JUDGE_OFF:
        return {"mode": "off", "model": None, "error": None}
    return {"mode": "pinned", "model": value, "error": None}


# ollama の `keep_alive` に渡せる書き方（秒数、`30m` のような単位つき、`-1` は常駐、`0` は即解放）。
_KEEP_ALIVE = __import__("re").compile(r"^-1$|^\d+(?:\.\d+)?(?:ns|us|ms|s|m|h)?$")


def normalize_keep_alive(value) -> "str | None":
    """`judge.keep_alive` の値。空は未設定（ollama の既定 5 分に任せる）。

    判定のたびにモデルが読み込み直されると、この mac の実測で 1 回目に 6.24 秒乗る
    （gemma4:e4b）。長くすると速いが、その間メモリを占める（e4b で約 3.9GB）ので既定は置かない。
    """
    if value is None or (isinstance(value, bool)) or (isinstance(value, str) and not value.strip()):
        return None if not isinstance(value, bool) else None
    text = str(value).strip()
    if not text:
        return None
    if not _KEEP_ALIVE.match(text):
        raise ConfigError("judge.keep_alive は秒数か 30m のような時間、-1（常駐）、0（即解放）です")
    return text


def keep_alive_setting() -> "str | None":
    """`judge.keep_alive` の値。未設定は None。壊れていれば ConfigError。"""
    section = load().get("judge")
    return normalize_keep_alive(section.get("keep_alive")) if isinstance(section, dict) else None


def normalize_rotations(value) -> "int | None":
    """`judge.rotations`（選択肢の並びを巡回させて読む回数）: 1〜26 の整数。空は未設定。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ConfigError("judge.rotations は 1 以上の整数です")
    try:
        count = int(str(value).strip())
    except ValueError as exc:
        raise ConfigError("judge.rotations は 1 以上の整数です") from exc
    if not 1 <= count <= 26:
        raise ConfigError("judge.rotations は 1〜26 です（選択肢は 26 個まで）")
    return count


def rotations_setting() -> "int | None":
    """`judge.rotations` の値。未設定は None（judge 側が既定を使う）。壊れていれば ConfigError。"""
    section = load().get("judge")
    return normalize_rotations(section.get("rotations")) if isinstance(section, dict) else None


CALIBRATION_PURPOSES = ("filter", "route", "assess", "transition")


def normalize_calibration(value) -> dict:
    """Explicit operator policy; never derive or apply thresholds from a report."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ConfigError("judge.calibration は JSON オブジェクトで指定します") from exc
    if not isinstance(value, dict):
        raise ConfigError("judge.calibration はオブジェクトです")
    if not isinstance(value.get("model"), str) or not value["model"].strip():
        raise ConfigError("judge.calibration.model が必要です")
    if value.get("method") not in ("logprobs", "vote"):
        raise ConfigError("judge.calibration.method は logprobs / vote です")
    thresholds = value.get("thresholds")
    if not isinstance(thresholds, dict) or set(thresholds) - set(CALIBRATION_PURPOSES):
        raise ConfigError("judge.calibration.thresholds の用途が不正です")
    numbers = [value.get("min_coverage"), *[v for v in thresholds.values() if v is not None]]
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or not 0 <= v <= 1 for v in numbers):
        raise ConfigError("calibration の confidence / coverage は 0〜1 です（用途の null は保留）")
    return value


def calibration_setting():
    section = load().get("judge")
    if not isinstance(section, dict) or "calibration" not in section:
        return None
    return normalize_calibration(section["calibration"])


def select_setting() -> dict:
    """`select` の設定を 1 つの dict で: {"jev": {"api_key", "endpoint", "model", "source", "off"},
    "min_confidence": float|None, "error": str|None}。壊れたファイルは「設定なし」に倒して理由を残す。"""
    try:
        data = load()
    except ConfigError as exc:
        return {"jev": {}, "min_confidence": None, "error": str(exc)}
    section = data.get("select") if isinstance(data.get("select"), dict) else {}
    jev_raw = section.get("jev") if isinstance(section.get("jev"), dict) else {}
    jev: dict = {}
    key = str(jev_raw.get("api_key") or "").strip()
    if key:
        jev["api_key"] = key
        jev["source"] = "config"
    if jev_raw.get("api_key") is False or str(jev_raw.get("api_key") or "").strip().lower() == JUDGE_OFF:
        jev["off"] = True
        jev.pop("api_key", None)
        jev.pop("source", None)
    for name in ("endpoint", "model"):
        text = str(jev_raw.get(name) or "").strip()
        if text:
            jev[name] = text
    raw_conf = section.get("min_confidence")
    min_conf = None
    if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool) \
            and math.isfinite(raw_conf) and 0 <= raw_conf <= 1:
        min_conf = float(raw_conf)
    return {"jev": jev, "min_confidence": min_conf, "error": None}


def _confidence_or_none(value) -> "float | None":
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and math.isfinite(value) and 0 <= value <= 1:
        return float(value)
    return None


def route_setting() -> dict:
    """`route` の設定を 1 つの dict で: {"min_confidence": float|None,
    "hold_min_confidence": float|None, "error": str|None}。壊れたファイルは「設定なし」に倒す。"""
    try:
        data = load()
    except ConfigError as exc:
        return {"min_confidence": None, "hold_min_confidence": None, "error": str(exc)}
    section = data.get("route") if isinstance(data.get("route"), dict) else {}
    return {"min_confidence": _confidence_or_none(section.get("min_confidence")),
            "hold_min_confidence": _confidence_or_none(section.get("hold_min_confidence")),
            "error": None}


def _normalize_select_value(key: str, value):
    if key.endswith("min_confidence"):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} は 0〜1 の数です") from exc
        if not math.isfinite(number) or not 0 <= number <= 1:
            raise ConfigError(f"{key} は 0〜1 の数です")
        return number
    if key == "select.jev.api_key" and value is False:
        return JUDGE_OFF
    text = str(value if value is not None else "").strip()
    if not text:
        raise ConfigError(f"{key} の値が空です（消すなら unset）")
    return text


def _set_path(data: dict, path: "tuple[str, ...]", value) -> None:
    """入れ子の鍵へ書く（None は消す）。空になった節は落とす。"""
    head, rest = path[0], path[1:]
    if not rest:
        if value is None:
            data.pop(head, None)
        else:
            data[head] = value
        return
    child = data.get(head) if isinstance(data.get(head), dict) else {}
    _set_path(child, rest, value)
    if child:
        data[head] = child
    else:
        data.pop(head, None)


def set_value(key: str, value) -> Path:
    """`agent-herd config set KEY VALUE`。鍵は KNOWN_KEYS だけ（未知の鍵は黙って書かない）。"""
    if key not in KNOWN_KEYS:
        raise ConfigError(f"未知の設定 {key!r}（使えるのは {', '.join(KNOWN_KEYS)}）")
    data = load()
    if key == "judge.model":
        model = normalize_judge_model(value)
        section = data.get("judge") if isinstance(data.get("judge"), dict) else {}
        if model == JUDGE_AUTO:
            section.pop("model", None)
        else:
            section["model"] = model
        if section:
            data["judge"] = section
        else:
            data.pop("judge", None)
    elif key == "judge.calibration":
        section = data.get("judge") if isinstance(data.get("judge"), dict) else {}
        if value is None:
            section.pop("calibration", None)
        else:
            section["calibration"] = normalize_calibration(value)
        if section:
            data["judge"] = section
        else:
            data.pop("judge", None)
    elif key == "judge.rotations":
        _set_path(data, ("judge", "rotations"), normalize_rotations(value))
    elif key == "judge.keep_alive":
        _set_path(data, ("judge", "keep_alive"), normalize_keep_alive(value))
    elif key in SELECT_KEYS or key in ROUTE_KEYS:
        normalized = None if value is None else _normalize_select_value(key, value)
        _set_path(data, tuple(key.split(".")), normalized)
    return save(data)


def unset_value(key: str) -> Path:
    return set_value(key, None)


def describe() -> dict:
    """`agent-herd config` が出す姿: 設定ファイルの場所と、解決済みの各項目。"""
    path = find_path()
    try:
        calibration, calibration_error = calibration_setting(), None
    except ConfigError as exc:
        calibration, calibration_error = None, str(exc)
    try:
        rotations, rotations_error = rotations_setting(), None
    except ConfigError as exc:
        rotations, rotations_error = None, str(exc)
    try:
        keep_alive, keep_alive_error = keep_alive_setting(), None
    except ConfigError as exc:
        keep_alive, keep_alive_error = None, str(exc)
    select = select_setting()
    jev = dict(select["jev"])
    # API キーは表示しない（`config --json` は agent-app や人の画面へ流れる）。
    if jev.get("api_key"):
        jev["api_key"] = "(set)"
    return {"calibration": calibration, "calibration_error": calibration_error,
            "rotations": rotations, "rotations_error": rotations_error,
            "keep_alive": keep_alive, "keep_alive_error": keep_alive_error,
            "path": str(path) if path else None,
            "default_path": str(agents_home() / CONFIG_NAMES[0]),
            "judge": judge_setting(),
            "select": {"jev": jev, "min_confidence": select["min_confidence"],
                       "error": select["error"]},
            "route": route_setting()}
