"""コマンド行。`python change_watch.py --help`。"""
from __future__ import annotations

import argparse
import json

from . import VERSION, access, config as configmod, hooks, plugins
from .fetch import Deferred, Fetcher, load_records
from .maint import gc, print_status, status
from .store import Store
from .util import (EXIT_ERROR, EXIT_OK, EXIT_TRANSIENT, ConfigError, PermanentError,
                   REDACT, TransientError, log, setup_stdio, warn)


def _sources(cfg, names: list[str] | None) -> list[str]:
    if not names:
        return list(cfg.sources)
    for n in names:
        if n not in cfg.sources:
            raise ConfigError(f"設定に無い取得元です: {n}")
    return names


def cmd_check(cfg, store, a) -> int:
    log(f"設定: {cfg.path}")
    log(f"状態の保存先: {cfg.state_dir}")
    log(f"接続設定: {cfg.connections.path or '（--connections の指定なし）'}")
    ok = True
    for name, src in cfg.sources.items():
        try:
            cfg.resolve_connection(src.connection)
            conn = "なし" if src.connection is None else f"{src.connection[0]}/{src.connection[1]}"
            what = (f"フォルダ {src.folder.path}（{'・'.join(src.folder.include)}）" if src.folder is not None else
                    f"{src.method} {REDACT(src.url)}（接続 {conn}・ページング {src.pagination.type}・"
                    f"増分 {src.incremental.type if src.incremental else 'なし'}・")
            if src.folder is not None:
                what += "（"
                if not src.folder.path.is_dir():
                    log(f"    注意: フォルダがまだありません: {src.folder.path}（取得は一時的な失敗になります）")
            log(f"  取得元 {name}: {what}"
                + (f"予定 {src.schedule}（起動されても予定の時刻まで取得しない）" if src.schedule
                   else "予定なし（起動されるたびに取得）")
                + (f"・間隔 {src.min_interval_sec:.0f} 秒以上" if src.min_interval_sec else "")
                + (f"・group {src.rate_group}" if src.rate_group else "")
                + (f"・ずらし 0〜{src.jitter_sec:.0f} 秒" if src.jitter_sec else "")
                + (f"・取得中なら {src.busy_wait_sec:.0f} 秒まで待つ" if src.busy_wait_sec else "") + "）")
        except ConfigError as e:
            ok = False
            log(f"  取得元 {name}: {e}")
    log("問題ありません" if ok else "問題があります")
    return EXIT_OK if ok else EXIT_ERROR


def cmd_fetch(cfg, store, a) -> int:
    code = EXIT_OK
    for name in _sources(cfg, a.source):
        o = Fetcher(cfg, store).run(cfg.sources[name], force=a.force, jitter=not getattr(a, "no_jitter", False))
        log(f"[{name}] {o.status}: {o.message}")
        if o.status == "failed":
            code = max(code, EXIT_TRANSIENT if o.transient else EXIT_ERROR)
    return code


def cmd_status(cfg, store, a) -> int:
    st = status(cfg, store)
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        print_status(st)
    return EXIT_OK


def cmd_records(cfg, store, a) -> int:
    """保存済みの取得結果を出す（get の --watch やワークフローの条件を確かめる用）。"""
    src = _sources(cfg, [a.source])[0]
    if a.fetch:
        fid = a.fetch
    else:
        row = store.one("SELECT id FROM fetches WHERE source=? AND status='complete' ORDER BY id DESC LIMIT 1", (src,))
        if row is None:
            log("保存済みの取得結果がありません")
            return EXIT_ERROR
        fid = row["id"]
    recs = load_records(store, fid)
    print(json.dumps(recs[: a.limit], ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_gc(cfg, store, a) -> int:
    c = gc(cfg, store, a.dry_run, a.vacuum)
    log(("消す予定" if a.dry_run else "消しました") +
        f": 取得結果 {c['fetches']} 回分・途中で止まった取得のページ {c['pages_of_abandoned']} 回分・"
        f"条件付き要求の控え {c['http_cache']} 件・長く取得されていないレコード {c['stale_records']} 件"
        + ("" if a.dry_run else f"。ファイルから {c['freed_bytes'] / 1048576:.1f} MB 返しました"))
    return EXIT_OK


def _fetch_quietly(cfg, store, name: str, a) -> int:
    """get の前の取得。取得元の予定・間隔・取得中の待ち・失敗の待ちはそのまま効く（問い合わせるかは change-watch が決める）。
    失敗しても、前回までの取得結果で答える（ワークフローは止めない）。"""
    if getattr(a, "no_fetch", False):
        return EXIT_OK
    o = Fetcher(cfg, store).run(cfg.sources[name], jitter=not getattr(a, "no_jitter", False))
    log(f"[{name}] {o.status}: {o.message}")
    if o.status == "failed":
        warn(f"[{name}] 取得に失敗したので、前回までの取得結果で答えます")
    return EXIT_OK


def cmd_get(cfg, store, a) -> int:
    name = _sources(cfg, [a.source])[0]
    src = cfg.sources[name]
    if a.consumer is None and (a.watch or a.one or a.initial):
        raise ConfigError("--watch・--one・--initial は --consumer と一緒に使ってください")
    _fetch_quietly(cfg, store, name, a)
    if a.consumer is None:
        out: object = access.records(store, src)
    else:
        found = access.changes(store, src, a.consumer, a.watch or [], a.initial or "baseline")
        log(f"[{name}] {a.consumer} が前回 commit してから変わったレコード: {len(found)} 件")
        if a.one:
            if not found:
                return EXIT_OK          # 何も出さない（ワークフローは空の出力で後ろのステップを飛ばす）
            out = found[0]
        else:
            out = found
    print(json.dumps(out, ensure_ascii=False))
    return EXIT_OK


def cmd_commit(cfg, store, a) -> int:
    name = _sources(cfg, [a.source])[0]
    keys = [k for k in a.keys if k != ""]
    if not keys:
        log("処理し終えたレコードがありません")
        return EXIT_OK
    try:
        done = access.commit(Fetcher(cfg, store), cfg.sources[name], a.consumer, keys, a.refresh)
    except Deferred as e:
        warn(str(e))
        return EXIT_TRANSIENT
    log(f"[{name}] {a.consumer} が処理し終えたと記録しました: {', '.join(done)}")
    return EXIT_OK


def cmd_call(cfg, store, a) -> int:
    name = _sources(cfg, [a.source])[0]
    body = None
    if a.json is not None and a.json_file is not None:
        raise ConfigError("--json と --json-file はどちらか一方にしてください")
    try:
        if a.json is not None:
            body = json.loads(a.json)
        elif a.json_file is not None:
            with open(a.json_file, encoding="utf-8-sig") as f:
                body = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError(f"送る JSON を読めません: {e}") from e
    query = {}
    for q in a.query or []:
        k, sep, v = q.partition("=")
        if not sep:
            raise ConfigError(f"--query は 名前=値 で書いてください: {q}")
        query[k] = v
    try:
        status, resp = Fetcher(cfg, store).call(cfg.sources[name], a.method, a.url, body, query)
    except Deferred as e:
        warn(str(e))
        return EXIT_TRANSIENT
    except TransientError as e:
        warn(str(e))
        return EXIT_TRANSIENT
    except PermanentError as e:
        warn(str(e))
        return EXIT_ERROR
    log(f"[{name}] {a.method.upper()} → HTTP {status}")
    if resp is not None:
        print(json.dumps(resp, ensure_ascii=False))
    return EXIT_OK


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--plugin", action="append", default=[],
                   help="読み込むプラグイン（.py のパス。繰り返し可）")


def build_parser(plugin_specs: list[str] | None = None) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="change-watch", description="ワークフロー（Dagu）から API を使う口: 取得をまとめ、前回から変わったレコードを返し、書き込みも通す")
    _common(p)
    p.add_argument("--config", help=f"設定ファイル（既定: {configmod.DEFAULT_CONFIG}）")
    p.add_argument("--connections", help="接続設定（connections.yaml）のパス。認証情報はここからだけ読む")
    p.add_argument("--state-dir", help="状態の置き場所（設定の state_dir より優先）")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn, help: str):
        sp = sub.add_parser(name, help=help, description=help)
        sp.set_defaults(fn=fn)
        return sp

    add("check", cmd_check, "設定と接続設定を確かめる（API には問い合わせない）")
    sp = add("fetch", cmd_fetch, "取得元から取得する（get の前に取っておくとき。get も必要なら取得する）")
    sp.add_argument("--source", action="append")
    sp.add_argument("--force", action="store_true", help="min_interval_sec・jitter_sec を無視する")
    sp.add_argument("--no-jitter", action="store_true", help="jitter_sec でずらさない（手で叩くとき）")
    sp = add("get", cmd_get, "取得元のレコードを JSON で出す（--consumer なら、前回 commit してから変わったものだけ）。"
                             "取得するかは取得元の予定・間隔で決め、失敗しても前回までの結果で答える")
    sp.add_argument("--source", required=True)
    sp.add_argument("--consumer", help="変化を受け取るワークフローの名前（commit と同じ名前にする）")
    sp.add_argument("--watch", action="append", help="変化として見るパス（繰り返し可。省略するとレコード全体）")
    sp.add_argument("--initial", choices=access.INITIAL,
                    help="初めて呼ばれたとき: baseline はいまの姿を覚えるだけ（既定）、emit は全部を appeared で返す")
    sp.add_argument("--one", action="store_true", help="1 件だけを JSON のオブジェクトで出す（無ければ何も出さない）")
    sp.add_argument("--no-fetch", action="store_true", help="取得せず、保存済みの取得結果で答える")
    sp.add_argument("--no-jitter", action="store_true", help="jitter_sec でずらさない（手で叩くとき）")
    sp = add("commit", cmd_commit, "get --consumer で受け取ったレコードを、処理し終えたと記録する（次の get で返さない）")
    sp.add_argument("--source", required=True)
    sp.add_argument("--consumer", required=True)
    sp.add_argument("--refresh", action="store_true",
                    help="取得元の item でレコードを 1 件取り直し、その姿で記録する（処理の中で自分が書き込んだ分を変化にしない）")
    sp.add_argument("keys", nargs="*", help="レコードの key（get の出力の key）")
    sp = add("call", cmd_call, "取得元の接続（認証）と要求の間隔の制限で API を 1 回呼び、応答の JSON を出す（書き込みにも使える）")
    sp.add_argument("method", help="GET / POST / PUT / PATCH / DELETE")
    sp.add_argument("url", help="URL（{{ conn.* }}・{{ env.* }} が使える。/ で始まれば接続の url に足す）")
    sp.add_argument("--source", required=True, help="接続・要求の間隔の制限・失敗の待ちを分け合う取得元")
    sp.add_argument("--json", help="送る本文（JSON の文字列）")
    sp.add_argument("--json-file", help="送る本文（JSON のファイル）")
    sp.add_argument("--query", action="append", help="名前=値（繰り返し可）")
    sp = add("status", cmd_status, "取得元と consumer の状況")
    sp.add_argument("--json", action="store_true")
    sp = add("records", cmd_records, "保存済みの取得結果のレコードを出す")
    sp.add_argument("--source", required=True)
    sp.add_argument("--fetch", type=int)
    sp.add_argument("--limit", type=int, default=20)
    sp = add("gc", cmd_gc, "保持期間を過ぎた取得結果を消してファイルを縮める")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--vacuum", action="store_true", help="VACUUM で DB を作り直す（時間がかかる。古い DB を自動で縮む形にする）")
    plugins.load(plugin_specs or [], add)
    return p


def main(argv: list[str] | None = None) -> int:
    setup_stdio()
    pre = argparse.ArgumentParser(add_help=False)
    _common(pre)
    known, _ = pre.parse_known_args(argv)
    hooks.reset_plugins()
    try:
        parser = build_parser(known.plugin)
    except ConfigError as e:
        warn(str(e))
        return EXIT_ERROR
    a = parser.parse_args(argv)
    try:
        cfg = configmod.load(a.config, a.connections, a.state_dir)
        store = Store(cfg.state_dir)
    except ConfigError as e:
        warn(str(e))
        return EXIT_ERROR
    try:
        code = a.fn(cfg, store, a)
        if a.cmd in ("fetch", "get") and cfg.retention.auto_gc and code == EXIT_OK:
            gc(cfg, store)
        return code
    except ConfigError as e:
        warn(str(e))
        return EXIT_ERROR
    finally:
        store.close()
