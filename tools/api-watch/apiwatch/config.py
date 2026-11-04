"""設定ファイルの読み込みと検査。

設定は YAML（PyYAML が要る）か JSON。項目は README の「設定」を参照。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import adapters, jpath, template
from .connections import Connections, load_yaml_or_json, parse_ref
from .util import ConfigError, canonical, sha

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
EVENT_KINDS = ("appeared", "changed", "entered", "exited", "matching", "removed")
PAGINATION_TYPES = ("none", "page", "offset", "cursor", "next_url", "link_header")
ORDER_MODES = ("completion", "enqueue", "none")
DEFAULT_CONFIG = "~/.config/api-watch/api-watch.yaml"
DEFAULT_STATE_DIR = "~/.local/state/api-watch"


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def _num(d: dict, key: str, default: float, where: str, minimum: float = 0) -> float:
    v = d.get(key, default)
    _expect(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= minimum,
            f"{where}.{key}: {minimum} 以上の数を書いてください")
    return float(v)


def _int(d: dict, key: str, default: int, where: str, minimum: int = 0) -> int:
    v = d.get(key, default)
    _expect(isinstance(v, int) and not isinstance(v, bool) and v >= minimum,
            f"{where}.{key}: {minimum} 以上の整数を書いてください")
    return int(v)


def _paths(v: Any, where: str) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    _expect(isinstance(v, list) and all(isinstance(x, str) for x in v), f"{where}: パスの配列を書いてください")
    for p in v:
        jpath.parse(p)
    return list(v)


def _known(d: dict, keys: set[str], where: str) -> None:
    unknown = sorted(str(k) for k in set(d) - keys)
    _expect(not unknown, f"{where}: 知らない項目です: {', '.join(unknown)}")


# ---------------------------------------------------------------------------


@dataclass
class Pagination:
    type: str = "none"
    location: str = "query"         # query | body（ページ番号などを置く場所）
    param: str | None = None
    start: int = 0
    size_param: str | None = None
    size: int | None = None
    cursor_path: str | None = None
    has_more_path: str | None = None
    next_url_path: str | None = None
    total_path: str | None = None
    total_pages_path: str | None = None
    max_pages: int = 1000
    allow_cross_host: bool = False


@dataclass
class Incremental:
    type: str                       # max_value | cursor
    param: str | None = None
    location: str = "query"
    field: str | None = None        # max_value: レコードのどの値の最大を覚えるか
    cursor_path: str | None = None  # cursor: 応答のどこに次回のカーソルがあるか
    initial: Any = None


@dataclass
class SourceSpec:
    name: str
    raw: dict
    connection: tuple[str, str] | None
    method: str
    url: str
    headers: dict
    query: dict
    body: Any
    body_form: dict | None
    timeout_sec: float
    records: str
    key: str | None
    pagination: Pagination
    incremental: Incremental | None
    schedule: str
    min_interval_sec: float
    jitter_sec: float
    busy_wait_sec: float
    rate_min_interval_sec: float
    rate_per_minute: int
    retry_attempts: int
    retry_backoff_sec: float
    retry_max_backoff_sec: float
    max_wait_sec: float
    conditional: bool
    resume: bool
    resume_max_age_sec: float
    lease_sec: float

    def fingerprint(self, conn: dict | None) -> str:
        """取得条件・認証の指紋。変わったら別のデータとして扱い、過去の取得結果や位置を使わない。"""
        cfg = {k: v for k, v in self.raw.items()
               if k not in ("schedule", "min_interval_sec", "jitter_sec", "busy_wait_sec", "rate_limit", "retry",
                            "resume", "resume_max_age_sec", "lease_sec", "conditional", "description")}
        cred = sha(canonical(conn), 32) if conn else None
        return sha(canonical({"cfg": cfg, "conn": list(self.connection) if self.connection else None, "cred": cred}), 32)


@dataclass
class TargetSpec:
    name: str
    workflow: str | None            # 受け渡し先のワークフロー（アダプタで渡す）
    params: dict
    queue: str | None
    order: str
    max_attempts: int
    retry_backoff_sec: float
    retry_max_backoff_sec: float
    inline_payload: bool
    command: list | None = None     # その場で実行するコマンド（work が実行する。アダプタを使わない）
    timeout_sec: float = 3600

    @property
    def runs_here(self) -> bool:
        """受け渡し先へ渡さず、api-watch の work がその場でコマンドを実行する受け渡し先か。"""
        return self.command is not None


@dataclass
class RuleSpec:
    name: str
    source: str
    raw: dict
    filter: dict | None
    id_paths: list[str]
    watch: list[str] | None
    ignore: list[str]
    array_order: str                # respect | ignore
    unordered: list[str]
    missing_is_null: bool
    condition: dict | None
    on: list[str]
    initial: str                    # baseline | emit
    on_rule_change: str             # rebaseline | continue
    targets: list[TargetSpec]
    include_record: bool
    schedule: str | None = None     # その場で実行する受け渡し先があるとき、dags が書き出すワークフローの起動間隔

    def fingerprint(self) -> str:
        """判定の仕方の指紋。変わると、前回の観測値とは比べ方が違うので基準を取り直す。"""
        keys = ("filter", "id", "watch", "ignore", "array_order", "unordered", "missing_is_null", "condition")
        return sha(canonical({k: self.raw.get(k) for k in keys}), 32)


@dataclass
class DeliverySpec:
    """受け渡しのアダプタ。名前と設定だけを持ち、アダプタを使う時点で解決する。

    アダプタを使わないコマンド（status・events・ack など）は、プラグインを読み込まずに動く。
    """
    adapter: str
    options: dict
    _resolved: Any = None

    def resolve(self) -> tuple[Any, Any]:
        """(アダプタの型, 検査済みの設定)。アダプタが無ければ ConfigError（--plugin の案内つき）。"""
        if self._resolved is None:
            t = adapters.get(self.adapter)
            self._resolved = (t, t.parse(dict(self.options), "delivery"))
        return self._resolved

    def create(self, connections: Any):
        t, spec = self.resolve()
        return t.create(spec, connections)

    @property
    def timeout_sec(self) -> float:
        return float(getattr(self.resolve()[1], "timeout_sec", 60))


@dataclass
class Retention:
    fetches_keep: int = 3
    fetch_days: float = 7
    events_days: float = 30
    removed_days: float = 30          # 消えた・対象外になったレコードの前回の観測値を残す日数
    record_days: float | None = None  # 増分取得で組み立てた全体の姿から、この日数取得されないレコードを消す（既定は消さない）
    auto_gc: bool = True


@dataclass
class Config:
    path: Path
    state_dir: Path
    sources: dict[str, SourceSpec]
    rules: dict[str, RuleSpec]
    delivery: DeliverySpec | None
    retention: Retention
    connections: Connections

    def rules_for(self, source: str) -> list[RuleSpec]:
        return [r for r in self.rules.values() if r.source == source]

    def resolve_connection(self, ref: tuple[str, str] | None) -> dict | None:
        if ref is None:
            return None
        return self.connections.get(*ref)


# ---------------------------------------------------------------------------


def _pagination(d: Any, where: str) -> Pagination:
    if d is None:
        return Pagination()
    if isinstance(d, str):
        d = {"type": d}
    _expect(isinstance(d, dict), f"{where}: pagination は辞書で書いてください")
    _known(d, {"type", "in", "param", "start", "size_param", "size", "cursor_path", "has_more_path",
               "next_url_path", "total_path", "total_pages_path", "max_pages", "allow_cross_host"}, where)
    t = d.get("type", "none")
    _expect(t in PAGINATION_TYPES, f"{where}.type: {' / '.join(PAGINATION_TYPES)} のどれかです")
    p = Pagination(type=t, location=d.get("in", "query"))
    _expect(p.location in ("query", "body"), f"{where}.in: query か body です")
    p.max_pages = _int(d, "max_pages", 1000, where, 1)
    p.allow_cross_host = bool(d.get("allow_cross_host", False))
    for k in ("cursor_path", "has_more_path", "next_url_path", "total_path", "total_pages_path"):
        if d.get(k) is not None:
            jpath.parse(str(d[k]))
            setattr(p, k, str(d[k]))
    if t == "page":
        p.param = d.get("param", "page")
        p.start = _int(d, "start", 1, where)
        p.size_param = d.get("size_param")
        p.size = d.get("size")
    elif t == "offset":
        p.param = d.get("param", "offset")
        p.start = _int(d, "start", 0, where)
        p.size_param = d.get("size_param", "limit")
        _expect("size" in d, f"{where}.size: offset 方式では 1 ページの件数（size）が要ります")
        p.size = d.get("size")
    elif t == "cursor":
        p.param = d.get("param", "cursor")
        _expect(p.cursor_path is not None, f"{where}.cursor_path: 応答の中の次のカーソルの場所を書いてください")
    elif t == "next_url":
        _expect(p.next_url_path is not None, f"{where}.next_url_path: 応答の中の次ページ URL の場所を書いてください")
    if p.size is not None:
        _expect(isinstance(p.size, int) and p.size > 0, f"{where}.size: 1 以上の整数を書いてください")
    return p


def _incremental(d: Any, where: str) -> Incremental | None:
    if d is None:
        return None
    _expect(isinstance(d, dict), f"{where}: incremental は辞書で書いてください")
    _known(d, {"type", "param", "in", "field", "cursor_path", "initial"}, where)
    t = d.get("type", "max_value")
    _expect(t in ("max_value", "cursor"), f"{where}.type: max_value か cursor です")
    inc = Incremental(type=t, param=d.get("param"), location=d.get("in", "query"), initial=d.get("initial"))
    _expect(inc.location in ("query", "body"), f"{where}.in: query か body です")
    if t == "max_value":
        _expect(isinstance(d.get("field"), str), f"{where}.field: 最大値を覚えるレコードの項目（例: updated_at）を書いてください")
        jpath.parse(d["field"])
        inc.field = d["field"]
    else:
        _expect(isinstance(d.get("cursor_path"), str), f"{where}.cursor_path: 応答の中の次回のカーソルの場所を書いてください")
        jpath.parse(d["cursor_path"])
        inc.cursor_path = d["cursor_path"]
    return inc


def _source(name: str, d: Any) -> SourceSpec:
    where = f"sources.{name}"
    _expect(NAME_RE.match(name) is not None, f"{where}: 名前は英数字・- _ . で 64 文字以内にしてください")
    _expect(isinstance(d, dict), f"{where}: 辞書で書いてください")
    _known(d, {"description", "connection", "request", "records", "key", "pagination", "incremental", "schedule",
               "min_interval_sec", "jitter_sec", "busy_wait_sec", "rate_limit", "retry", "conditional", "resume",
               "resume_max_age_sec", "lease_sec"}, where)
    req = d.get("request")
    _expect(isinstance(req, dict) and isinstance(req.get("url"), str), f"{where}.request.url を書いてください")
    _known(req, {"method", "url", "headers", "query", "body", "body_form", "timeout_sec"}, f"{where}.request")
    method = str(req.get("method", "GET")).upper()
    headers = req.get("headers") or {}
    query = req.get("query") or {}
    _expect(isinstance(headers, dict), f"{where}.request.headers: 辞書で書いてください")
    _expect(isinstance(query, dict), f"{where}.request.query: 辞書で書いてください")
    body_form = req.get("body_form")
    _expect(body_form is None or isinstance(body_form, dict), f"{where}.request.body_form: 辞書で書いてください")
    _expect(not (body_form is not None and req.get("body") is not None),
            f"{where}.request: body と body_form はどちらか一方にしてください")
    rl = d.get("rate_limit") or {}
    rt = d.get("retry") or {}
    _expect(isinstance(rl, dict) and isinstance(rt, dict), f"{where}: rate_limit / retry は辞書で書いてください")
    _known(rl, {"min_interval_sec", "per_minute", "max_wait_sec"}, f"{where}.rate_limit")
    _known(rt, {"attempts", "backoff_sec", "max_backoff_sec"}, f"{where}.retry")
    records = d.get("records", "$")
    jpath.parse(records)
    key = d.get("key")
    if key is not None:
        _expect(isinstance(key, str), f"{where}.key: レコードを一意に指すパスを 1 つ書いてください")
        jpath.parse(key)
    pag = _pagination(d.get("pagination"), f"{where}.pagination")
    inc = _incremental(d.get("incremental"), f"{where}.incremental")
    if pag.location == "body" or (inc and inc.location == "body"):
        _expect(req.get("body") is None or isinstance(req.get("body"), dict),
                f"{where}: ページングや増分の値を body に置くときは、body を辞書（JSON）で書いてください")
    return SourceSpec(
        name=name, raw=d, connection=parse_ref(d.get("connection"), f"{where}.connection"),
        method=method, url=req["url"], headers=headers, query=query, body=req.get("body"), body_form=body_form,
        timeout_sec=_num(req, "timeout_sec", 30, f"{where}.request", 1),
        records=records, key=key, pagination=pag, incremental=inc,
        schedule=str(d.get("schedule", "*/5 * * * *")),
        min_interval_sec=_num(d, "min_interval_sec", 0, where),
        jitter_sec=_num(d, "jitter_sec", 0, where),
        busy_wait_sec=_num(d, "busy_wait_sec", 0, where),
        rate_min_interval_sec=_num(rl, "min_interval_sec", 0, f"{where}.rate_limit"),
        rate_per_minute=_int(rl, "per_minute", 0, f"{where}.rate_limit"),
        max_wait_sec=_num(rl, "max_wait_sec", 120, f"{where}.rate_limit"),
        retry_attempts=_int(rt, "attempts", 4, f"{where}.retry", 1),
        retry_backoff_sec=_num(rt, "backoff_sec", 2, f"{where}.retry"),
        retry_max_backoff_sec=_num(rt, "max_backoff_sec", 60, f"{where}.retry"),
        conditional=bool(d.get("conditional", True)),
        resume=bool(d.get("resume", True)),
        resume_max_age_sec=_num(d, "resume_max_age_sec", 3600, where),
        lease_sec=_num(d, "lease_sec", 300, where, 10),
    )


def _target(rule: str, i: int, d: Any) -> TargetSpec:
    where = f"rules.{rule}.targets[{i}]"
    if isinstance(d, str):
        d = {"workflow": d}
    _expect(isinstance(d, dict), f"{where}: 辞書か、ワークフローの名前を書いてください")
    _known(d, {"name", "workflow", "command", "timeout_sec", "params", "queue", "order", "max_attempts",
               "retry_backoff_sec", "retry_max_backoff_sec", "inline_payload"}, where)
    command = d.get("command")
    if command is None:
        _expect(isinstance(d.get("workflow"), str),
                f"{where}.workflow: 受け渡し先のワークフロー（Dagu なら DAG 名）か、その場で実行する command を書いてください")
        _expect("timeout_sec" not in d, f"{where}.timeout_sec: command を書いたときだけ使えます")
    else:
        _expect("workflow" not in d and "queue" not in d,
                f"{where}: command（その場で実行）と workflow / queue（受け渡し先へ渡す）はどちらか一方にしてください")
        _expect(isinstance(command, list) and command and all(isinstance(x, (str, int, float)) for x in command),
                f"{where}.command: argv の配列を書いてください")
        _expect(isinstance(d.get("name"), str), f"{where}.name: command の受け渡し先には name を書いてください")
    name = str(d.get("name") or d["workflow"])
    _expect(NAME_RE.match(name) is not None, f"{where}.name: 英数字・- _ . で 64 文字以内にしてください")
    params = d.get("params") or {}
    _expect(isinstance(params, dict), f"{where}.params: 辞書で書いてください")
    for k in params:
        _expect(re.match(r"^[A-Za-z][A-Za-z0-9_]*$", str(k)) is not None, f"{where}.params.{k}: 引数名は英字で始めてください")
    order = d.get("order", "completion")
    _expect(order in ORDER_MODES, f"{where}.order: {' / '.join(ORDER_MODES)} のどれかです")
    return TargetSpec(
        name=name, workflow=d.get("workflow"), params=params, queue=d.get("queue"), order=order,
        max_attempts=_int(d, "max_attempts", 10, where, 1),
        retry_backoff_sec=_num(d, "retry_backoff_sec", 30, where),
        retry_max_backoff_sec=_num(d, "retry_max_backoff_sec", 3600, where),
        inline_payload=bool(d.get("inline_payload", False)),
        command=list(command) if command is not None else None,
        timeout_sec=_num(d, "timeout_sec", 3600, where, 1),
    )


def _rule(name: str, d: Any, sources: dict[str, SourceSpec]) -> RuleSpec:
    where = f"rules.{name}"
    _expect(NAME_RE.match(name) is not None, f"{where}: 名前は英数字・- _ . で 64 文字以内にしてください")
    _expect(isinstance(d, dict), f"{where}: 辞書で書いてください")
    # YAML 1.1（PyYAML）はキーの on を真偽値 true と読むので、on に戻す
    d = {("on" if k is True else k): v for k, v in d.items()}
    _known(d, {"description", "source", "filter", "id", "watch", "ignore", "array_order", "unordered",
               "missing_is_null", "condition", "on", "initial", "on_rule_change", "targets", "include_record",
               "schedule"}, where)
    src = d.get("source")
    _expect(src in sources, f"{where}.source: sources に無い取得元です: {src}")
    filt = d.get("filter")
    if filt is not None:
        jpath.validate_condition(filt, f"{where}.filter")
    cond = d.get("condition")
    if cond is not None:
        jpath.validate_condition(cond, f"{where}.condition")
    ids = _paths(d.get("id"), f"{where}.id")
    _expect(bool(ids), f"{where}.id: レコードを識別する項目（例: id）を書いてください")
    watch = d.get("watch")
    watch_l = None if watch is None else _paths(watch, f"{where}.watch")
    on = d.get("on") or (["appeared", "changed"] if cond is None else ["entered"])
    if isinstance(on, str):
        on = [on]
    for k in on:
        _expect(k in EVENT_KINDS, f"{where}.on: {k} は使えません（{' / '.join(EVENT_KINDS)}）")
    if cond is None:
        for k in ("entered", "exited", "matching"):
            _expect(k not in on, f"{where}.on: {k} には condition が要ります")
    array_order = d.get("array_order", "respect")
    _expect(array_order in ("respect", "ignore"), f"{where}.array_order: respect か ignore です")
    initial = d.get("initial", "baseline")
    _expect(initial in ("baseline", "emit"), f"{where}.initial: baseline か emit です")
    orc = d.get("on_rule_change", "rebaseline")
    _expect(orc in ("rebaseline", "continue"), f"{where}.on_rule_change: rebaseline か continue です")
    tgts = d.get("targets") or []
    _expect(isinstance(tgts, list), f"{where}.targets: 配列で書いてください")
    targets = [_target(name, i, t) for i, t in enumerate(tgts)]
    names = [t.name for t in targets]
    _expect(len(names) == len(set(names)), f"{where}.targets: 同じワークフローを 2 回書くときは name で区別してください")
    for t in targets:
        for what, value in (("params", t.params), ("command", t.command or [])):
            for ref in template.references(value):
                _expect(ref.split(".")[0] in ("event", "env", "delivery"),
                        f"{where}.targets.{t.name}.{what}: 使えるのは event.* / delivery.* / env.* です: {ref}")
    schedule = d.get("schedule")
    _expect(schedule is None or (isinstance(schedule, str) and schedule.strip()),
            f"{where}.schedule: cron の書式で書いてください")
    _expect(schedule is None or any(t.runs_here for t in targets),
            f"{where}.schedule: command の受け渡し先があるルールだけに書けます（ほかは取得元の schedule で動く）")
    return RuleSpec(
        name=name, source=src, raw=d, filter=filt, id_paths=ids, watch=watch_l,
        ignore=_paths(d.get("ignore"), f"{where}.ignore"), array_order=array_order,
        unordered=_paths(d.get("unordered"), f"{where}.unordered"),
        missing_is_null=bool(d.get("missing_is_null", False)), condition=cond, on=list(on), initial=initial,
        on_rule_change=orc, targets=targets, include_record=bool(d.get("include_record", True)),
        schedule=schedule,
    )


def _delivery(d: Any) -> DeliverySpec | None:
    """delivery: {adapter: <名前>, ...アダプタの設定}。本体は名前と設定を持つだけで、使う時点でアダプタを探す。"""
    if d is None:
        return None
    _expect(isinstance(d, dict) and isinstance(d.get("adapter"), str),
            "delivery.adapter: 受け渡しのアダプタ（dagu / command など）を書いてください")
    return DeliverySpec(adapter=d["adapter"], options={k: v for k, v in d.items() if k != "adapter"})


def load(path: str | Path | None = None, connections: str | Path | None = None,
         state_dir: str | Path | None = None) -> Config:
    """設定を読む。どれもコマンド行の引数から渡す（環境変数は見ない）。

    path         設定ファイル（--config。省略時は DEFAULT_CONFIG）
    connections  connections.yaml のパス（--connections）
    state_dir    状態の置き場所（--state-dir。省略時は設定の state_dir）
    """
    p = Path(os.path.expanduser(str(path or DEFAULT_CONFIG))).resolve()
    if not p.is_file():
        raise ConfigError(f"設定ファイルが見つかりません: {p}")
    d = load_yaml_or_json(p)
    _expect(isinstance(d, dict), f"{p}: 辞書の形で書いてください")
    _known(d, {"version", "state_dir", "retention", "delivery", "sources", "rules"}, "（最上位）")
    _expect(d.get("version", 1) == 1, "version: 1 だけに対応しています")
    sd = d.get("sources") or {}
    rd = d.get("rules") or {}
    _expect(isinstance(sd, dict) and sd, "sources: 取得元を 1 つ以上書いてください")
    _expect(isinstance(rd, dict), "rules: 辞書で書いてください")
    sources = {str(k): _source(str(k), v) for k, v in sd.items()}
    rules = {str(k): _rule(str(k), v, sources) for k, v in rd.items()}
    delivery = _delivery(d.get("delivery"))
    if delivery is None and any(not t.runs_here for r in rules.values() for t in r.targets):
        raise ConfigError("ワークフローへ渡す受け渡し先（targets の workflow）があります。delivery.adapter を書いてください")
    if state_dir is not None:
        sdir = Path(os.path.expanduser(str(state_dir))).absolute()      # 引数の相対パスは作業フォルダから
    else:
        sdir = Path(os.path.expanduser(str(d.get("state_dir") or DEFAULT_STATE_DIR)))
        if not sdir.is_absolute():
            sdir = p.parent / sdir                                       # 設定の相対パスは設定ファイルから
    rt = d.get("retention") or {}
    _expect(isinstance(rt, dict), "retention: 辞書で書いてください")
    _known(rt, {"fetches_keep", "fetch_days", "events_days", "removed_days", "record_days", "auto_gc"}, "retention")
    record_days = rt.get("record_days")
    _expect(record_days is None or (isinstance(record_days, (int, float)) and not isinstance(record_days, bool)
                                    and record_days > 0), "retention.record_days: 0 より大きい数を書いてください（消さないなら書かない）")
    retention = Retention(fetches_keep=_int(rt, "fetches_keep", 3, "retention", 1),
                          fetch_days=_num(rt, "fetch_days", 7, "retention"),
                          events_days=_num(rt, "events_days", 30, "retention"),
                          removed_days=_num(rt, "removed_days", 30, "retention"),
                          record_days=None if record_days is None else float(record_days),
                          auto_gc=bool(rt.get("auto_gc", True)))
    return Config(path=p, state_dir=sdir.resolve(), sources=sources, rules=rules, delivery=delivery,
                  retention=retention, connections=Connections(connections))
