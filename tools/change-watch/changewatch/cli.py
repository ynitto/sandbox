"""コマンド行。`python change_watch.py --help`。"""
from __future__ import annotations

import argparse
import json

from . import VERSION, access, adapters, config as configmod, hooks, plugins
from .deliver import Dispatcher
from .detect import Evaluator, log_outcomes
from .fetch import Deferred, Fetcher, load_records
from .maint import forget_rule, gc, print_status, status
from .store import ALL_STATUSES, Store
from .util import (EXIT_ERROR, EXIT_OK, EXIT_PARTIAL, EXIT_TRANSIENT, EXIT_USAGE, ConfigError, PermanentError,
                   REDACT, TransientError, log, setup_stdio, unpack, warn)



def _sources(cfg, names: list[str] | None) -> list[str]:
    if not names:
        return list(cfg.sources)
    for n in names:
        if n not in cfg.sources:
            raise ConfigError(f"設定に無い取得元です: {n}")
    return names


def _rules(cfg, names: list[str] | None) -> list[str] | None:
    if not names:
        return None
    for n in names:
        if n not in cfg.rules:
            raise ConfigError(f"設定に無いルールです: {n}")
    return names


def cmd_check(cfg, store, a) -> int:
    log(f"設定: {cfg.path}")
    log(f"状態の保存先: {cfg.state_dir}")
    log(f"接続設定: {cfg.connections.path or '（--connections の指定なし）'}")
    here = any(t.runs_here for r in cfg.rules.values() for t in r.targets)
    log(f"受け渡し: {cfg.delivery.adapter if cfg.delivery else '（アダプタなし）'}"
        + ("（command の受け渡し先は work がその場で実行）" if here else ""))
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
            log(f"  取得元 {name}: {what}ルール {len(cfg.rules_for(name))} 本・"
                + (f"予定 {src.schedule}（起動されても予定の時刻まで取得しない）" if src.schedule
                   else "予定なし（起動されるたびに取得）")
                + (f"・間隔 {src.min_interval_sec:.0f} 秒以上" if src.min_interval_sec else "")
                + (f"・group {src.rate_group}" if src.rate_group else "")
                + (f"・ずらし 0〜{src.jitter_sec:.0f} 秒" if src.jitter_sec else "")
                + (f"・取得中なら {src.busy_wait_sec:.0f} 秒まで待つ" if src.busy_wait_sec else "") + "）")
            flows = cfg.fetching_workflows(name)
            if len(flows) > 1:
                log(f"    {len(flows)} 本のワークフロー（{'・'.join(flows)}）が取得し、問い合わせは"
                    + ("予定の時刻ごとに 1 回" if src.schedule else f" {src.min_interval_sec:.0f} 秒に 1 回まで")
                    + "にまとめます")
        except ConfigError as e:
            ok = False
            log(f"  取得元 {name}: {e}")
    for name, r in cfg.rules.items():
        tg = ", ".join(f"{t.name}→{'その場で実行' if t.runs_here else t.workflow}（{t.order}）"
                       for t in r.targets) or "なし（検知だけ記録）"
        log(f"  ルール {name}: {r.source} / ID {r.id_paths} / 監視 {r.watch or '全体'} / 発火 {r.on} / 受け渡し {tg}")
    if cfg.delivery is not None:
        try:
            cfg.delivery.create(cfg.connections)
        except ConfigError as e:
            ok = False
            log(f"  delivery: {e}")
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


def cmd_evaluate(cfg, store, a) -> int:
    only = _rules(cfg, a.rule)
    srcs = _sources(cfg, a.source)
    if only:
        srcs = sorted({cfg.rules[r].source for r in only} & set(srcs))
    code = EXIT_OK
    ev = Evaluator(cfg, store)
    for s in srcs:
        outs = ev.evaluate_source(s, only)
        log_outcomes(outs)
        if any(o.status == "error" for o in outs):
            code = EXIT_PARTIAL
    return code


def _rules_of_sources(cfg, a) -> list[str] | None:
    only = _rules(cfg, a.rule)
    if a.source:
        srcs = set(_sources(cfg, a.source))
        names = [r for r in cfg.rules if cfg.rules[r].source in srcs]
        only = names if only is None else [r for r in only if r in names]
    return only


def cmd_dispatch(cfg, store, a) -> int:
    o = Dispatcher(cfg, store).dispatch(_rules_of_sources(cfg, a), a.limit)
    log(f"受け渡し: 渡した {o.sent}・既に渡し済み {o.duplicate}・失敗（あとで再送） {o.failed}・"
        f"再送の上限に達した {o.dead}・前のイベントの完了待ち {o.waiting}"
        + (f"・設定に無いルール/受け渡し先 {o.unknown}" if o.unknown else ""))
    return EXIT_PARTIAL if (o.failed or o.dead) else EXIT_OK


def cmd_work(cfg, store, a) -> int:
    rules = _rules_of_sources(cfg, a)
    targets = a.target or None
    o = Dispatcher(cfg, store).work(rules, targets, a.limit)
    log(f"実行: 完了 {o.done}・処理することが無く飛ばした {o.skipped}・失敗（あとでやり直す） {o.failed}・打ち切り {o.dead}・"
        f"待ち {o.waiting}"
        + ("・中止されました" if o.stopped else ""))
    if o.stopped:
        return EXIT_ERROR
    return EXIT_PARTIAL if (o.failed or o.dead) else EXIT_OK


def cmd_due(cfg, store, a) -> int:
    """external の受け渡し先の、実行を待っている受け渡しの ID を JSON の配列で出す（ほかの基盤で 1 件ずつ回すとき。1 本のワークフローなら claim）。"""
    ids = Dispatcher(cfg, store).due(_rules_of_sources(cfg, a), a.target or None, external=True, limit=a.limit)
    print(json.dumps(ids))
    return EXIT_OK


def cmd_begin(cfg, store, a) -> int:
    """受け渡しを 1 件取り、実行してよいかを標準出力に 1 語で出す（run / skip / busy / wait / failed / gone）。"""
    decision, msg = Dispatcher(cfg, store).begin(a.delivery)
    log(msg)
    print(decision)
    return EXIT_OK


def cmd_claim(cfg, store, a) -> int:
    """実行を待っている受け渡しを古い順に begin し、最初に run になった 1 件の ID を出す（無ければ空行）。

    1 本のワークフローで 1 回の起動につき 1 件を処理するときに使う（claim → render → 処理 → finish）。飛ばすもの
    （処理し終えた状態と同じ・filter から外れた など）は begin が完了として記録するので、そのまま次を見る。
    lock が空いていない・取得元が失敗の待ちの中なら、そこでやめる（次の起動で試す）。"""
    disp = Dispatcher(cfg, store)
    for d in disp.due(_rules_of_sources(cfg, a), a.target or None, external=True):
        decision, msg = disp.begin(d)
        log(msg)
        if decision == "run":
            print(d)
            return EXIT_OK
        if decision in ("busy", "wait"):
            break
    print("")
    return EXIT_OK


def cmd_render(cfg, store, a) -> int:
    """begin した受け渡しのイベントでテンプレートを展開して出す（プロンプトなど）。"""
    from pathlib import Path
    try:
        payload, path = Dispatcher(cfg, store).run_payload(a.delivery)
    except KeyError:
        log(f"受け渡し {a.delivery} が見つかりません")
        return EXIT_ERROR
    tpl = a.template
    if tpl is None:
        d = store.one("SELECT rule, target FROM deliveries WHERE id=?", (a.delivery,))
        t = Dispatcher(cfg, store).target(d["rule"], d["target"]) if d else None
        tpl = t.input_template if t else None
        if tpl is None:
            raise ConfigError("--template か、受け渡し先の input_template を書いてください")
        p = Path(tpl).expanduser()
        tpl = str(p if p.is_absolute() else cfg.path.parent / p)     # 設定の相対パスは設定ファイルから
    try:
        text = Path(tpl).expanduser().read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"テンプレートが読めません: {tpl}（{e}）")
    from . import template
    ctx = {"event": payload, "delivery": payload.get("delivery") or {"id": a.delivery}, "env": dict(__import__("os").environ),
           "event_file": str(path)}
    out = template.render(text, ctx)
    out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
    if a.out:
        Path(a.out).expanduser().write_text(out, encoding="utf-8")
    else:
        print(out)
    return EXIT_OK


def cmd_finish(cfg, store, a) -> int:
    """begin した受け渡しの終わり（--failed なら失敗）を記録する。外のワークフローの最後か handler_on から呼ぶ。"""
    ok, message = not a.failed, a.message
    if a.exit_code is not None:
        # 処理のステップの終了コード（Dagu の ${handle.exit_code}）で成否を決める。数でなければ失敗とみなす
        try:
            code = int(str(a.exit_code).strip())
        except ValueError:
            code = None
        if code != 0:
            ok = False
            message = message or (f"処理が終了コード {code} で終わった" if code is not None
                                  else f"処理の終了コードが分からない（{a.exit_code}）")
    st = Dispatcher(cfg, store).finish(a.delivery, ok, message, a.handled) if a.delivery.strip() else "gone"
    log(f"{a.delivery}: {st}")
    return EXIT_OK


def cmd_run(cfg, store, a) -> int:
    rules = _rules(cfg, a.rule)
    if rules:
        a.source = sorted({cfg.rules[r].source for r in rules} & set(_sources(cfg, a.source)))
    code = cmd_fetch(cfg, store, a)
    code2 = cmd_evaluate(cfg, store, a)
    code3 = cmd_dispatch(cfg, store, a)
    a.target = None
    code4 = cmd_work(cfg, store, a)
    return max(code, code2, code3, code4)


def cmd_ack(cfg, store, a) -> int:
    try:
        st = Dispatcher(cfg, store).ack(a.delivery, not a.failed, a.message)
    except KeyError:
        log(f"受け渡し {a.delivery} が見つかりません")
        return EXIT_ERROR
    log(f"{a.delivery}: {st}")
    return EXIT_OK


def cmd_reconcile(cfg, store, a) -> int:
    c = Dispatcher(cfg, store).reconcile()
    log(f"後続処理の確認: 完了 {c['done']}・失敗 {c['failed']}・変化なし・確認できない {c['unchanged']}")
    return EXIT_OK


def _select_deliveries(store, a) -> list[str]:
    if a.ids:
        return a.ids
    sql = "SELECT id FROM deliveries WHERE 1=1"
    args: list = []
    if a.rule:
        sql += " AND rule=?"
        args.append(a.rule)
    if a.target:
        sql += " AND target=?"
        args.append(a.target)
    statuses = a.status.split(",") if a.status else ["send_failed", "dead", "failed"]
    sql += f" AND status IN ({','.join('?' * len(statuses))}) ORDER BY seq"
    args += statuses
    return [r["id"] for r in store.q(sql, tuple(args))]


def cmd_retry(cfg, store, a) -> int:
    ids = _select_deliveries(store, a)
    if not ids:
        log("対象の受け渡しはありません")
        return EXIT_OK
    for m in Dispatcher(cfg, store).retry(ids):
        log(m)
    return EXIT_OK


def cmd_skip(cfg, store, a) -> int:
    if not a.ids:
        log("飛ばす受け渡しの ID を指定してください")
        return EXIT_USAGE
    for m in Dispatcher(cfg, store).skip(a.ids):
        log(m)
    return EXIT_OK


def cmd_status(cfg, store, a) -> int:
    st = status(cfg, store)
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        print_status(st)
    return EXIT_OK


def cmd_events(cfg, store, a) -> int:
    sql = ("SELECT d.*, e.kinds, e.created_at AS ev_created FROM deliveries d JOIN events e ON e.id=d.event_id "
           "WHERE 1=1")
    args: list = []
    if a.rule:
        sql += " AND d.rule=?"
        args.append(a.rule)
    if a.status:
        st = a.status.split(",")
        sql += f" AND d.status IN ({','.join('?' * len(st))})"
        args += st
    if a.record:
        sql += " AND d.rkey=?"
        args.append(a.record)
    sql += " ORDER BY d.seq DESC LIMIT ?"
    args.append(a.limit)
    rows = store.q(sql, tuple(args))
    # 受け渡し先の無いルールのイベントも出す
    ev_only = []
    if not a.status:
        q = "SELECT * FROM events e WHERE NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.event_id=e.id)"
        eargs: list = []
        if a.rule:
            q += " AND e.rule=?"
            eargs.append(a.rule)
        if a.record:
            q += " AND e.rkey=?"
            eargs.append(a.record)
        ev_only = store.q(q + " ORDER BY seq DESC LIMIT ?", tuple(eargs + [a.limit]))
    if a.json:
        out = [{"delivery": r["id"], "event": r["event_id"], "rule": r["rule"], "target": r["target"],
                "workflow": r["workflow"], "record": r["rkey"], "kinds": r["kinds"].split(","), "status": r["status"],
                "attempts": r["attempts"], "error": r["last_error"]} for r in rows]
        out += [{"delivery": None, "event": r["id"], "rule": r["rule"], "record": r["rkey"],
                 "kinds": r["kinds"].split(","), "status": "recorded"} for r in ev_only]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return EXIT_OK
    from .util import iso
    for r in rows:
        print(f"{iso(r['ev_created'])}  {r['id']}  {r['rule']} → {r['target']}  レコード {r['rkey']}  "
              f"{r['kinds']}  {r['status']}" + (f"（{r['last_error']}）" if r["last_error"] else ""))
    for r in ev_only:
        print(f"{iso(r['created_at'])}  {r['id']}  {r['rule']}（受け渡し先なし）  レコード {r['rkey']}  {r['kinds']}")
    if not rows and not ev_only:
        print("該当するイベントはありません")
    return EXIT_OK


def cmd_show(cfg, store, a) -> int:
    row = store.one("SELECT event_id FROM deliveries WHERE id=?", (a.id,))
    eid = row["event_id"] if row else a.id
    ev = store.one("SELECT payload FROM events WHERE id=?", (eid,))
    if ev is None:
        log(f"{a.id} が見つかりません")
        return EXIT_ERROR
    payload = unpack(ev["payload"])
    payload["deliveries"] = [{"id": d["id"], "target": d["target"], "workflow": d["workflow"], "status": d["status"],
                              "attempts": d["attempts"], "error": d["last_error"]}
                             for d in store.q("SELECT * FROM deliveries WHERE event_id=? ORDER BY target", (eid,))]
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_rebaseline(cfg, store, a) -> int:
    names = _rules(cfg, a.rule) or []
    if a.source:
        names += [r for r in cfg.rules if cfg.rules[r].source in _sources(cfg, a.source)]
    if not names:
        log("--rule か --source を指定してください")
        return EXIT_USAGE
    rules = [cfg.rules[n] for n in dict.fromkeys(names)]
    ev = Evaluator(cfg, store)
    ev.request_rebaseline(rules)
    if a.refetch:
        with store.tx() as db:
            for s in {r.source for r in rules}:
                db.execute("UPDATE sources SET position=NULL WHERE name=?", (s,))
        log("次の取得は増分ではなく全件で取ります")
    if a.now:
        for s in sorted({r.source for r in rules}):
            log_outcomes(ev.evaluate_source(s, [r.name for r in rules if r.source == s]))
    else:
        log("次の判定で、最新の取得結果を基準値にします（その間の変化では発火しません）: " + ", ".join(r.name for r in rules))
    return EXIT_OK


def cmd_replay(cfg, store, a) -> int:
    rule = cfg.rules.get(a.rule)
    if rule is None:
        raise ConfigError(f"設定に無いルールです: {a.rule}")
    events, msg = Evaluator(cfg, store).replay(rule, a.from_fetch, a.dry_run, a.emit_first)
    for e in events[: a.show]:
        log(f"  #{e['fetch_id']} レコード {e['record_key']} {e['kinds']} 変更 "
            + ", ".join(c["path"] for c in e["changes"][:5]))
    if len(events) > a.show:
        log(f"  …ほか {len(events) - a.show} 件")
    log(msg)
    return EXIT_OK


def cmd_records(cfg, store, a) -> int:
    """保存済みの取得結果を出す（ルールの書き方を確かめる用）。"""
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
        f"イベント {c['events']} 件・イベントのファイル {c['event_files']} 個・条件付き要求の控え {c['http_cache']} 件・"
        f"消えたレコードの観測値 {c['removed_records']} 件・長く取得されていないレコード {c['stale_records']} 件"
        + ("" if a.dry_run else f"。ファイルから {c['freed_bytes'] / 1048576:.1f} MB 返しました"))
    return EXIT_OK


def cmd_forget_handled(cfg, store, a) -> int:
    rule = cfg.rules.get(a.rule)
    if rule is None:
        raise ConfigError(f"設定に無いルールです: {a.rule}")
    names = [t.name for t in rule.targets if t.refresh is not None and (not a.target or t.name in a.target)]
    if not names:
        log(f"ルール {a.rule} に refresh のある受け渡し先がありません")
        return EXIT_USAGE
    n = 0
    with store.tx() as db:
        for rec in a.records:
            for t in names:
                n += db.execute("DELETE FROM handled_records WHERE rule=? AND target=? AND rkey=?",
                                (a.rule, t, rec)).rowcount
    log(f"処理し終えた状態を {n} 件忘れました（次に変化を受け取ったとき、必ず実行します）")
    return EXIT_OK


def cmd_forget_rule(cfg, store, a) -> int:
    if a.rule in cfg.rules:
        log(f"{a.rule} は設定にあります。設定から消してから実行してください（基準値の取り直しなら rebaseline）")
        return EXIT_USAGE
    log(forget_rule(store, a.rule))
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
                   help="読み込むプラグイン。同梱のものは名前（例: dagu）、ほかは .py のパス。繰り返し可")


def build_parser(plugin_specs: list[str] | None = None) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="change-watch", description="API をポーリングして変化を検知し、ワークフローへ渡す")
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
    sp = add("fetch", cmd_fetch, "取得元から取得する（全ページを取り終えたら判定に回せる）")
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
    sp = add("evaluate", cmd_evaluate, "未判定の取得結果をルールで判定する（API には問い合わせない）")
    sp.add_argument("--source", action="append")
    sp.add_argument("--rule", action="append")
    sp = add("dispatch", cmd_dispatch, "未受け渡し・再送待ちのイベントを Dagu へ渡す")
    sp.add_argument("--source", action="append")
    sp.add_argument("--rule", action="append")
    sp.add_argument("--limit", type=int)
    sp = add("work", cmd_work, "command の受け渡し先を、その場で 1 件ずつ実行する")
    sp.add_argument("--source", action="append")
    sp.add_argument("--rule", action="append")
    sp.add_argument("--target", action="append")
    sp.add_argument("--limit", type=int)
    sp = add("due", cmd_due, "external の受け渡し先の、実行を待っている受け渡しの ID を JSON の配列で出す")
    sp.add_argument("--source", action="append")
    sp.add_argument("--rule", action="append")
    sp.add_argument("--target", action="append")
    sp.add_argument("--limit", type=int)
    sp = add("begin", cmd_begin, "受け渡しを 1 件取り、実行してよいか（run / skip / busy / wait / failed / gone）を出す")
    sp.add_argument("delivery")
    sp = add("claim", cmd_claim, "external の受け渡し先の、実行できる受け渡しを 1 件取って ID を出す（無ければ空行）")
    sp.add_argument("--source", action="append")
    sp.add_argument("--rule", action="append")
    sp.add_argument("--target", action="append")
    sp = add("render", cmd_render, "begin した受け渡しのイベントでテンプレートを展開する（{{ event.* }}・プラグインのフィルタ）")
    sp.add_argument("delivery")
    sp.add_argument("--template", help="テンプレートのファイル（省略すると受け渡し先の input_template）")
    sp.add_argument("--out", help="書き出すファイル（省略すると標準出力）")
    sp = add("finish", cmd_finish, "begin した受け渡しの終わりを記録する（--failed なら失敗。あとでやり直す）")
    sp.add_argument("delivery")
    sp.add_argument("--failed", action="store_true")
    sp.add_argument("--message")
    sp.add_argument("--exit-code", help="処理のステップの終了コード。0 以外（数でないものも）なら失敗として記録する")
    sp.add_argument("--handled", choices=["before", "after"],
                    help="処理し終えた状態にするのを、実行前（before）か実行のあと（after。既定）か")
    sp = add("run", cmd_run, "fetch → evaluate → dispatch → work をまとめて行う（--rule でそのルールだけ）")
    sp.add_argument("--source", action="append")
    sp.add_argument("--rule", action="append")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-jitter", action="store_true")
    sp.add_argument("--limit", type=int)
    sp = add("ack", cmd_ack, "後続処理の完了（--failed なら失敗）を記録する。後続の DAG の handler_on から呼ぶ")
    sp.add_argument("delivery", help="受け渡しの ID（後続の DAG では ${DAG_RUN_ID}）")
    sp.add_argument("--failed", action="store_true")
    sp.add_argument("--message")
    add("reconcile", cmd_reconcile, "受け渡し先に後続処理の状態を問い合わせて記録する（問い合わせられるアダプタだけ）")
    for name, fn, h in (("retry", cmd_retry, "失敗した受け渡し・後続処理を同じ ID のまま再実行する"),):
        sp = add(name, fn, h)
        sp.add_argument("ids", nargs="*")
        sp.add_argument("--rule")
        sp.add_argument("--target")
        sp.add_argument("--status", help="対象の状態（既定 send_failed,dead,failed）")
    sp = add("skip", cmd_skip, "受け渡しを飛ばす（同じレコードの後ろのイベントを進める）")
    sp.add_argument("ids", nargs="*")
    sp = add("status", cmd_status, "取得元・ルール・受け渡しの状況")
    sp.add_argument("--json", action="store_true")
    sp = add("events", cmd_events, "イベントと受け渡しの一覧（新しい順）")
    sp.add_argument("--rule")
    sp.add_argument("--record")
    sp.add_argument("--status", help=f"カンマ区切り（{', '.join(ALL_STATUSES)}）")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true")
    sp = add("show", cmd_show, "イベントの中身（イベント ID か受け渡しの ID）")
    sp.add_argument("id")
    sp = add("rebaseline", cmd_rebaseline, "ルールの基準値を取り直す（その間の変化では発火しない）")
    sp.add_argument("--rule", action="append")
    sp.add_argument("--source", action="append")
    sp.add_argument("--refetch", action="store_true", help="次の取得を全件にする（増分取得の取得元）")
    sp.add_argument("--now", action="store_true", help="次の判定を待たずにいま取り直す")
    sp = add("replay", cmd_replay, "保存済みの取得結果でルールの判定をやり直す（API には問い合わせない）")
    sp.add_argument("--rule", required=True)
    sp.add_argument("--from-fetch", type=int)
    sp.add_argument("--dry-run", action="store_true", help="結果を出すだけで状態を変えない")
    sp.add_argument("--emit-first", action="store_true", help="最初の取得のレコードも新規として扱う")
    sp.add_argument("--show", type=int, default=20)
    sp = add("records", cmd_records, "保存済みの取得結果のレコードを出す")
    sp.add_argument("--source", required=True)
    sp.add_argument("--fetch", type=int)
    sp.add_argument("--limit", type=int, default=20)
    sp = add("gc", cmd_gc, "保持期間を過ぎたものを消してファイルを縮める（未判定・未完了のものは消さない）")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--vacuum", action="store_true", help="VACUUM で DB を作り直す（時間がかかる。古い DB を自動で縮む形にする）")
    sp = add("forget-handled", cmd_forget_handled,
             "refresh のある受け渡し先が覚えている「処理し終えた状態」を忘れる（次の変化で必ず実行させる）")
    sp.add_argument("--rule", required=True)
    sp.add_argument("--target", action="append")
    sp.add_argument("records", nargs="+", help="レコードのキー（events の「レコード」の値）")
    sp = add("forget-rule", cmd_forget_rule, "設定から消したルールの判定状態を消す")
    sp.add_argument("rule")
    plugins.load(plugin_specs or [], add)
    return p


def main(argv: list[str] | None = None) -> int:
    setup_stdio()
    pre = argparse.ArgumentParser(add_help=False)
    _common(pre)
    known, _ = pre.parse_known_args(argv)
    adapters.reset_plugins()
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
        if a.cmd in ("fetch", "run", "get") and cfg.retention.auto_gc and code in (EXIT_OK, EXIT_PARTIAL):
            gc(cfg, store)
        return code
    except ConfigError as e:
        warn(str(e))
        return EXIT_ERROR
    finally:
        store.close()
