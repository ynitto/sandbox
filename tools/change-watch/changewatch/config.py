"""設定ファイルの読み込みと検査。

設定は YAML（PyYAML が要る）か JSON。項目は README の「設定」を参照。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import cron, folder as foldermod, jpath, template
from .connections import Connections, load_yaml_or_json, parse_ref
from .util import ConfigError, canonical, sha

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
PAGINATION_TYPES = ("none", "page", "offset", "cursor", "next_url", "link_header")
DEFAULT_CONFIG = "~/.config/change-watch/change-watch.yaml"
DEFAULT_STATE_DIR = "~/.local/state/change-watch"


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
    schedule: str | None            # change-watch が見る取得の予定（cron）。無ければ起動されるたびに取得する（Dagu の schedule に任せる）
    cron: cron.Cron | None
    min_interval_sec: float
    jitter_sec: float
    busy_wait_sec: float
    failure_backoff_sec: float
    failure_max_backoff_sec: float
    rate_min_interval_sec: float
    rate_per_minute: int
    rate_group: str | None          # 同じ group の取得元は、要求の間隔・1 分あたりの上限・失敗の待ちを共有し、同時には取得しない
    retry_attempts: int
    retry_backoff_sec: float
    retry_max_backoff_sec: float
    max_wait_sec: float
    conditional: bool
    resume: bool
    resume_max_age_sec: float
    lease_sec: float
    folder: foldermod.FolderSpec | None = None   # フォルダの取得元（request の代わり）。レコードはファイル 1 件ずつ
    item: "ItemSpec | None" = None                # レコードを 1 件取り直す要求（commit --refresh。{{ key }} が使える）

    def fingerprint(self, conn: dict | None) -> str:
        """取得条件・認証の指紋。変わったら別のデータとして扱い、過去の取得結果や位置を使わない。"""
        cfg = {k: v for k, v in self.raw.items()
               if k not in ("schedule", "min_interval_sec", "jitter_sec", "busy_wait_sec", "failure_backoff_sec",
                            "failure_max_backoff_sec", "rate_limit", "retry",
                            "resume", "resume_max_age_sec", "lease_sec", "conditional", "description", "item")}
        cred = sha(canonical(conn), 32) if conn else None
        return sha(canonical({"cfg": cfg, "conn": list(self.connection) if self.connection else None, "cred": cred}), 32)


@dataclass
class ItemSpec:
    """レコードを 1 件取り直す要求（取得元の item。commit --refresh で使う）。headers が無ければ取得元のものを使う。"""
    method: str
    url: str
    headers: dict | None
    query: dict
    records: str
    timeout_sec: float | None


@dataclass
class Retention:
    fetches_keep: int = 3
    fetch_days: float = 7
    record_days: float | None = None  # 増分取得で組み立てた全体の姿から、この日数取得されないレコードを消す（既定は消さない）
    auto_gc: bool = True


@dataclass
class Config:
    path: Path
    state_dir: Path
    sources: dict[str, SourceSpec]
    retention: Retention
    connections: Connections

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


def _source(name: str, d: Any, base: Path | None = None) -> SourceSpec:
    where = f"sources.{name}"
    _expect(NAME_RE.match(name) is not None, f"{where}: 名前は英数字・- _ . で 64 文字以内にしてください")
    _expect(isinstance(d, dict), f"{where}: 辞書で書いてください")
    _known(d, {"description", "connection", "request", "folder", "records", "key", "pagination", "incremental", "schedule",
               "min_interval_sec", "jitter_sec", "busy_wait_sec", "failure_backoff_sec", "failure_max_backoff_sec",
               "rate_limit", "retry", "conditional", "resume",
               "resume_max_age_sec", "lease_sec", "item"}, where)
    fold = None
    if "folder" in d:
        _expect("request" not in d, f"{where}: request（API）と folder（フォルダ）はどちらか一方にしてください")
        for k in ("connection", "records", "key", "pagination", "incremental", "conditional", "rate_limit", "retry", "item"):
            _expect(k not in d, f"{where}.{k}: folder の取得元には書けません")
        fold = foldermod.parse(d["folder"], f"{where}.folder", base or Path.cwd())
        d = dict(d, request={"method": "LIST", "url": str(fold.path)}, key=foldermod.KEY)
    req = d.get("request")
    _expect(isinstance(req, dict) and isinstance(req.get("url"), str),
            f"{where}.request.url（API）か folder.path（フォルダ）を書いてください")
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
    _known(rl, {"min_interval_sec", "per_minute", "max_wait_sec", "group"}, f"{where}.rate_limit")
    group = rl.get("group")
    _expect(group is None or (isinstance(group, str) and NAME_RE.match(group) is not None),
            f"{where}.rate_limit.group: 英数字・- _ . で 64 文字以内の名前を書いてください")
    schedule = d.get("schedule")
    _expect(schedule is None or isinstance(schedule, str), f"{where}.schedule: cron の書式（例 \"0 * * * *\"）で書いてください")
    try:
        cron_spec = cron.parse(schedule) if schedule is not None else None
    except ConfigError as e:
        raise ConfigError(f"{where}.schedule: {e}") from e
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
        schedule=schedule, cron=cron_spec, rate_group=group,
        min_interval_sec=_num(d, "min_interval_sec", 0, where),
        jitter_sec=_num(d, "jitter_sec", 0, where),
        busy_wait_sec=_num(d, "busy_wait_sec", 0, where),
        failure_backoff_sec=_num(d, "failure_backoff_sec", 60, where),
        failure_max_backoff_sec=_num(d, "failure_max_backoff_sec", 3600, where),
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
        folder=fold,
        item=_item(d.get("item"), f"{where}.item"),
    )


def _item(d: Any, where: str) -> ItemSpec | None:
    names = ("key", "conn", "env")
    if d is None:
        return None
    _expect(isinstance(d, dict) and isinstance(d.get("url"), str),
            f"{where}.url: レコードを 1 件取り直す URL を書いてください（例 \"{{{{ conn.url }}}}/items/{{{{ key }}}}\"）")
    _known(d, {"method", "url", "headers", "query", "records", "timeout_sec"}, where)
    headers = d.get("headers")
    query = d.get("query") or {}
    _expect(headers is None or isinstance(headers, dict), f"{where}.headers: 辞書で書いてください")
    _expect(isinstance(query, dict), f"{where}.query: 辞書で書いてください")
    records = d.get("records", "$")
    jpath.parse(records)
    for ref in template.references([d["url"], headers or {}, query]):
        _expect(ref.split(".")[0] in names,
                f"{where}: 使えるのは {' / '.join(n + '.*' if n != 'key' else n for n in names)} です: {ref}")
    t = d.get("timeout_sec")
    return ItemSpec(method=str(d.get("method", "GET")).upper(), url=d["url"], headers=headers, query=query,
                     records=records, timeout_sec=None if t is None else _num(d, "timeout_sec", 30, where, 1))


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
    gone = [k for k in ("rules", "delivery") if k in d]
    _expect(not gone, f"{'・'.join(gone)}: ルールと受け渡しは 0.8.0 でなくなりました。条件と処理は Dagu のワークフローに書き、"
                      "change-watch からは get / commit / call で使ってください（README の「0.7 からの移行」）")
    _known(d, {"version", "state_dir", "retention", "sources"}, "（最上位）")
    _expect(d.get("version", 1) == 1, "version: 1 だけに対応しています")
    sd = d.get("sources") or {}
    _expect(isinstance(sd, dict) and sd, "sources: 取得元を 1 つ以上書いてください")
    sources = {str(k): _source(str(k), v, p.parent) for k, v in sd.items()}
    groups: dict[str, SourceSpec] = {}
    for name, src in sources.items():
        if src.rate_group is None:
            continue
        first = groups.setdefault(src.rate_group, src)
        _expect((first.rate_min_interval_sec, first.rate_per_minute) == (src.rate_min_interval_sec, src.rate_per_minute),
                f"sources.{name}.rate_limit: group {src.rate_group} の min_interval_sec / per_minute は、同じ group の "
                f"{first.name} と同じ値にしてください（group で 1 つの上限を共有します）")
    if state_dir is not None:
        sdir = Path(os.path.expanduser(str(state_dir))).absolute()      # 引数の相対パスは作業フォルダから
    else:
        sdir = Path(os.path.expanduser(str(d.get("state_dir") or DEFAULT_STATE_DIR)))
        if not sdir.is_absolute():
            sdir = p.parent / sdir                                       # 設定の相対パスは設定ファイルから
    rt = d.get("retention") or {}
    _expect(isinstance(rt, dict), "retention: 辞書で書いてください")
    _known(rt, {"fetches_keep", "fetch_days", "record_days", "auto_gc"}, "retention")
    record_days = rt.get("record_days")
    _expect(record_days is None or (isinstance(record_days, (int, float)) and not isinstance(record_days, bool)
                                    and record_days > 0), "retention.record_days: 0 より大きい数を書いてください（消さないなら書かない）")
    retention = Retention(fetches_keep=_int(rt, "fetches_keep", 3, "retention", 1),
                          fetch_days=_num(rt, "fetch_days", 7, "retention"),
                          record_days=None if record_days is None else float(record_days),
                          auto_gc=bool(rt.get("auto_gc", True)))
    return Config(path=p, state_dir=sdir.resolve(), sources=sources,
                  retention=retention, connections=Connections(connections))
