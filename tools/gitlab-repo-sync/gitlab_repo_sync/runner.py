# -*- coding: utf-8 -*-
"""実行ドライバ — 設定どおりにペアを同期し、結果を返す。

実行タイミングは持たない（cron / タスクスケジューラ / 手動のどれから叩かれてもよい）。
このモジュールの責務は「1 回呼ばれたら、今の状態を見て、やるべき同期だけをやる」こと。

1 グループの処理順:
  1. グループ内の各リポジトリの ref 一覧を 1 回ずつ取得する（ペアの数ではなくリポジトリの数）
  2. ペアごとに、前回スナップショットと突き合わせて素の判定を出す
  3. 判定に足りないオブジェクトだけを、リポジトリごとにまとめて fetch する
  4. merge-base で ff を決着させ、ルールの戦略を当てる
  5. ペアをまたいだ書き込み衝突を検出して、衝突した ref は両方止める
  6. リポジトリごとにまとめて push する
"""
from __future__ import annotations

import datetime
import os

from . import planner
from .config import build_groups
from .planner import RefPlan
from .store import GitError, Store, sync_ref
from .util import read_json, safe_name, write_json


# --------------------------------------------------------------------------- #
# 状態ファイル
# --------------------------------------------------------------------------- #

def pair_state_path(cfg, name):
    return os.path.join(cfg.state_dir, "pairs", "%s.json" % safe_name(name))


def load_snapshot(cfg, pair):
    """そのペアが最後に突き合わせた両側の ref 一覧を返す。

    スナップショットは**ペア単位**で持つ。リポジトリ単位にすると、同じリポジトリを
    共有する別のペアが先に走ったときに「観測済み＝同期済み」と誤認して、まだ一度も
    突き合わせていない組を飛ばしてしまう。
    """
    snapshot = read_json(pair_state_path(cfg, pair.name)).get("snapshot", {}) or {}
    return (snapshot.get(planner.SIDE_A, {}) or {},
            snapshot.get(planner.SIDE_B, {}) or {})


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# ペア 1 組ぶんの判定
# --------------------------------------------------------------------------- #

def classify_pair(pair, a_refs, b_refs, prev_a, prev_b):
    """merge-base 抜きで分かるところまで判定する。

    戻り値: [(ref, kind), ...] — kind は planner の定数（COMPARE を含む）
    """
    rule = pair.rule
    candidates = sorted(set(a_refs) | set(b_refs))
    result = []
    for ref in candidates:
        if not planner.ref_in_scope(ref, rule.include, rule.exclude):
            continue
        kind = planner.classify_ref(a_refs.get(ref), b_refs.get(ref),
                                    prev_a.get(ref), prev_b.get(ref),
                                    rule.propagate_deletes)
        result.append((ref, kind))
    return result


def fetch_needs(pair, raw, a_refs, b_refs, a_slug, b_slug, store):
    """判定と push に足りないオブジェクトを、リポジトリごとに洗い出す。

    既にストアに在るものは取りに行かない。前回までに取り込んだ履歴が使い回せるので、
    毎回の転送は実際に増えたぶんだけになる。

    必要なものは**戦略を当てた後のアクション**から決める。素の判定だけで決めると、
    たとえば a-to-b + 削除伝播で「b で消された ref を a から復元する」ケース
    （素の判定は DELETE_ON_A、最終アクションは CREATE_ON_B）で、送るべき a 側の
    オブジェクトを取り忘れる。
    """
    rule = pair.rule
    needs = {a_slug: set(), b_slug: set()}
    for ref, kind in raw:
        if kind == planner.NOOP:
            continue
        if kind == planner.COMPARE:
            # ff 判定には両側のコミットが要る（片方だけでは merge-base を出せない）
            if not store.has_object(a_refs.get(ref)):
                needs[a_slug].add(ref)
            if not store.has_object(b_refs.get(ref)):
                needs[b_slug].add(ref)
            continue
        action = planner.apply_strategy(kind, rule.strategy,
                                        rule.allow_force, rule.propagate_deletes)
        plan = RefPlan(ref=ref, action=action, a_sha=a_refs.get(ref), b_sha=b_refs.get(ref))
        sha = plan.target_sha()
        if sha is None or store.has_object(sha):
            continue                       # 削除アクション、または既に手元に在る
        # 書き込み先の反対側が送り元。その ref はそちらに必ず存在する。
        source = b_slug if plan.target_side() == planner.SIDE_A else a_slug
        needs[source].add(ref)
    return needs


def resolve_pair(pair, raw, a_refs, b_refs, store):
    """COMPARE を決着させ、戦略を当てて最終アクションにする。"""
    rule = pair.rule
    plans = []
    for ref, kind in raw:
        a_sha, b_sha = a_refs.get(ref), b_refs.get(ref)
        if kind == planner.COMPARE:
            base = store.merge_base(a_sha, b_sha)
            kind = planner.resolve_compare(a_sha, b_sha, base,
                                           is_tag=ref.startswith("refs/tags/"))
        action = planner.apply_strategy(kind, rule.strategy,
                                        rule.allow_force, rule.propagate_deletes)
        plans.append(RefPlan(ref=ref, action=action, a_sha=a_sha, b_sha=b_sha))
    return plans


def build_refspecs(pair_plans, slug_of):
    """全ペアの計画を、リポジトリごとの refspec に畳む。

    引数:
      pair_plans … [(pair, [RefPlan, ...]), ...]
      slug_of    … pair と side から slug を引く関数
    戻り値: {slug: [refspec, ...]}

    同じ ref へ同じ SHA を書くペアが複数あっても refspec は 1 本にまとめる
    （衝突は呼び出し側で除外済み）。
    """
    specs = {}
    seen = set()
    for pair, plans in pair_plans:
        for plan in plans:
            side = plan.target_side()
            if side is None:
                continue
            slug = slug_of(pair, side)
            if plan.action in planner.DELETE_ACTIONS:
                spec = ":%s" % plan.ref
            else:
                prefix = "+" if plan.action in planner.FORCE_ACTIONS else ""
                spec = "%s%s:%s" % (prefix, plan.target_sha(), plan.ref)
            key = (slug, spec)
            if key in seen:
                continue
            seen.add(key)
            specs.setdefault(slug, []).append(spec)
    return specs


# --------------------------------------------------------------------------- #
# グループ 1 つぶんの同期
# --------------------------------------------------------------------------- #

def sync_group(cfg, group, pairs, log, dry_run=False):
    """1 グループ（＝1 共有ストア）を同期して、ペアごとのレポートを返す。"""
    store = Store(os.path.join(cfg.store_dir, group.gid), log, timeout=cfg.git_timeout)
    if store.ensure():
        log.info("共有ストアを作成しました: %s（リポジトリ %d 件）"
                 % (store.path, len(group.repos)))

    slug_of = lambda pair, side: (pair.slugs()[0] if side == planner.SIDE_A
                                  else pair.slugs()[1])

    # --- 1. リポジトリごとに 1 回だけ ref 一覧を取る ------------------------- #
    needed_slugs = set()
    for pair in pairs:
        needed_slugs.update(pair.slugs())

    observed, unreachable = {}, {}
    for slug in sorted(needed_slugs):
        url = group.repos[slug]
        try:
            observed[slug] = store.ls_remote(cfg.credentials.authenticated_url(url))
        except GitError as exc:
            unreachable[slug] = str(exc)
            log.error("[%s] ref 一覧の取得に失敗しました: %s" % (slug, exc))
            continue
        log.debug("[%s] ref %d 件" % (slug, len(observed[slug])))

    # --- 2. ペアごとに素の判定を出す ---------------------------------------- #
    reports, active, raws = [], [], {}
    for pair in pairs:
        a_slug, b_slug = pair.slugs()
        broken = [s for s in (a_slug, b_slug) if s in unreachable]
        if broken:
            reports.append(_report(pair, "failed", error=unreachable[broken[0]]))
            continue
        prev_a, prev_b = load_snapshot(cfg, pair)
        if observed[a_slug] == prev_a and observed[b_slug] == prev_b:
            state = read_json(pair_state_path(cfg, pair.name))
            carried = state.get("diverged", []) or []
            carried_conflicts = state.get("conflicts", []) or []
            log.info("[%s] 前回から変化なし。転送を省略します。" % pair.name)
            if carried or carried_conflicts:
                log.warn("[%s] 未解決のものが残っています: %s"
                         % (pair.name, ", ".join(carried + carried_conflicts)))
            reports.append(_report(pair, "unchanged", diverged=carried,
                                   conflicts=carried_conflicts))
            continue
        raws[pair.name] = classify_pair(pair, observed[a_slug], observed[b_slug],
                                        prev_a, prev_b)
        active.append(pair)

    if not active:
        return reports

    # --- 3. 足りないオブジェクトをリポジトリごとにまとめて取る ---------------- #
    merged_needs = {}
    for pair in active:
        a_slug, b_slug = pair.slugs()
        needs = fetch_needs(pair, raws[pair.name], observed[a_slug], observed[b_slug],
                            a_slug, b_slug, store)
        for slug, refs in needs.items():
            merged_needs.setdefault(slug, set()).update(refs)
    for slug in sorted(merged_needs):
        refs = merged_needs[slug]
        if not refs:
            continue
        log.info("[%s] %d 件の ref を取得します" % (slug, len(refs)))
        store.fetch(cfg.credentials.authenticated_url(group.repos[slug]), slug, sorted(refs))

    # --- 4. ff を決着させて戦略を当てる ------------------------------------- #
    pair_plans = []
    for pair in active:
        a_slug, b_slug = pair.slugs()
        plans = resolve_pair(pair, raws[pair.name], observed[a_slug], observed[b_slug], store)
        pair_plans.append((pair, plans))

    # --- 5. ペアをまたいだ書き込み衝突を止める ------------------------------ #
    writes = []
    for pair, plans in pair_plans:
        for plan in plans:
            side = plan.target_side()
            if side is not None:
                writes.append((slug_of(pair, side), plan.ref, plan.target_sha(), pair.name))
    conflicts = planner.detect_write_conflicts(writes)
    if conflicts:
        for (slug, ref), names in sorted(conflicts.items()):
            log.error("書き込みが衝突しました: %s の %s に対して %s が別々の内容を書こうとしています"
                      % (slug, ref, " / ".join(sorted(set(names)))))
        for pair, plans in pair_plans:
            for plan in plans:
                side = plan.target_side()
                if side is not None and (slug_of(pair, side), plan.ref) in conflicts:
                    plan.action = planner.CONFLICT

    # --- 6. リポジトリごとにまとめて push ----------------------------------- #
    specs = build_refspecs(pair_plans, slug_of)
    push_results = {}
    for slug in sorted(specs):
        log.info("[%s] %d 件を push します%s"
                 % (slug, len(specs[slug]), "（dry-run）" if dry_run else ""))
        try:
            push_results[slug] = store.push(
                cfg.credentials.authenticated_url(group.repos[slug]), specs[slug],
                dry_run=dry_run)
        except GitError as exc:
            log.error("[%s] push に失敗しました: %s" % (slug, exc))
            push_results[slug] = {}

    applied = {slug: dict(refs) for slug, refs in observed.items()}
    for pair, plans in pair_plans:
        for plan in plans:
            side = plan.target_side()
            if side is None:
                continue
            slug = slug_of(pair, side)
            plan.ok, plan.detail = push_results.get(slug, {}).get(
                plan.ref, (False, "push 結果に対応する行がありません"))
            if plan.ok and not dry_run:
                if plan.action in planner.DELETE_ACTIONS:
                    applied[slug].pop(plan.ref, None)
                else:
                    applied[slug][plan.ref] = plan.target_sha()

    # --- 7. 記録とレポート -------------------------------------------------- #
    for pair, plans in pair_plans:
        a_slug, b_slug = pair.slugs()
        # 観測結果ではなく「push 後の姿」を保存する。次回の「変化なし」判定と
        # 削除検出の基準になるので、実際に書けたぶんだけを反映する。
        snapshot = {planner.SIDE_A: applied[a_slug], planner.SIDE_B: applied[b_slug]}
        reports.append(_finish_pair(cfg, pair, plans, snapshot, log, dry_run))
    return reports


def _report(pair, status, changed=0, diverged=None, skipped=None,
            conflicts=None, failed=None, error=""):
    report = {"pair": pair.name, "rule": pair.rule.name, "strategy": pair.rule.strategy,
              "status": status, "changed": changed, "diverged": diverged or [],
              "skipped": skipped or [], "conflicts": conflicts or [], "failed": failed or []}
    if error:
        report["error"] = error
    return report


def _finish_pair(cfg, pair, plans, snapshot, log, dry_run):
    diverged = [p.ref for p in plans if p.action == planner.DIVERGED]
    skipped = [p.ref for p in plans if p.action == planner.SKIPPED]
    conflicts = [p.ref for p in plans if p.action == planner.CONFLICT]
    failed = [p.ref for p in plans if p.action in planner.WRITE_ACTIONS and not p.ok]
    changed = [p for p in plans if p.action in planner.WRITE_ACTIONS and p.ok]

    for plan in plans:
        if plan.action in planner.WRITE_ACTIONS:
            emit = log.info if plan.ok else log.error
            emit("[%s]   %s %s (%s)" % (pair.name, plan.action, plan.ref, plan.detail))
        elif plan.action == planner.DIVERGED:
            log.warn("[%s]   分岐のため同期しません: %s（a=%s b=%s）"
                     % (pair.name, plan.ref, (plan.a_sha or "-")[:8], (plan.b_sha or "-")[:8]))
        elif plan.action == planner.CONFLICT:
            log.warn("[%s]   他のペアと書き込みが衝突: %s" % (pair.name, plan.ref))
        elif plan.action == planner.SKIPPED:
            log.debug("[%s]   戦略 %s の対象外: %s" % (pair.name, pair.rule.strategy, plan.ref))

    if failed:
        status = "failed"
    elif diverged or conflicts:
        status = "diverged"
    else:
        status = "ok"

    if not dry_run:
        state = {"last_run_at": _now(), "status": status, "diverged": diverged,
                 "conflicts": conflicts, "changed": len(changed)}
        # push に失敗した ref があるときはスナップショットを更新しない。更新すると
        # 次回に「変化なし」と判定され、失敗したまま再試行されなくなる。
        if not failed:
            state["snapshot"] = snapshot
        else:
            state["snapshot"] = read_json(pair_state_path(cfg, pair.name)).get("snapshot", {})
        write_json(pair_state_path(cfg, pair.name), state)
    log.info("[%s] 完了: %s（更新 %d 件 / 分岐 %d 件）"
             % (pair.name, status, len(changed), len(diverged)))
    return _report(pair, status, len(changed), diverged, skipped, conflicts, failed)


# --------------------------------------------------------------------------- #
# 全体の実行
# --------------------------------------------------------------------------- #

def run(cfg, log, only=None, dry_run=False):
    """設定内のペアを同期する。戻り値は終了コード。"""
    # グループは**常に全ペア**から組む。--pair で絞ったときにストアの単位が変わると、
    # 同じリポジトリを別の場所へもう一度 clone することになるため。
    groups = build_groups(cfg.pairs)
    if only is not None and cfg.pair(only) is None:
        log.error("設定に無いペアです: %s" % only)
        return 1

    reports = []
    for group in groups:
        selected = [p for p in group.pairs
                    if p.enabled and (only is None or p.name == only)]
        disabled = [p for p in group.pairs if not p.enabled and (only is None or p.name == only)]
        for pair in disabled:
            log.debug("[%s] enabled: false のためスキップします" % pair.name)
        if not selected:
            continue
        try:
            reports.extend(sync_group(cfg, group, selected, log, dry_run=dry_run))
        except (GitError, OSError) as exc:
            log.error("グループ %s の同期に失敗しました: %s" % (group.gid, exc))
            for pair in selected:
                reports.append(_report(pair, "failed", error=log.redact(exc)))

    if not reports:
        log.info("同期対象のペアがありません。")
        return 0

    failed = [r for r in reports if r["status"] == "failed"]
    attention = [r for r in reports if r["diverged"] or r["conflicts"]]
    write_report(cfg, reports, dry_run=dry_run)

    log.info("サマリ: %d ペア / 更新 %d 件 / 失敗 %d / 要対応 %d"
             % (len(reports), sum(r["changed"] for r in reports), len(failed), len(attention)))
    for r in attention:
        if r["diverged"]:
            log.warn("要対応（分岐）: %s -> %s" % (r["pair"], ", ".join(r["diverged"])))
        if r["conflicts"]:
            log.warn("要対応（衝突）: %s -> %s" % (r["pair"], ", ".join(r["conflicts"])))
    if failed:
        return 1
    if attention and cfg.fail_on_diverged:
        return 2
    return 0


def write_report(cfg, reports, dry_run=False):
    """最新の実行結果を残す（監視や通知から拾えるように）。"""
    if dry_run:
        return
    write_json(os.path.join(cfg.state_dir, "last-report.json"),
               {"finished_at": _now(), "pairs": reports})


# --------------------------------------------------------------------------- #
# 表示コマンド
# --------------------------------------------------------------------------- #

def print_status(cfg, out=print):
    out("%-22s %-14s %-18s %-9s %-20s" % ("PAIR", "RULE", "STRATEGY", "STATUS", "LAST RUN"))
    for pair in cfg.pairs:
        state = read_json(pair_state_path(cfg, pair.name))
        status = state.get("status", "-")
        if not pair.enabled:
            status = "disabled"
        out("%-22s %-14s %-18s %-9s %-20s"
            % (pair.name, pair.rule.name, pair.rule.strategy, status,
               state.get("last_run_at", "-")))
        for key, label in (("diverged", "分岐"), ("conflicts", "衝突")):
            refs = state.get(key) or []
            if refs:
                out("    %s: %s" % (label, ", ".join(refs)))
    report = read_json(os.path.join(cfg.state_dir, "last-report.json"))
    if report.get("finished_at"):
        out("")
        out("最終実行: %s" % report["finished_at"])


def print_layout(cfg, out=print):
    """どのリポジトリがどのストアを共有するか（＝clone が何個できるか）を表示する。"""
    groups = build_groups(cfg.pairs)
    out("ペア %d 組 / リポジトリ %d 個 / 共有ストア %d 個"
        % (len(cfg.pairs), len(cfg.repos()), len(groups)))
    for group in groups:
        out("")
        out("ストア %s -> %s" % (group.gid, os.path.join(cfg.store_dir, group.gid)))
        for slug in sorted(group.repos):
            out("  リポジトリ %-52s %s" % (slug, group.repos[slug]))
        for pair in group.pairs:
            a_slug, b_slug = pair.slugs()
            out("  ペア %-16s %s %s %s  [%s]"
                % (pair.name, a_slug,
                   {"a-to-b": "->", "b-to-a": "<-"}.get(pair.rule.strategy, "<->"),
                   b_slug, pair.rule.name + ("" if pair.enabled else ", disabled")))


def print_refs(cfg, out=print):
    """最後に突き合わせた ref と、ストア内での置き場所を見せる（トラブルシュート用）。"""
    for pair in cfg.pairs:
        a_slug, b_slug = pair.slugs()
        prev_a, prev_b = load_snapshot(cfg, pair)
        out("ペア %s" % pair.name)
        for slug, url, refs in ((a_slug, pair.a_url, prev_a), (b_slug, pair.b_url, prev_b)):
            out("  %s (%s) — 記録済み %d 件" % (slug, url, len(refs)))
            for ref in sorted(refs):
                out("    %-38s %s -> %s" % (ref, refs[ref][:8], sync_ref(slug, ref)))
