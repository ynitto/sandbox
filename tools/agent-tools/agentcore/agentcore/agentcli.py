"""agentcore.agentcli — エージェント CLI 定義（agents/<name>.json）の唯一のローダ。

## なぜここに 1 実装を置くか

同じ「この CLI をどう起動するか」を 4 者が別々に実装していた:

- `agent_project/prioritize.py:_agent_cmd` … 組み込み 4 CLI の分岐 + 自前プラグインローダ
- `agent_flow/agent.py`                    … 同上
- `agent_amigos/agentcli.py`               … 同上
- `agent-dashboard` の `agent.js`          … 同上 + 対話 argv + 読み取り専用フラグ

`agentcore.repolocal` が解決したのと同型の問題（3 者の URL 正規化の吸収規則が食い違い、同じ
2 つの URL が経路によって一致したりしなかったりした）で、`empty_output_is_error` の扱いや
`{model}` の省略規則が実装ごとにずれれば、**同じ定義ファイルがツールによって別の argv になる**。

Python 側（agent-project / agent-flow / agent-amigos）はここを共有する。agent-dashboard だけは
UI の応答性のため JS の自前ローダを持ち（Python を起こすと候補一覧の描画がプロセス起動待ちに
なる）、同じ定義から同じ argv が出ることをゴールデンテストで固定する。

## argv の組み立て順（契約）

    command + (write_args | readonly_args) + no_session_args? + spill.args?
            + model_flag model + command_suffix + argv 渡しのプロンプト

`continue_args` / `resume_args` だけはこの順に載らず、**サブコマンドの直後**（先頭から
連続する非オプションのトークンの後ろ）へ差し込む。codex の継続が `codex exec resume --last`
というサブコマンドで、オプション列の後ろに置くと別の意味になるためである。

`command` 内の `{model}` / `{output_file}` は置換され、`{model}` はモデル未指定ならトークンごと
落ちる。`model_flag` は `command` に `{model}` が無くモデル指定があるときだけ付く。

契約の正典は `schemas/agent-cli.schema.json`。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 探索順（schemas/agent-cli.schema.json の規約）。環境変数はテストが実ホームを汚さないための seam。
_AGENTS_DIR_ENV = "KIRO_AGENTS_DIR"
_AGENTS_HOME_ENV = "AGENT_PROJECT_AGENTS_HOME"
_AGENTS_HOME_DIR = ".agents"

_USAGE_RE = re.compile(
    r"(?m)^@agent-usage\s+tokens_in=(\d+)\s+tokens_out=(\d+)\s*$")

def _bundled_dir() -> "Path | None":
    """ツール同梱の定義（このリポジトリの agents/）。install.py が ~/.agents/agents/ へ配るが、
    リポジトリから直接動かす開発環境でも解決できるよう、上へ辿って最後の候補として見る。"""
    for parent in Path(__file__).resolve().parents:
        cand = parent / "agents"
        if (cand / "kiro.json").is_file():
            return cand
    return None


# ---------------------------------------------------------------------------
# プロファイル（1 つのエージェントを用途で使い分ける起動差）
#
# 以前は用途ごとに別の定義ファイル（ollama-json / ollama-list / …）を置き、それぞれが
# 独立した `agent_cli` として台帳と格付けに現れていた。しかし「どの用途か」は候補契約が
# 既に持っている次元である（agent-candidate-qualifications の
# candidate=(agent_cli, model) → qualifications: {operation_class → 格付け}、
# agent-audit の集計キーも (agent_cli, model, operation_class)）。同じ次元を agent_cli の
# 値へ畳み込むと、1 実行系の実測が複数の偽候補へ割れ、運用者には別エージェントに見える。
#
# そこで用途別の起動差は**定義の中の profiles** にする。`ollama-json` のような従来の綴りは
# そのまま解決できる（base=ollama / profile=json）が、spec["name"] は正典の "ollama" になる
# ——台帳と格付けのキーはこれを使う。
#
# 継承の規則（今日の挙動をそのまま再現するために、こう分けてある）:
#
# - **引き継ぐ**（エージェント単位の性質）… relative_cost / prompt_via / prompt_flag /
#   file_flag / read_flag / model_flag / output / command_suffix / skill_command_prefix /
#   empty_output_is_error / readonly / spill / errors / session_log / default_model /
#   timeout / headless_autonomy / write_args / readonly_args / no_session_args / env
#   （profile が宣言すれば上書き。`[]` の宣言も「上書き」として扱う）
# - **引き継がない**（起動の形ごとに決まるもの）… interactive / variants / slash_native
#   引き継ぐと、対話面を持たない役割に base の TUI が生えて実行経路が変わる
#   （agent-dashboard は interactive の有無を見る）。variants も同様に、役割へ base の
#   振り替え表が生えると振り替え先が変わる。slash_native も起動の形の性質で、既定は
#   その profile 自身の headless_autonomy から導く（下の normalize を参照）。
# ---------------------------------------------------------------------------
_PROFILE_NOT_INHERITED = ("interactive", "variants", "slash_native")
_PROFILE_FIELDS = (
    "command", "command_suffix", "write_args", "readonly_args", "no_session_args",
    "continue_args", "resume_args",
    "headless_autonomy", "default_model", "timeout", "env", "readonly", "prompt_via",
    "prompt_flag", "file_flag", "read_flag", "model_flag", "empty_output_is_error",
    "json_object_only",
    "output", "skill_command_prefix", "relative_cost", "interactive", "variants",
    "slash_native",
    # errors は本来エージェント単位の性質だが、実際には役割ごとに調整されている
    # （read は同じ match に別の hint、verify は自前の timeout 規則）。分類の挙動を
    # 変えないことを優先して上書きを許す。宣言しない profile は base をそのまま継ぐ。
    "errors",
)


# 一族（ローカル実行系）の入口。`command[0]` がこれなら agent-herd の共通 TUI を持つ
# ——判定は綴りだけで済む（定義に family フィールドを足さない）。dashboard 側の写しは
# `agent-dashboard/src/features/orchestration/main/herd-family.js`。
HERD_ENTRYPOINT = "agent-herd"


def is_herd_family(spec: "dict | None") -> bool:
    """この定義が agent-herd の一族か（対話面が我々の共通 TUI か）。

    共通 TUI はコマンド面としてルート表（`/sm` `/edit` …）を持つ。クラウド CLI は
    それを知らないので、送る 1 行の綴りがこの判定で変わる。
    """
    if not isinstance(spec, dict):
        return False
    for key in ("command", "interactive"):
        argv = spec.get(key)
        if key == "interactive":
            argv = (argv or {}).get("command") if isinstance(argv, dict) else None
        if isinstance(argv, (list, tuple)) and argv and str(argv[0]).strip() == HERD_ENTRYPOINT:
            return True
    return False


def canonical_name(name: str, project_dir=None) -> str:
    """台帳・格付けのキーに使う正典の agent_cli 名。

    `ollama-json` のような従来の綴りで呼ばれても、記録に残すのは `ollama` である。
    ここを通さないと、1 実行系の実測が用途ごとの偽候補へ割れる
    （agent-audit の集計キーは (agent_cli, model, operation_class) で、用途の次元は
    そちらが持っている）。

    **綴りでは判定しない**——`ollama-json` という名前の定義ファイルが実在するなら、それは
    profile ではなく独立したエージェントである。定義に問い合わせて答える。解決できない
    名前は素通しする（未知の名前で記録を落とさない）。
    """
    key = str(name or "").strip().lower()
    if not key:
        return ""
    try:
        return str(load_cli(key, project_dir).get("name") or key)
    except AgentCliError:
        return key


def _apply_profile(base: dict, profile: str, path) -> dict:
    """base の spec へ profile を重ねた spec を返す（`name` は正典のまま）。

    合成は**生の定義（raw）の段で行い、そのあと normalize を通す**。正規化を 2 通り
    持たない（profile 経由でだけ検証が緩い、が起きない）ためである。
    継承の規則は上の注記のとおりで、`interactive` と `variants` は引き継がない。
    """
    profiles = base.get("profiles") or {}
    body = profiles.get(profile)
    if not isinstance(body, dict):
        raise AgentCliError(
            f"エージェント定義 {path}: profile {profile!r} がありません"
            f"（あるのは {', '.join(sorted(profiles)) or 'なし'}）")
    raw = {k: v for k, v in (base.get("_raw") or {}).items() if k != "profiles"}
    for field in _PROFILE_NOT_INHERITED:
        raw.pop(field, None)
    body = dict(body)
    if "env" in body:                      # env だけは base へ重ねる（profile の宣言が勝つ）
        body["env"] = {**(raw.get("env") or {}), **(body.get("env") or {})}
    raw.update(body)
    spec = normalize(base["name"], raw, path)
    spec["profile"] = profile
    spec["profiles"] = profiles
    return spec


class AgentCliError(RuntimeError):
    """定義が見つからない / 壊れている。黙って別 CLI へ倒さないための明示エラー。"""


def parse_usage(stderr: str) -> "tuple[int | None, int | None]":
    """CLI アダプターが stderr に出す実測 usage を読む。本文(stdout)は信頼しない。"""
    matches = list(_USAGE_RE.finditer(str(stderr or "")))
    if not matches:
        return None, None
    match = matches[-1]
    return int(match.group(1)), int(match.group(2))


class UsageText(str):
    """文字列互換の応答に、台帳へ渡す実測値だけを添える。"""

    def __new__(cls, text: str, tokens_in=None, tokens_out=None):
        value = super().__new__(cls, text)
        value.tokens_in = tokens_in
        value.tokens_out = tokens_out
        return value


def _agents_home() -> Path:
    """エージェント共通ホーム（既定 `~/.agents`）。"""
    override = os.environ.get(_AGENTS_HOME_ENV)
    return Path(override) if override else (Path.home() / _AGENTS_HOME_DIR)


def plugin_dirs(project_dir=None) -> "list[Path]":
    """定義の探索順。first-wins（上位に置けば同梱定義を上書きできる）。"""
    dirs: "list[Path]" = []
    envd = os.environ.get(_AGENTS_DIR_ENV)
    if envd:
        dirs.append(Path(envd).expanduser())
    dirs.append(Path(project_dir).expanduser() / "agents" if project_dir
                else Path.cwd() / "agents")
    dirs.append(_agents_home() / "agents")
    dirs.append(Path.home() / ".kiro" / "agents")
    bundled = _bundled_dir()
    if bundled:
        dirs.append(bundled)
    return dirs


def bundled_drift(project_dir=None, bundled: "Path | None" = None) -> "list[dict]":
    """同梱定義（リポジトリの agents/）と配布物（`~/.agents/agents/`）の食い違いを検出する
    （決定的・読み取り専用）。

    配布物は install.sh が同梱定義を cp して作る写しで、独自定義の置き場ではない
    （install.sh 自身が「この置き場は同梱定義の更新で上書きします」と案内する）。したがって
    ここに差があるのは常に「配り直し忘れ」——探索順（`plugin_dirs`）は配布物を同梱より先に
    解決するため、同梱定義を直しても実機は古い配布物で動き続け、修正が静かに届かない
    （実際に起きた: ollama.json の json_object_only 欠落で plan の器分岐が実機で不発、
    readonly_args の think 反転が届かない、claude/kiro の readonly 姿勢が古いまま——
    いずれも気づいてから手動 cp で直す、を短期間に 3 度繰り返した）。

    返り値は 1 定義 1 レコード:
      {"name", "bundled", "dist", "reason": "differs"|"missing", "resolved"}
    - 同梱 dir が無い（zipapp 配布で動いている）＝配布物が正なので検査対象なし。
    - 配布 dir が無い（install.sh を一度も実行していない開発機）も無言——リポジトリ直接
      実行では探索順の最後で同梱定義が解決されるので、写しが無いことは害にならない。
    - 探索順で配布物より上位（$KIRO_AGENTS_DIR・プロジェクトの agents/・~/.kiro/agents）の
      別ファイルが勝つ名前は対象外。first-wins の上書きは契約（上に置けば同梱定義を
      上書きできる）で、意図した上書きへ恒久警告を出さない。
    - 配布物が **無い** のも所見にする（reason="missing"）。zipapp 配布のエンジンは同梱
      定義を持ち出せないため、配られていない定義は配布インストールでは未知の agent_cli
      になる——新しい定義を足して配り忘れる、は差分と同じドリフトの一種。
    """
    src_dir = Path(bundled) if bundled else _bundled_dir()
    if src_dir is None:
        return []
    src_dir = src_dir.resolve()
    dist_dir = (_agents_home() / "agents").resolve()
    if src_dir == dist_dir or not dist_dir.is_dir():
        return []
    out: "list[dict]" = []
    for src in sorted(src_dir.glob("*.json")):
        name = src.stem
        try:
            resolved = next((d / f"{name}.json" for d in plugin_dirs(project_dir)
                             if (d / f"{name}.json").is_file()), None)
            if resolved is not None:
                resolved = resolved.resolve()
                if resolved.parent not in (src_dir, dist_dir):
                    continue            # 意図した上書きが勝っている（first-wins は契約）
            dist = dist_dir / f"{name}.json"
            if not dist.is_file():
                reason = "missing"
            elif (hashlib.sha256(dist.read_bytes()).hexdigest()
                  != hashlib.sha256(src.read_bytes()).hexdigest()):
                reason = "differs"
            else:
                continue
        except OSError:
            continue                    # 読めないものは判定不能（誤検知よりノイズの少なさ）
        out.append({"name": name, "bundled": str(src), "dist": str(dist),
                    "reason": reason, "resolved": str(resolved or src)})
    return out


def _strs(raw, field: str, path) -> "list[str]":
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise AgentCliError(f"エージェント定義 {path}: {field} は文字列配列が必要です")
    return list(raw)


def normalize(name: str, raw: dict, path) -> dict:
    """定義を正規化する。壊れた定義は黙って無視せず例外（設定ミスの静かな握り潰しを作らない）。"""
    if not isinstance(raw, dict):
        raise AgentCliError(f"エージェント定義 {path}: オブジェクトが必要です")
    command = _strs(raw.get("command"), "command", path)
    if not command:
        raise AgentCliError(f"エージェント定義 {path}: command は 1 要素以上の文字列配列が必須です")
    output = str(raw.get("output", "stdout"))
    if output not in ("stdout", "file"):
        raise AgentCliError(f"エージェント定義 {path}: output は stdout か file です")
    if output == "file" and not any("{output_file}" in c for c in command):
        raise AgentCliError(f"エージェント定義 {path}: output=file には command 中の "
                            "{output_file} プレースホルダが必要です")
    prompt_via = str(raw.get("prompt_via", "stdin"))
    if prompt_via not in ("stdin", "argv"):
        raise AgentCliError(f"エージェント定義 {path}: prompt_via は stdin か argv です")
    # 省略と空は許すが、型が違うものは `or {}` で握り潰さない（[] / "" が通ってしまう）。
    if "env" not in raw or raw.get("env") is None:
        env_raw: dict = {}
    else:
        env_raw = raw.get("env")
        if not isinstance(env_raw, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()):
            raise AgentCliError(f"エージェント定義 {path}: env は文字列→文字列のオブジェクトです")
    if "errors" not in raw or raw.get("errors") is None:
        errors_raw: list = []
    else:
        errors_raw = raw.get("errors")
        if not isinstance(errors_raw, list):
            raise AgentCliError(f"エージェント定義 {path}: errors はオブジェクト配列です")
    errors = []
    error_details = []
    for e in errors_raw:
        if not isinstance(e, dict):
            raise AgentCliError(f"エージェント定義 {path}: errors[] はオブジェクト配列です")
        try:
            cls = str(e.get("class", "env"))
            pattern = re.compile(str(e.get("match", "")), re.I)
            hint = str(e.get("hint", ""))
            quota_kind = str(e.get("quota_kind") or ("exhausted" if cls == "quota" else ""))
            if quota_kind not in ("", "exhausted", "rate_limit"):
                raise AgentCliError(
                    f"エージェント定義 {path}: errors.quota_kind が不正です: {quota_kind}")
            errors.append((cls, pattern, hint))
            error_details.append({"class": cls, "pattern": pattern, "hint": hint,
                                  "quota_kind": quota_kind})
        except re.error as ex:
            raise AgentCliError(
                f"エージェント定義 {path}: errors.match が正規表現として不正です: {ex}") from ex
    if "spill" not in raw or raw.get("spill") is None:
        spill_raw: dict = {}
    else:
        spill_raw = raw.get("spill")
        if not isinstance(spill_raw, dict):
            raise AgentCliError(f"エージェント定義 {path}: spill はオブジェクトです")
    if "interactive" not in raw or raw.get("interactive") is None:
        inter_raw: dict = {}
    else:
        inter_raw = raw.get("interactive")
        if not isinstance(inter_raw, dict):
            raise AgentCliError(f"エージェント定義 {path}: interactive はオブジェクトです")
    if "variants" not in raw or raw.get("variants") is None:
        variants_raw: dict = {}
    else:
        variants_raw = raw.get("variants")
        if not isinstance(variants_raw, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in variants_raw.items()):
            raise AgentCliError(f"エージェント定義 {path}: variants は文字列→文字列のオブジェクトです")

    profiles_raw = raw.get("profiles") or {}
    if not isinstance(profiles_raw, dict):
        raise AgentCliError(f"エージェント定義 {path}: profiles はオブジェクトです")
    for pname, pbody in profiles_raw.items():
        if not isinstance(pname, str) or not re.fullmatch(r"[\w.-]+", pname):
            raise AgentCliError(f"エージェント定義 {path}: profile 名が不正です: {pname!r}")
        if not isinstance(pbody, dict):
            raise AgentCliError(f"エージェント定義 {path}: profiles.{pname} はオブジェクトです")
        unknown = sorted(set(pbody) - set(_PROFILE_FIELDS))
        if unknown:
            raise AgentCliError(
                f"エージェント定義 {path}: profiles.{pname} が上書きできない項目を含みます: "
                f"{', '.join(unknown)}（上書きできるのは {', '.join(_PROFILE_FIELDS)}）")
        if not _strs(pbody.get("command"), f"profiles.{pname}.command", path):
            raise AgentCliError(
                f"エージェント定義 {path}: profiles.{pname}.command は 1 要素以上必要です")
    readonly = str(raw.get("readonly", "best-effort"))
    if readonly not in ("enforced", "best-effort"):
        raise AgentCliError(f"エージェント定義 {path}: readonly は enforced か best-effort です")
    # headless で 1 回起動したとき、この CLI が自分でツールを回して仕事を完遂できるか。
    # **interactive の有無や file_flag から推測しない**——定義の申告が正典。未宣言は安全側の
    # single-shot（呼び出し側がツールループを供給する）。
    headless_autonomy = str(raw.get("headless_autonomy") or "single-shot")
    if headless_autonomy not in ("tool-loop", "single-shot"):
        raise AgentCliError(
            f"エージェント定義 {path}: headless_autonomy は tool-loop か single-shot です")
    # 本文先頭のコマンド行（`/name [args]`）を、この CLI へ**残して渡す**か、ランチャが
    # **消費する**か。ネイティブのスラッシュを持つ CLI（claude / codex / agent-ollama …）は
    # 自分で解釈するので残して渡し、持たない CLI ではルータの解釈が実装そのものになる
    # （設計 2026-08-27 §3.2）。
    #
    # 未宣言のときは `headless_autonomy` から導く。**これは移行のための橋ではなく後方
    # 互換そのもの**——以前はこの判定が `headless_autonomy == "tool-loop"` という代理で
    # 書かれていたので、宣言していない定義（利用者が置いた `agents/<name>.json` を含む）は
    # 今日と同じに振る舞う。宣言があればそちらが勝つ。
    slash_native_raw = raw.get("slash_native")
    if slash_native_raw is not None and not isinstance(slash_native_raw, bool):
        raise AgentCliError(f"エージェント定義 {path}: slash_native は true か false です")
    slash_native = (bool(slash_native_raw) if slash_native_raw is not None
                    else headless_autonomy == "tool-loop")
    relative_cost = raw.get("relative_cost", 1)
    if (not isinstance(relative_cost, (int, float)) or isinstance(relative_cost, bool)
            or not math.isfinite(relative_cost) or relative_cost < 0):
        raise AgentCliError(f"エージェント定義 {path}: relative_cost は 0 以上の数値です")
    spec = {
        "name": str(raw.get("name") or name),
        "relative_cost": float(relative_cost),
        "path": str(path),
        "command": command,
        "command_suffix": _strs(raw.get("command_suffix"), "command_suffix", path),
        # スキル起動の行頭記号（既定 `/`。codex は `$skill-name`）。対話セッションへ
        # テキストを送る経路が skill_command_prefix() 経由で参照する。
        "skill_command_prefix": str(raw.get("skill_command_prefix") or "/"),
        "slash_native": slash_native,
        "prompt_via": prompt_via,
        "prompt_flag": raw.get("prompt_flag"),
        # 編集対象・読み取り専用のファイルを argv で受け取る CLI（aider）の口。宣言しない
        # CLI では呼び出し側がパスを渡しても無視される——プロンプト本文で伝える従来の
        # 作法のままで、定義を書き換えない限り argv は 1 トークンも変わらない。
        "file_flag": raw.get("file_flag"),
        "read_flag": raw.get("read_flag"),
        "model_flag": raw.get("model_flag"),
        "default_model": raw.get("default_model"),
        "output": output,
        "env": dict(env_raw),
        "timeout": raw.get("timeout"),
        "empty_output_is_error": bool(raw.get("empty_output_is_error", True)),
        # この起動形が **JSON オブジェクト 1 件しか返せない**か（ollama の `--format json` 等、
        # 制約付きデコードの器）。呼び出し側はこれを見て出力契約を選ぶ——配列契約を
        # 書いてよいか・1 件ずつ訊くべきかは器の性質であり、argv の綴りでは判定しない
        # （綴り判定を許すと定義を差し替えた日に静かに壊れる）。未宣言は False＝自由文。
        "json_object_only": bool(raw.get("json_object_only", False)),
        # 用途（role/kind/purpose 名。例 "planner" "split" "verify" "retrieve"）ごとに
        # 自動で振り替える変種の名前。空 = 変種なし。「どの用途で振り替えるか」はエンジン側の
        # 語彙（呼び出し元が対象の用途集合を持つ）、「その用途にはこの変種を使う」は
        # 定義側の申告——こう分けると、エンジンが CLI 名で分岐せずに済む（適用拡大設計 §4.3）。
        # variant は 1 つのエージェント（例 ollama）が用途で使い分ける実体を表す——
        # variant 先の定義自身の default_model も用途専用のチューニング（ollama-verify の
        # gemma4:12b 等）を持つため、振り替え時は cli と一緒にそちらへ寄せる。
        "variants": {str(k).strip().lower(): str(v).strip().lower()
                    for k, v in variants_raw.items() if str(k).strip() and str(v).strip()},
        "write_args": _strs(raw.get("write_args"), "write_args", path),
        "readonly_args": _strs(raw.get("readonly_args"), "readonly_args", path),
        "readonly": readonly,
        "no_session_args": _strs(raw.get("no_session_args"), "no_session_args", path),
        # セッション継続。**ネイティブの機能を持つ CLI だけが宣言する**——持たない CLI の
        # 継続は材料の再構築で、それは argv では表せない（仕様書 §3.3 / 設計 §4）。
        "continue_args": _strs(raw.get("continue_args"), "continue_args", path),
        "resume_args": _strs(raw.get("resume_args"), "resume_args", path),
        "headless_autonomy": headless_autonomy,
        "spill": {
            "args": _strs(spill_raw.get("args"), "spill.args", path),
            "instruction": str(spill_raw.get("instruction") or ""),
        },
        "errors": errors,
        "error_details": error_details,
        # 用途別の起動差（生のまま持つ。合成は _apply_profile が行う）。
        "profiles": profiles_raw,
        # この spec がどの profile で組まれたか（base のときは ""）。
        "profile": "",
        # profile 合成の入力（正規化を 2 通り持たないための保持。公開 API ではない）。
        "_raw": raw,
    }
    if inter_raw:
        icmd = _strs(inter_raw.get("command"), "interactive.command", path)
        if not icmd:
            raise AgentCliError(f"エージェント定義 {path}: interactive.command は 1 要素以上必要です")
        inject = str(inter_raw.get("prompt_inject", "send-keys"))
        if inject not in ("send-keys", "file"):
            raise AgentCliError(
                f"エージェント定義 {path}: interactive.prompt_inject は send-keys か file です")
        turn_completion = str(inter_raw.get("turn_completion") or "")
        if turn_completion not in ("", "kiro", "claude", "codex", "copilot", "ollama"):
            raise AgentCliError(
                f"エージェント定義 {path}: interactive.turn_completion が未知です: "
                f"{turn_completion!r}")
        spec["interactive"] = {
            "command": icmd,
            # 対話の既定モードで付けるフラグ。トップレベルの write_args はヘッドレス専用の
            # 危険フラグ（--dangerously-skip-permissions 等）を含むので継承しない
            "write_args": _strs(inter_raw.get("write_args"), "interactive.write_args", path),
            # 省略時はトップレベルを継承する（同じ知識を 2 度書かせない）
            "readonly_args": (_strs(inter_raw["readonly_args"], "interactive.readonly_args", path)
                              if "readonly_args" in inter_raw else spec["readonly_args"]),
            "no_session_args": (_strs(inter_raw["no_session_args"],
                                      "interactive.no_session_args", path)
                                if "no_session_args" in inter_raw else spec["no_session_args"]),
            "continue_args": (_strs(inter_raw["continue_args"], "interactive.continue_args", path)
                              if "continue_args" in inter_raw else spec["continue_args"]),
            "resume_args": (_strs(inter_raw["resume_args"], "interactive.resume_args", path)
                            if "resume_args" in inter_raw else spec["resume_args"]),
            "ready_pattern": str(inter_raw.get("ready_pattern") or ""),
            "ready_timeout_sec": float(inter_raw.get("ready_timeout_sec") or 60),
            "ready_tail_lines": max(1, int(inter_raw.get("ready_tail_lines") or 3)),
            # 待機/処理中の判定は CLI ごとに方法が違う（入力欄を出したまま処理する TUI では
            # ready_pattern の消失が起きない）。busy_pattern は「処理中」の正のシグナル、
            # idle_quiet_sec はパターンで判定できない CLI 向けの静穏判定。
            "busy_pattern": str(inter_raw.get("busy_pattern") or ""),
            "failure_pattern": str(inter_raw.get("failure_pattern") or ""),
            "idle_quiet_sec": float(inter_raw.get("idle_quiet_sec") or 0),
            # clear_command は「未指定 → 既定 /clear」と「空文字 → クリア手段なし」を区別する。
            "clear_command": (str(inter_raw["clear_command"])
                              if "clear_command" in inter_raw and inter_raw["clear_command"] is not None
                              else "/clear"),
            # save/exit は未定義または空 = 非サポート（推測しない）
            "save_command": str(inter_raw.get("save_command") or ""),
            "exit_command": str(inter_raw.get("exit_command") or ""),
            "prompt_inject": inject,
            "turn_completion": turn_completion,
        }
    else:
        spec["interactive"] = None
    return spec


_CACHE: "dict[str, dict]" = {}


def load_cli(name: str, project_dir=None, *, use_cache: bool = True) -> dict:
    """agents/<name>.json を探索順に読み、正規化して返す。

    見つからなければ AgentCliError。以前は未知の agent_cli が黙って kiro-cli へ落ちており、
    設定ミスに気づけなかった。組み込み名も定義ファイル化した今、「見つからない」は
    ほぼインストールの破損なので、そう読めるメッセージにする。
    """
    key = str(name or "").strip().lower()
    if not key or not re.fullmatch(r"[\w.-]+", key):
        raise AgentCliError(f"agent_cli の名前が不正です: {name!r}")
    cache_key = f"{key}\0{project_dir or ''}"
    if use_cache and cache_key in _CACHE:
        return _CACHE[cache_key]
    dirs = plugin_dirs(project_dir)
    for d in dirs:
        p = d / f"{key}.json"
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            if isinstance(e, ValueError):
                raise AgentCliError(f"エージェント定義 {p}: JSON として読めません: {e}") from e
            continue
        spec = normalize(key, raw, p)
        if use_cache:
            _CACHE[cache_key] = spec
        return spec
    # 定義ファイルが無ければ profile 名として解く（`ollama-list` → base=ollama / profile=list）。
    # 用途別の定義を 1 つへ畳んだので、従来の綴りはここを通る。**実ファイルが優先**なので、
    # `ollama-json.json` を置けばそれは独立したエージェントとして扱われる（後方互換）。
    resolved = _resolve_profile_name(key, project_dir)
    if resolved is not None:
        if use_cache:
            _CACHE[cache_key] = resolved
        return resolved
    raise AgentCliError(
        f"未知の agent_cli です: {key!r}（agents/{key}.json が見つかりません）\n"
        f"  探索順: {' → '.join(str(d) for d in dirs)}\n"
        "  組み込み CLI もこの定義ファイルで動きます。インストールが壊れている可能性が"
        "あります（install.sh の再実行を検討してください）。")


def _resolve_profile_name(key: str, project_dir) -> "dict | None":
    """`<base>-<profile>` を base 定義の profile として解く。該当が無ければ None。

    区切りは `-` で、**長い base から順に**試す（`ollama-list-thinking` は
    base=`ollama-list` の profile=`thinking` ではなく base=`ollama` の
    profile=`list-thinking` として解けるが、前者の定義が実在するならそちらが勝つ）。
    """
    parts = key.split("-")
    for cut in range(len(parts) - 1, 0, -1):
        base_name, profile = "-".join(parts[:cut]), "-".join(parts[cut:])
        try:
            base = load_cli(base_name, project_dir)
        except AgentCliError:
            continue
        if profile in (base.get("profiles") or {}):
            return _apply_profile(base, profile, base["path"])
    return None


def clear_cache() -> None:
    _CACHE.clear()


def resolve_variant(name: str, purpose: str, project_dir=None) -> "dict | None":
    """用途（role/kind/purpose 名）に使う変種（cli + 既定モデル）。無ければ None。

    ollama や aider のように 1 つのエージェントが用途で使い分ける実体（tool-loop 前提の
    ツールループ役、JSON 専用役、配列専用役、検証専用役…）を、呼び出し元は「変種を
    持つかもしれない 1 つのエージェント」として選ぶだけでよい——用途ごとにどの変種を
    使うかは定義側（agents/<name>.json の `variants`）が申告し、この関数が引く。

    呼び出し元（agent-flow / agent-project）は、どの用途で振り替えを試みるかの集合
    （例: JSON 契約の役割、配列契約の役割、検証役）を自分で持つ——契約の性質を知って
    いるのは呼び出し元で、CLI 名で分岐させない（適用拡大設計 §4.3 の分業を維持）。

    変種は自分自身の `default_model` を持つことが多い（例: ollama-verify の gemma4:12b
    は検証用にチューニングされた既定）。呼び出し元が明示的にモデルを指定していなければ、
    その既定モデルも一緒に返す——base 定義で選んだモデルは「base をそのまま使う用途」に
    しか効かず、変種へ振り替わった用途では変種自身の調整が優先される。

    申告先が実在しない・自分自身を指す場合は None（設定ミスで実行を殺さない）。
    """
    key = str(name or "").strip().lower()
    purpose_key = str(purpose or "").strip().lower()
    if not purpose_key:
        return None
    try:
        spec = load_cli(key, project_dir)
    except AgentCliError:
        return None
    variant = str((spec.get("variants") or {}).get(purpose_key) or "")
    if not variant or variant == key:
        return None
    try:
        variant_spec = load_cli(variant, project_dir)
    except AgentCliError:
        return None
    return {"agent_cli": variant, "default_model": variant_spec.get("default_model") or None}


def costlier_fallback(current: str, candidates, project_dir=None) -> "dict | None":
    """宣言順の候補から、現在より相対コストが高い最初の 1 件だけを返す。"""
    try:
        base = load_cli(current, project_dir)
    except AgentCliError:
        return None
    for raw in candidates if isinstance(candidates, list) else []:
        if not isinstance(raw, dict) or not str(raw.get("agent_cli") or "").strip():
            continue
        cli = str(raw["agent_cli"]).strip().lower()
        try:
            spec = load_cli(cli, project_dir)
        except AgentCliError:
            continue
        if spec["relative_cost"] <= base["relative_cost"]:
            continue
        return {"agent_cli": cli, "model": str(raw.get("model") or "").strip(),
                "from_relative_cost": base["relative_cost"],
                "to_relative_cost": spec["relative_cost"]}
    return None


def session_args(spec: dict, *, interactive: bool = False, resume: str = "") -> "list[str]":
    """セッション継続の argv 断片。宣言が無ければ空リスト（＝ネイティブ機能を持たない）。

    `resume` を渡すと `resume_args` の `{session}` を埋める。continue（直近）と resume（ID
    指定）で別の綴りを持つ CLI があるので、宣言も 2 つに分ける（claude の `--continue` と
    `--resume <id>`）。
    """
    src = spec["interactive"] if interactive and spec.get("interactive") else spec
    if resume:
        return [tok.replace("{session}", resume) for tok in src.get("resume_args") or []]
    return list(src.get("continue_args") or [])


def _insert_session_args(argv: "list[str]", fragment: "list[str]") -> "list[str]":
    """継続の断片を**サブコマンドの直後・オプションの前**へ差し込む。

    末尾へ足せない CLI があるからである——codex の継続は `codex exec resume --last` という
    サブコマンドで、`exec` のオプション列の後ろに置くと別の意味になる。一方フラグ形
    （claude の `--continue`）はどこに置いても同じなので、この位置なら両方が成り立つ。
    先頭から連続する非オプションのトークン（プログラム名とサブコマンド）が境界である。
    """
    if not fragment:
        return argv
    at = 0
    while at < len(argv) and not argv[at].startswith("-"):
        at += 1
    return argv[:at] + list(fragment) + argv[at:]


def _mode_args(spec: dict, *, interactive: bool, readonly: bool, no_session: bool) -> "list[str]":
    src = spec["interactive"] if interactive and spec.get("interactive") else spec
    args = list(src["readonly_args"]) if readonly else list(src["write_args"])
    if no_session:
        for tok in src["no_session_args"]:
            if tok not in args:          # readonly と no_session が同じフラグを持つ CLI の重複を防ぐ
                args.append(tok)
    return args


def _expand(tokens, model: str, output_file_holder: dict) -> "list[str]":
    out: "list[str]" = []
    for tok in tokens:
        if "{model}" in tok:
            if not model:
                continue                 # モデル未指定なら {model} を含むトークンごと落とす
            out.append(tok.replace("{model}", model))
            continue
        if "{output_file}" in tok:
            if not output_file_holder.get("path"):
                fd, path = tempfile.mkstemp(prefix="agentcli-", suffix=".txt")
                os.close(fd)
                output_file_holder["path"] = path
            out.append(tok.replace("{output_file}", output_file_holder["path"]))
            continue
        out.append(tok)
    return out


DEFAULT_ARGV_LIMIT = 100000
"""argv でプロンプトを渡すときの既定の上限バイト数（`spill_prompt`）。

OS の `ARG_MAX`（Linux で概ね 2MB・環境変数と共有）より十分小さく取る。超えると
`execve` が E2BIG で失敗し、プロセス起動そのものが立たない。"""


def spill_instruction(what: str, *, then: str = "その指示に従ってください") -> str:
    """argv 退避時に本文の代わりに渡す短い指示（`{file}` を含む・P2-5）。

    **枠だけをここに置く。** 呼び出し側が決めるのは `what`（何の全文か）と `then`（読んだ
    あと何をするか）で、役割ごとに違うのはそこだけ。「必ず読み込ませる」という**効き目に
    関わる部分は共通**なので、3 者が全文を自前で持つと、言い回しの改善が 1 か所にしか
    入らない（そして入っていない方は誰も気付かない）。

    **定義側の `spill.instruction`（`agents/<cli>.json`）とは別物**。あちらは権限フラグの
    置き換えを伴う読み取り専用の退避モード用で、`headless_cmd(spill_path=…)` が使う
    （Python からの消費者は無く、dashboard の診断だけ）。混同しないよう、こちらを使うのは
    `spill_prompt` 経由に限る。
    """
    return f"以下のファイルに{what}があります。必ずファイルの内容を読み込み、{then}: {{file}}"


def spill_prompt(prompt: str, limit: "int | None" = None, *, prompt_via: str,
                 prefix: str, instruction: str) -> "tuple[str | None, str]":
    """argv 長制限を超えるプロンプトを一時ファイルへ退避し、`(退避先, 短い指示)` を返す。

    退避が要らなければ `(None, prompt)`。退避先の削除は呼び出し側の責務
    （`subprocess.run` を囲む `finally`）——ここは実行しないので寿命を知らない。

    **`headless_cmd(spill_path=…)` とは別物**。あちらは定義の `spill.args` で
    **権限フラグを置き換える**（kiro-cli では `--trust-all-tools` → `--trust-tools=fs_read`）
    ので、コマンドを実行して確かめる用途のヘッドレス呼び出し（検証エージェント等）に
    使うと実行権限ごと失われる。ここが見ているのは OS の `ARG_MAX` であって CLI の癖では
    ないので、**権限フラグには触らない**。

    `instruction` は `{file}` を含む呼び出し側の文（「何の全文か」は役割ごとに違う）。
    """
    if str(prompt_via) != "argv":
        return None, prompt                  # stdin 渡しは ARG_MAX に当たらない
    cap = int(limit) if limit and int(limit) > 0 else DEFAULT_ARGV_LIMIT
    if len(str(prompt).encode("utf-8")) <= cap:
        return None, prompt
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(prompt))
    return path, str(instruction).replace("{file}", path)


def headless_cmd(spec: dict, model: "str | None", prompt: str, *,
                 readonly: bool = False, no_session: bool = False,
                 spill_path: "str | None" = None,
                 files: "list[str] | tuple | None" = None,
                 read_files: "list[str] | tuple | None" = None,
                 session_continue: bool = False, session_id: str = "") -> dict:
    """ヘッドレス 1 回分を組み立てる（実行はしない・決定的）。

    戻り値: {argv, stdin, output_file, env, empty_output_is_error, timeout, readonly_warning}
    spill_path を渡すと、渡された prompt の末尾へ spill.instruction（{file} 置換済み）を足し、
    権限フラグを spill.args で置き換える（kiro-cli が positional プロンプト併用時に stdin を
    読まない癖への対処）。本文はファイル側にある前提で、prompt には短い指示だけを渡す。

    **argv 長制限の退避には使わない**（`spill_prompt` を使う）。権限フラグの置き換えは
    「本文を読ませるために読み取りだけ許す」読み取り専用の用途に閉じた振る舞いで、
    実行して確かめる呼び出しに掛けると、退避したときだけ何も実行できなくなる。
    """
    m = str(model or spec.get("default_model") or "")
    holder: dict = {}
    argv = _expand(spec["command"], m, holder)
    use_spill = bool(spill_path and spec["spill"]["instruction"])
    mode = _mode_args(spec, interactive=False, readonly=readonly, no_session=no_session)
    if use_spill and spec["spill"]["args"]:
        # 退避時の権限フラグは、そのモードの権限フラグを **置き換える**。退避は「本文を読ませる
        # ためにファイル読み取りだけは許す」という固有のモードで、追加にすると kiro のように
        # `--trust-tools=` と `--trust-tools=fs_read` が並んで後勝ちに賭けることになる。
        mode = [t for t in mode if t not in spec["readonly_args"] and t not in spec["write_args"]]
        mode = list(spec["spill"]["args"]) + mode
    argv += _expand(mode, m, holder)
    if m and spec["model_flag"] and not any("{model}" in t for t in spec["command"]):
        argv += [str(spec["model_flag"]), m]
    argv += _expand(spec["command_suffix"], m, holder)
    if session_continue or session_id:
        argv = _insert_session_args(argv, session_args(spec, resume=session_id))
    # ファイルの受け渡し。**宣言した CLI にだけ載る**——aider は「チャットに入っている
    # ファイルしか編集しない」ので、渡さないと本文で「追加してくれ」と要求して終わる
    # （1 発起動では答える人がいない＝着手すらしない）。実測でこの取りこぼしを
    # モデルの不合格として数えかけた（2026-08-11）。
    for flag, paths in ((spec.get("read_flag"), read_files), (spec.get("file_flag"), files)):
        for path in (paths or ()):
            if flag:
                argv += [str(flag), str(path)]

    text = str(prompt if prompt is not None else "")
    if use_spill:
        # 指示は **置き換えず付け足す**。呼び出し側の指示（役割・出力書式）はプロンプト本文とは
        # 別に argv へ載っていることがあり（Doctor がまさにそれ）、置き換えると役割ごと消える。
        text = (text + " " if text else "") + \
            spec["spill"]["instruction"].replace("{file}", str(spill_path))
    stdin = None
    if spec["prompt_via"] == "argv":
        if spec["prompt_flag"]:
            argv += [str(spec["prompt_flag"]), text]
        else:
            argv.append(text)
    else:
        stdin = text
    return {
        "argv": argv,
        "stdin": stdin,
        "output_file": holder.get("path"),
        "env": dict(spec["env"]),
        "empty_output_is_error": spec["empty_output_is_error"],
        "timeout": spec["timeout"],
        "readonly_warning": readonly_warning(spec, readonly),
    }


def interactive_cmd(spec: dict, model: "str | None", *,
                    readonly: bool = False, no_session: bool = False,
                    session_continue: bool = False, session_id: str = "") -> "list[str]":
    """対話起動 argv を組み立てる。interactive セクションを持たない定義は AgentCliError。"""
    inter = spec.get("interactive")
    if not inter:
        raise AgentCliError(
            f"{spec['name']} は対話起動に対応していません"
            f"（{spec['path']} に interactive.command がありません）")
    m = str(model or spec.get("default_model") or "")
    if any("{model}" in t for t in inter["command"]) and not m:
        raise AgentCliError(f"{spec['name']} の対話起動にはモデルの指定が必要です")
    holder: dict = {}
    argv = _expand(inter["command"], m, holder)
    argv += _expand(_mode_args(spec, interactive=True, readonly=readonly,
                               no_session=no_session), m, holder)
    if m and spec["model_flag"] and not any("{model}" in t for t in inter["command"]):
        argv += [str(spec["model_flag"]), m]
    if session_continue or session_id:
        argv = _insert_session_args(
            argv, session_args(spec, interactive=True, resume=session_id))
    if holder.get("path"):               # 対話起動でファイル出力は意味を成さない
        raise AgentCliError(f"{spec['name']} の interactive.command に {{output_file}} は使えません")
    return argv


def readonly_warning(spec: dict, readonly: bool) -> str:
    """読み取り専用を要求したが CLI が保証しないときの警告文（S9 未決 7 の決着）。

    このレイヤは宣言どおりの argv を組み立てるだけで、フラグを無視する CLI への防御は持たない。
    できるのは「保証できない」ことを人に伝えることだけなので、判断材料として返す。
    """
    if not readonly or spec["readonly"] == "enforced":
        return ""
    return (f"{spec['name']} は読み取り専用を保証しません"
            "（助言のみのつもりでもファイル変更やコマンド実行が起こりえます）")


def ready_pattern(spec: dict, default: str = "") -> str:
    inter = spec.get("interactive") or {}
    return inter.get("ready_pattern") or default


def ready_timeout_sec(spec: dict, default: float = 60) -> float:
    inter = spec.get("interactive") or {}
    return float(inter.get("ready_timeout_sec") or default)


def prompt_inject(spec: dict) -> str:
    inter = spec.get("interactive") or {}
    return str(inter.get("prompt_inject") or "send-keys")


def busy_pattern(spec: dict, default: str = "") -> str:
    """「処理中」を正に検出する ERE（無ければ空 = ready_pattern 非マッチを処理中とみなす従来法）。"""
    inter = spec.get("interactive") or {}
    return inter.get("busy_pattern") or default


def idle_quiet_sec(spec: dict, default: float = 0) -> float:
    """パターン判定不能時の静穏判定秒数（0 = 無効）。"""
    inter = spec.get("interactive") or {}
    return float(inter.get("idle_quiet_sec") or default)


def clear_command(spec: dict) -> str:
    """コンテキスト破棄コマンド。既定 /clear、空文字はクリア手段なし。"""
    inter = spec.get("interactive")
    if not inter:
        return "/clear"
    return str(inter.get("clear_command", "/clear"))


_RESET_AT_RE = re.compile(
    r"(?:reset(?:s)?(?:\s+at)?|available\s+at)\s*[:=]?\s*"
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2}))",
    re.I,
)
_RETRY_AFTER_RE = re.compile(
    r"(?:retry(?:\s+after)?|try\s+again\s+in|reset(?:s)?\s+in|retry-after\s*:?)\s*"
    r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b",
    re.I,
)


def _quota_reset_at(text: str, now) -> "str | None":
    absolute = _RESET_AT_RE.search(text)
    if absolute:
        try:
            dt = datetime.fromisoformat(absolute.group(1).replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    relative = _RETRY_AFTER_RE.search(text)
    if not relative:
        return None
    amount = float(relative.group(1))
    unit = relative.group(2).lower()[0]
    seconds = amount * ({"s": 1, "m": 60, "h": 3600}[unit])
    if isinstance(now, datetime):
        base = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    else:
        base = datetime.fromtimestamp(float(now), timezone.utc)
    return (base.astimezone(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def classify_error(spec: dict, blob: str, *, detailed: bool = False, now=None):
    """定義の errors[] で失敗本文を分類する。

    既存呼出しは `(class, hint)` のまま。`detailed=True` で quota の細分と
    復帰時刻を返す。相対時刻の抽出時は `now` を必須にし、現在時刻を隠れた入力にしない。"""
    text = str(blob or "")
    details = spec.get("error_details")
    if details is None:
        details = ({"class": cls, "pattern": pattern, "hint": hint, "quota_kind": ""}
                   for cls, pattern, hint in spec.get("errors", []))
    for rule in details:
        if rule["pattern"].search(text):
            if not detailed:
                return rule["class"], rule["hint"]
            quota_kind = rule.get("quota_kind") or None
            reset_at = (_quota_reset_at(text, now) if quota_kind == "rate_limit" and now is not None
                        else None)
            return {"class": rule["class"], "hint": rule["hint"],
                    "quota_kind": quota_kind, "reset_at": reset_at}
    return None


def skill_command_prefix(spec: dict) -> str:
    """スキル起動コマンドの行頭記号（既定 `/`。codex は `$`）。"""
    return str(spec.get("skill_command_prefix") or "/")


_SKILL_CMD_RE = re.compile(r"^/(?=[A-Za-z0-9_-]+(?:[\s:]|$))", re.M)


def rewrite_skill_commands(text: str, prefix: str) -> str:
    """セッションへ送るテキストの行頭 `/` を、その CLI のスキル起動記号へ差し替える。

    人は `/skill-name` と書くが、codex は `$skill-name` でないとスキルが起動しない。
    既定 `/` の CLI では何も変えない。行頭が `/` + 英数字トークンのときだけ対象にするので、
    パス（`/home/...`）は 2 つ目の `/` があるため書き換わらない。
    """
    p = str(prefix or "/")
    s = "" if text is None else str(text)
    return s if p == "/" else _SKILL_CMD_RE.sub(p, s)
