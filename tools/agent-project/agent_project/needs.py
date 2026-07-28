from __future__ import annotations
# needs.py — 元 agent-project.py の 2112-2609 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# 通知（案件毎 needs/<id>.md）＋ フィードバック往復
# ---------------------------------------------------------------------------
def needs_path(cfg: "Config", tid: str) -> Path:
    return cfg.needs / f"{tid}.md"


# needs へ載せる失敗の構造化フィールド。表示層はこれを読むだけにして、散文の再解析をやめる
# （書き手の文言が変わると読み手の正規表現が外れ、表示だけが静かに壊れるのを断つ）。
# 値は 1 行・キーは frontmatter 互換。空の項目は書かない＝旧記録と同じ見た目を保つ。
_FAILURE_FM_KEYS = (
    ("failure-class", "cls"),            # proximate cause（control/quota/auth/env/transient）
    ("failure-chain", "chain"),          # 観測した全分類（カンマ区切り。根拠として保持）
    ("failure-phase", "phase"),          # どの工程まで到達したか
    ("verify-verdict", "verdict"),       # 検証の結末（passed/failed/not_run/unknown）
    ("failure-summary", "summary"),      # 解釈できた失敗の要約（できなければ書かない）
    ("failure-resolution", "resolution"),
    ("failure-category", "category"),
    ("failure-owner", "owner"),
    ("failure-command", "command"),
    ("failure-workdir", "workdir"),
    ("failure-exit", "exit_code"),
    ("failure-target", "resolved_target"),
)


def _failure_frontmatter(failure: "dict | None") -> str:
    """失敗の構造化フィールドを frontmatter 行へ。改行は 1 行に畳む（キー: 値 の契約を守る）。"""
    if not failure:
        return ""
    out = ""
    for key, src in _FAILURE_FM_KEYS:
        val = failure.get(src)
        if isinstance(val, (list, tuple)):
            val = ",".join(str(v) for v in val)
        val = " ".join(str(val or "").split())
        if val:
            out += f"{key}: {val}\n"
    return out


def _madr_frontmatter(rec_id: str, kind: str, risk: str = "",
                      mr_url: str = "", delivery: "list | None" = None,
                      failure: "dict | None" = None,
                      verification: "dict | None" = None) -> str:
    """needs/<id>.md の MADR（Markdown Any Decision Records）互換 frontmatter。
    status は常に proposed で生成し、人の確定（[x]）＝決定。ファイル自体は取り込み時に
    消費され、恒久の決定記録は decisions/<id>.md（DR）に残る。
    risk（low/med/high）は検収票のリスクダイジェスト総合値（viewer のバッジ用）。
    mr-url / delivery は検収サブ画面向け（GitLab MR を開く・複数リポジトリの差分一覧）。"""
    extra = ""
    if risk:
        extra += f"risk: {risk}\n"
    if mr_url:
        # URL に空白は来ない想定。frontmatter は1行キーだけを viewer が読む。
        extra += f"mr-url: {mr_url}\n"
    if delivery:
        # JSON 1 行（viewer がパース）。複数リポジトリの書込/参照を構造化する。
        extra += f"delivery: {json.dumps(delivery, ensure_ascii=False, separators=(',', ':'))}\n"
    if verification and (verification.get("criteria") or []):
        # 検証レポートの要約（S5）。人が検収で読むのは「コマンド」ではなく **基準と証跡**——
        # コマンドの良し悪しは判断できないが、基準と証跡なら判断できる。
        extra += ("verification: "
                  + json.dumps({"criteria": verification["criteria"],
                                "report": verification.get("report", ""),
                                "pass": verification.get("pass", 0)},
                               ensure_ascii=False, separators=(",", ":")) + "\n")
    extra += _failure_frontmatter(failure)
    return (
        "---\n"
        "status: proposed\n"
        f"date: {_now_ts()[:10]}\n"
        "decision-makers: [human]\n"
        f"task-id: {rec_id}\n"
        f"kind: {kind}\n"
        + extra
        + "---\n\n"
    )


def write_needs_file(cfg: "Config", task: Task, reason: str, review: bool = False,
                     evidence: str = "", kind: str = "",
                     risk: "tuple[str, str] | None" = None,
                     mr_url: str = "", delivery: "list | None" = None,
                     failure: "dict | None" = None,
                     verification: "dict | None" = None) -> None:
    cfg.needs.mkdir(parents=True, exist_ok=True)
    if kind == "plan-review":   # 実行前レビュー（proposed。承認されるまで実行しない）
        state = "proposed（実行前レビュー待ち・未実行）"
        hint = (f"<!-- 承認して実行を許可するなら `agent-project approve {task.id}`（または空のまま [x]）。\n"
                f"     差し戻す（agent-project にタスクを修正させる）なら下に修正指示を書いて [x]。\n"
                f"     却下（廃止して関連バックログを再計画）なら `agent-project reject {task.id} --reason ...`。 -->\n")
        evidence_block = f"\n## タスク定義（レビュー対象）\n{evidence}\n" if evidence else ""
        body = (
            f"{_madr_frontmatter(task.id, kind)}"
            f"# 実行前レビュー: {task.id} — {task.title}\n\n"
            f"## Context and Problem Statement\n\n"
            f"- なぜ: {reason}\n"
            f"- 状態: {state}\n"
            f"{evidence_block}\n"
            f"{DECISION_MARKER}\n\n"
            f"<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->\n"
            f"- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n\n"
            f"{hint}"
        )
        needs_path(cfg, task.id).write_text(body, encoding="utf-8")
        return
    if review:    # verify=PASS の承認ゲート（検収待ち）
        state = "review（検収待ち・verify=PASS）"
        kind = "review"
        hint = (f"<!-- 承認して done 確定するなら `agent-project approve {task.id}`。\n"
                f"     差し戻すなら下に修正方針を書いて [x] にする（再実行されます）。 -->\n")
    else:
        state = "blocked（agent-project の判断待ち）"
        kind = "blocked"
        hint = (f"<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。\n"
                f"     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。\n"
                f"     コマンドなら `agent-project approve {task.id}`。 -->\n")
    # 判断材料（成果物の所在・差分・検証）。人がレビューせずに済むよう「どこに・何が・なぜ」を載せる。
    evidence_block = f"\n## 判断材料（成果物の所在・差分・検証）\n{evidence}\n" if evidence else ""
    # リスクダイジェスト（検収票のみ・決定的な材料のみ）。総合値は frontmatter（viewer バッジ用）にも載せる。
    risk_block = f"\n## リスク\n{risk[1]}\n" if risk else ""
    # 検収サブ画面向け: MR URL とリポジトリ単位の構造化ペイロード（無ければ空）。
    fm_mr = str(mr_url or (task.get("mr_url") if review else "") or "").strip()
    fm_delivery = delivery if delivery is not None else None
    body = (
        f"{_madr_frontmatter(task.id, kind, risk=risk[0] if risk else '', mr_url=fm_mr if review else '', delivery=fm_delivery, failure=failure, verification=verification)}"
        f"# 要対応: {task.id} — {task.title}\n\n"
        f"## Context and Problem Statement\n\n"
        f"- なぜ: {reason}\n"
        f"- 状態: {state}\n"
        f"{evidence_block}"
        f"{risk_block}\n"
        f"{DECISION_MARKER}\n\n"
        f"<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->\n"
        f"- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n\n"
        f"{hint}"
    )
    needs_path(cfg, task.id).write_text(body, encoding="utf-8")


def _task_definition_block(task: Task) -> str:
    """実行前レビュー票に載せるタスク定義（人がレビューする対象そのもの）。

    **受入基準（acceptance）は箇条書きで先に出す。**S5 で done の根拠が「1 行のコマンド」から
    「基準 × 証跡」に変わった以上、実行前に人が読んで直すべき一次表現はこれである
    （ここに出ていないものは、結局レビューされない）。
    """
    lines = [f"- title  : {task.title}",
             f"- verify : `{task.verify}`" if task.verify else "- verify : （未定義）"]
    crit = task_acceptance(task)
    if crit:
        lines.append("- acceptance（受入基準・検証エージェントがこれを証跡付きで判定します）:")
        lines += [f"    {i}. {c}" for i, c in enumerate(crit, 1)]
        if len(crit) > 7:
            lines.append(f"    ※ 基準が {len(crit)} 件あります（目安は 3〜7 件）。"
                         "多すぎる場合はタスクの分割を検討してください")
    for k in (*TASK_GUIDE_KEYS,   # 誘導・レビュー記述（why/desc/scope/…＝人がレビューする判断材料）
              # accept は task_acceptance が上の箇条書きへ畳むので、ここでは出さない（二重表示を避ける）
              "size", "verify_template", "after", "note", "workspace", "charter",
              "assess", "route"):   # assess=投入時採点（c/r/a）・route=spec ルーティングの決定
        v = task.get(k)
        if v:
            lines.append(f"- {k}: {v}")
    if task.priority:
        lines.append(f"- priority: {task.priority}")
    lines.append(f"- source : {task.source}")
    return "\n".join(lines)


# 人の判断を待っている状態（needs/<id>.md が対応する）。ここに入ったタスクは必ず票を持つ。
NEEDS_STATUSES = ("proposed", "blocked", "review")


def _remember_needs_reason(task: "Task", reason: str) -> None:
    """needs の再生成に要る理由をタスク自身へ残す（md の `- needs_reason:`）。

    従来 理由はメモリ（run_loop の reasons dict）にしか無かった。needs ファイルが失われると
    理由ごと消え、二度と票を作り直せない。タスクを正にするため、状態と一緒に理由も持たせる。"""
    task.drop("needs_reason")
    if reason:
        task.extra.append(("needs_reason", str(reason).replace("\n", " ⏎ ")[:300]))


def mark_needs_entry(cfg: "Config", task: "Task") -> None:
    """このタスクを判断待ちへ入れた時点の決定記録の位置を印として残す（`- needs_dr:`）。

    `_already_decided` が「人が答えた票の作り直し」と「その後で機械が改めて止めた新しい
    判断待ち」を区別するための唯一の材料。押さずに済ませると、決定の後に再 blocked に
    なったタスクの票が失われたとき、自己修復が働かなくなる（＝画面から消える）。
    呼び出し側が直後に persist_task すること（判断待ちへ入れる遷移と同じ書き込みに乗せる）。"""
    task.drop("needs_dr", "decided_by")
    dr = latest_dr_id(cfg, task.id)
    if dr:
        task.extra.append(("needs_dr", dr))


def _already_decided(cfg: "Config", task: "Task", st: str) -> bool:
    """この票は「人がもう答えた判断」の作り直しか（＝再投影してはいけないか）。

    判断待ちの復活ループ（総覧 G-2）を塞ぐ。状態の同期が競合したとき、backlog は機械状態＝
    ローカル優先で裁定されるため、**人の決定を受け取り損ねた PC の status だけが古いまま**
    残りうる。そのまま毎パスの自己修復（ensure_needs）が走ると、人が答え終わった票を作り直し、
    全 PC へ再伝播する——「同じ判断を人に二度させない」（コンセプト正典 C3）の直接違反。

    決定記録（`decisions/<id>.md`）は追記のみで衝突なく合流するので、status の同期が
    競合しても人の決定だけは届く。そこで **最後の人の DR が「判断待ちではない status」へ
    移したと記録しているのに、手元の status がまだ判断待ちなら、古いのは手元**と見なす。

    機械（auto/system/gitlab/forge）の記録は根拠にしない——人が答えていない票を消してしまう。
    人の決定の後で機械が改めて止めた場合（再 blocked 等）は、その遷移を起こした経路自身が
    票を書く（`_block` → `write_needs_file`）ので、ここで作り直さなくても票は在る。"""
    dec = last_human_decision(cfg, task.id)
    if not dec:
        return False
    to = str(dec.get("to") or "")
    if not to or to == st or to in NEEDS_STATUSES:
        return False                 # 人の決定も判断待ち行き（差し戻し等）なら投影を止めない
    # 人の決定の**後で**機械が改めて判断待ちへ入れた（再 blocked・再検収）なら、それは
    # 新しい判断であって作り直しではない。判断待ちへ入れた側が押した印（needs_dr）と
    # 突き合わせる（`mark_needs_entry`）。
    if dr_num(task.get("needs_dr")) >= dr_num(dec.get("dr")):
        return False
    if str(task.get("decided_by") or "").strip() != str(dec.get("dr") or ""):
        # 見送りは 1 回だけ記録する（毎パス書くと journal が埋まる）。印はタスク自身に残す。
        task.set("decided_by", str(dec.get("dr") or ""))
        persist_task(cfg, task)
        append_journal(cfg.journal,
                       f"needs 再投影を見送り: {task.id}（人の決定 {dec.get('dr') or ''} "
                       f"{dec.get('action') or ''} は {to} を指しているのに手元は {st}）")
    return True


def ensure_needs(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """人の判断待ち（proposed / blocked / review）のタスクに needs/<id>.md が無ければ、タスクの
    status から作り直す。既にあれば触らない（人の記入を消さない）。再生成した ID を返す。

    **needs は status の投影であって、独立した真実ではない。** 従来は「状態が変わった瞬間」に
    しか票を書いていなかった（_block / triage の遷移／proposed だけの ensure）。そのため票が
    失われると（feedback 取り込みの unlink、状態の同期・コピー事故、enqueue 直後のラグ）二度と
    作られず、backlog は blocked のままなのに viewer の要対応画面には出てこない — 人はその
    タスクを承認も再実行も差し戻しもできない袋小路に入った（viewer の操作ボタンは全て needs
    カードに紐づくため）。毎パス status を正として整合させ、票を失っても自己修復させる。"""
    made: "list[str]" = []
    for t in tasks:
        st = t.norm_status()
        if st not in NEEDS_STATUSES or needs_path(cfg, t.id).exists():
            continue
        if _already_decided(cfg, t, st):
            continue
        why = str(t.get("needs_reason") or "").strip()
        ev = _task_definition_block(t)
        if st == "proposed":
            write_needs_file(cfg, t, why or "新規タスクの実行前レビュー（承認されるまで実行しません）",
                             evidence=ev, kind="plan-review")
        elif st == "review":
            # 再生成時も検収サブ画面向けに成果差分・MR を載せる（タスク定義だけだと差分が無い）。
            mr = str(t.get("mr_url") or "").strip()
            try:
                ev_review = delivery_evidence(cfg, "", None, "local", task=t, mr_url=mr)
                delivery = delivery_entries(cfg, t, mr_url=mr)
            except Exception:  # noqa: BLE001 — 再生成は失敗しても票自体は起こす
                ev_review, delivery = ev, None
            write_needs_file(cfg, t, why or "成果物の検収待ち（承認すると完了になります）",
                             review=True, evidence=ev_review, mr_url=mr, delivery=delivery)
        else:  # blocked
            try:
                delivery = delivery_entries(cfg, t)
            except Exception:  # noqa: BLE001 — 再生成は失敗しても票自体は起こす
                delivery = None
            write_needs_file(cfg, t, why or f"実行が止まっています（retries={t.retries}）。"
                                            "指示を送るか、そのまま再実行してください。",
                             evidence=ev, delivery=delivery)
        append_journal(cfg.journal, f"needs 再生成: {t.id}（{st}）")
        made.append(t.id)
    return made


def ensure_plan_review_needs(cfg: "Config", tasks: "list[Task]") -> None:
    """後方互換の薄い別名（proposed だけでなく判断待ち全体を面倒見る ensure_needs へ委譲）。"""
    ensure_needs(cfg, tasks)


# 掃除の対象にするタスク級の票の kind（明示の allowlist）。milestone は reconcile_milestones が
# 持ち主なので触らない。kind が読めない票（旧形式・書きかけ）も触らない＝迷ったら残す。
_TASK_NEEDS_KINDS = {"plan-review", "review", "blocked"}


def reap_orphan_needs(cfg: "Config") -> "list[str]":
    """backlog に対応するタスクが無い needs/<id>.md を掃除する。消した ID を返す。

    **ensure_needs の対**。票は status の投影（ensure_needs の docstring）なのに、従来は
    「投影を作る」側しか無く、「投影元が消えたら票も消す」側が無かった。そのため
    タスクを消した後（viewer の削除・手作業・同期事故）に票だけが残り、しかも残った票は
    どこからも消せない袋小路になっていた:

      - `ingest_feedback` は対応タスクが無い票を読み飛ばす（`t is None` → continue）＝
        [x] を付けても取り込まれず、ファイルも消えない。
      - `has_work` / `_has_pending_input` は [x] 済みの票を「人の入力あり」と数える＝
        watch が毎パス起きるのに何も処理できない空回りになる。
      - 手で消しても、タスクが blocked/review/proposed のまま残っていれば ensure_needs が
        作り直す（設計どおり）。人には「消しても復活する」としか見えない。

    milestone 票は `reconcile_milestones` が project.json の status を正として掃除する
    （所有者が別）。ここは kind の allowlist でタスク級の票だけに触る。
    書きかけ（静穏化前）は触らない——同期で票が先に着地しタスクが次パスで来る取り違えを避ける。"""
    reaped: "list[str]" = []
    if not cfg.needs.exists():
        return reaped
    # backlog は「ファイル名 = タスク ID」なので、掃除の判定に本文の解析は要らない。
    # 自前で読み直すのは呼び出し側が渡した部分集合で誤って全部消さないため（誤用不能にする）。
    alive = {p.stem for p in cfg.backlog.glob("*.md")} if cfg.backlog.exists() else set()
    for nf in sorted(cfg.needs.glob("*.md")):
        if nf.stem in alive or _needs_kind(nf) not in _TASK_NEEDS_KINDS:
            continue
        if not settled(cfg, nf):        # 直近に書かれた票は見送る（次パスで掃除する）
            continue
        try:
            nf.unlink()
        except OSError:
            continue
        append_journal(cfg.journal, f"needs 掃除: {nf.stem}（対応するタスクが backlog に無い）")
        reaped.append(nf.stem)
    return reaped


def reconcile_needs(cfg: "Config", tasks: "list[Task]") -> "tuple[list[str], list[str]]":
    """タスク級の needs を backlog の status へ毎パス一致させる唯一の調整点（GC）。
    作る（ensure_needs）と消す（reap_orphan_needs）を必ず対で回すための入口。
    milestone 票の同等物は `reconcile_milestones`。返り値は (再生成した ID, 掃除した ID)。"""
    return ensure_needs(cfg, tasks), reap_orphan_needs(cfg)


def prune_dangling_afters(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """`after` の先行 id が backlog にも archive にも無い（＝物理削除された）タスクから、
    その辺を外す。after を編集した（＝切り離した）タスクの id を返す。

    却下（reject）は依存先の after 編集と再審査まで自分で行うが、物理削除は viewer の
    ファイル操作なので、依存側の切り離しはこの整合点が毎パス引き受ける。実行順だけなら
    `unmet_deps` が「無い id ＝満たし」と読むので詰まりはしないが、参照を残すと
    (1) viewer の依存表示・impact が存在しないタスクを指し続ける
    (2) 同じ id が後で再利用されたとき、無関係な新タスクが突然「先行」になる。
    archive にある id（done/rejected）は外さない——実行済みの順序の記録として意味があり、
    unmet_deps 上も満たし扱いで挙動が同じため。"""
    adir = cfg.archive_dir()
    archived = {p.stem for p in adir.glob("*.md")} if adir.exists() else set()
    alive = {t.id for t in tasks}
    pruned: "list[str]" = []
    for t in tasks:
        deps = task_deps(t)
        keep = [d for d in deps if d in alive or d in archived]
        if keep == deps:
            continue
        gone = [d for d in deps if d not in keep]
        t.drop("after")
        if keep:
            t.extra.append(("after", ", ".join(keep)))
        persist_task(cfg, t)
        append_journal(cfg.journal,
                       f"依存の切り離し: {t.id} の after から {', '.join(gone)} を外す"
                       f"（backlog にも archive にも無い＝削除済み）")
        pruned.append(t.id)
    return pruned


def reap_orphan_task_state(cfg: "Config") -> "list[str]":
    """タスク本体（backlog / archive）を失った id の付随状態を物理削除する。掃除した id を返す。

    対象は参照する主体が無く、残しても効果の無い孤児だけ:
      - `verifications/<id>/` … 検証レポート。読む導線はタスク/archive 記録から
      - `brief/<id>.md`       … run ブリーフ。残すと同じ id の再利用時に古い内容が注入される
      - `claims/<id>.lock`    … 実行権ロック。backlog に無い id は誰も claim を参照しない
    archive に居る id（done/rejected）の verifications/brief は記録の一部なので触らない。
    claims だけは backlog 基準（archive 行きの時点で実行権は意味を失う）。
    書きかけ保護は needs と同じ静穏化（settled）を通す。"""
    backlog_ids = {p.stem for p in cfg.backlog.glob("*.md")} if cfg.backlog.exists() else set()
    adir = cfg.archive_dir()
    recorded = backlog_ids | ({p.stem for p in adir.glob("*.md")} if adir.exists() else set())
    reaped: "set[str]" = set()
    root = cfg.backlog.parent
    vdir = root / "verifications"
    if vdir.exists():
        for d in sorted(p for p in vdir.iterdir() if p.is_dir()):
            if d.name in recorded or not settled(cfg, d):
                continue
            shutil.rmtree(d, ignore_errors=True)
            reaped.add(d.name)
    bdir = root / "brief"
    if bdir.exists():
        for f in sorted(bdir.glob("*.md")):
            if f.stem in recorded or not settled(cfg, f):
                continue
            with contextlib.suppress(OSError):
                f.unlink()
                reaped.add(f.stem)
    cdir = root / "claims"
    if cdir.exists():
        for f in sorted(cdir.glob("*.lock")):
            if f.stem in backlog_ids or not settled(cfg, f):
                continue
            with contextlib.suppress(OSError):
                f.unlink()
                reaped.add(f.stem)
    for tid in sorted(reaped):
        append_journal(cfg.journal, f"孤児の付随状態を掃除: {tid}（タスク本体なし）")
    return sorted(reaped)


def clear_needs_file(cfg: "Config", tid: str) -> None:
    p = needs_path(cfg, tid)
    if p.exists():
        p.unlink()


def read_feedback(path: Path) -> str:
    """決定記入欄（『## Decision Outcome』または旧『## フィードバック』）以降の人の記入
    （HTMLコメント・チェックボックス行は除く）を取り出す。"""
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    hits = [(text.find(m), m) for m in FEEDBACK_MARKERS]
    hits = [(i, m) for i, m in hits if i >= 0]
    if not hits:
        return ""
    i, marker = min(hits)
    body = text[i + len(marker):]
    lines = [ln for ln in body.splitlines() if not CHECKBOX_RE.match(ln)]
    return "\n".join(lines).strip()


def feedback_submitted(path: Path) -> bool:
    """確定チェックボックスが [x] かどうか（= 人が編集を終えた明示シグナル）。
    Decision Outcome / フィードバック欄配下だけを見る（本文のチェックリスト誤発火を防ぐ）。"""
    text = path.read_text(encoding="utf-8")
    hits = [(text.find(m), m) for m in FEEDBACK_MARKERS]
    hits = [(i, m) for i, m in hits if i >= 0]
    if not hits:
        return False
    i, marker = min(hits)
    body = text[i + len(marker):]
    return any(CHECKED_RE.match(ln) for ln in body.splitlines())


def settled(cfg: "Config", f: Path) -> bool:
    """watch 中の静穏化ガード: 最終保存から debounce 秒経つまでは触らない（書きかけ保護）。

    人の入力（needs/ の記入・commands/ のドロップ）を「まだ触ってよいか」で判定する唯一の場所。
    has_work（起床するか）と各 ingest（処理するか）がこの述語を共有していないと、起床したのに
    何も処理しないパスが生まれ、そのパスが charter を再評価して承認済みマイルストーンを
    書き直してしまう（要対応が復活する原因）。"""
    if not (cfg.watch and cfg.debounce > 0):
        return True
    try:
        return (time.time() - f.stat().st_mtime) >= cfg.debounce
    except OSError:
        return False


def ingest_feedback(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """needs/<id>.md の確定（[x]）を検知したら、対象をブロック解除し内容を次の act に渡す。

    明示シグナル（チェックボックス [x]）必須。書きかけでの誤発火を防ぐため、watch 中は
    最終保存から cfg.debounce 秒が経過するまで待つ（静穏化）。"""
    ingested: list[str] = []
    if not cfg.needs.exists():
        return ingested
    by_id = {t.id: t for t in tasks}
    for nf in sorted(cfg.needs.glob("*.md")):
        if not feedback_submitted(nf):                 # [x] が無ければ確定していない
            continue
        if not settled(cfg, nf):                        # 直近に編集 → 静穏化を待つ
            continue
        t = by_id.get(nf.stem)
        if t is None:
            continue
        fb = read_feedback(nf)
        if t.norm_status() == "proposed":            # 実行前レビューの決着（承認 or 差し戻し）
            # 票を読んで確定した＝人がこの計画を引き受けた。以後 backlog-planner には
            # 「人が確定済み・作り直すな」として届く（S6-3。revise と同じ印）。
            t.set("edited", "human")
            persist_task(cfg, t)
            if fb:                                   # 差し戻し: agent-project がタスクを修正して再提案
                plan_rework(cfg, t, fb)              # （新しいレビュー票を needs に書き直す）
            else:                                    # 空のまま [x] = 承認（実行を許可）
                _plan_approve(cfg, t, "チェックで承認")   # （needs は消える）
            ingested.append(t.id)
            continue
        was_review = t.norm_status() == "review"     # 検収待ちからの復帰か（自律度の clean/手戻り判定用）
        if was_review and not fb:
            # review の空 [x] ＝承認（票の hint どおり approve と同義）。CLI の approve と
            # 同じ done 確定経路へ委譲する——従来はここで ready に戻していたため、承認した
            # つもりの検証済み成果が丸ごと最初から再実行される手戻りになっていた
            # （autonomy には clean 承認と記録しながら再実行する矛盾）。
            release_claim(cfg, t)
            ok, msg = approve_review_done(cfg, t, "needs チェックで承認")
            if ok:
                nf.unlink(missing_ok=True)   # approve 側で書き直された票が無ければ消す
                append_journal(cfg.journal, f"feedback 取り込み: {t.id} を承認（done 確定）")
                ingested.append(t.id)
            else:
                # MR 未クリーン等 → 票は approve_review_done が書き直し済み（review のまま）
                append_journal(cfg.journal, f"feedback 取り込み: {t.id} の承認は保留（{msg[:120]}）")
            continue
        # 委譲中の needs [x] も approve と同じく flow を止めてから ready（二重書き込み防止）
        if t.norm_status() == "offloaded" or t.get("flow_run"):
            detached = detach_flow_run(cfg, t, (fb or "feedback")[:120] or "feedback により委譲から切り離し")
            if detached and not fb:
                # 空 [x] でも id 衝突を避ける（メモ付きは下で retries+=1。env_resume は別扱）
                t.retries += 1
        t.status = "ready"
        t.drop("feedback")
        if fb:
            t.extra.append(("feedback", fb.replace("\n", " ⏎ ")))
            if t.get("env_resume"):
                # 環境復帰のメモ（「トークン直した」等）は計画変更ではない。
                # retries を上げず env_resume を残し、run_id_for が同 run を再開する契約を守る。
                pass
            else:
                # 計画変更＝新しい run。retries を進めないと last_run と同じ id を再生成し、
                # agent-flow は既存 meta.request で再開して差し戻しが planner に届かない。
                t.retries += 1
                # 差し戻しの意図を run ブリーフへ蓄積する。feedback フィールドは次の差し戻しで上書きされるが、
                # ブリーフは追記のみ＝過去の制約が消えず、新 run（ap/<task-id> 同一ブランチ）の全ノードへ伝播する。
                append_brief_item(cfg, t, fb, source="feedback")
        if was_review:                               # review→ feedback あり=差し戻し(手戻り) / 無し=承認(clean)
            autonomy_record(cfg, t, clean=not bool(fb))
        persist_task(cfg, t)
        append_decision(cfg, t.id, cfg.actor, context=f"{t.id}（{t.title}）に人のフィードバック",
                        action="feedback-resume", reason=fb[:200] if fb else "チェックで承認",
                        affects=f"{t.id} → ready", learn=(t.title, fb) if fb else None)
        nf.unlink()
        append_journal(cfg.journal, f"feedback 取り込み: {t.id} を再開")
        ingested.append(t.id)
    return ingested



def _extract_json_object_loose(text: str) -> "dict | None":
    """エージェント出力から最初の JSON オブジェクトを寛容に取り出す（_extract_json_array の単体版）。"""
    s = str(text or "")
    i = s.find("{")
    while i >= 0:
        depth = 0
        for j in range(i, len(s)):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(s[i:j + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        break
                    break
        i = s.find("{", i + 1)
    return None


def _plan_approve(cfg: "Config", t: Task, reason: str) -> None:
    """実行前レビューの承認: proposed → ready（verify を用意できなければ inbox＝triage 行き）。"""
    t.status = "ready" if has_verify_plan(t) else "inbox"
    persist_task(cfg, t)
    clear_needs_file(cfg, t.id)
    append_decision(cfg, t.id, cfg.actor, context=f"{t.id}（{t.title}）の実行を承認",
                    action="plan-approve", reason=reason, affects=f"{t.id} → {t.status}")
    append_journal(cfg.journal, f"plan-review 承認: {t.id} → {t.status}")


def _plan_rework_prompt(t: Task, feedback: str) -> str:
    return (
        "あなたはバックログタスクの定義を人のレビュー指摘に沿って修正する編集者です。\n"
        "以下のタスク定義を、指摘を反映した形に修正してください。\n\n"
        f"## 現在のタスク定義\n{_task_definition_block(t)}\n\n"
        f"## 人のレビュー指摘（必ず反映する）\n{feedback}\n\n"
        "出力は JSON オブジェクトのみ: {\"title\": str, \"verify\": str（終了コード0=PASSのシェル。"
        "書けなければ空）, \"accept\": str（自然言語の完了条件・任意）, \"after\": str（依存タスクID・"
        "カンマ区切り・任意）, \"priority\": int（任意）, \"note\": str（任意）, "
        "\"why\": str（背景・目的・任意）, \"desc\": str（作業内容の詳細・任意）, "
        "\"scope\": str（変更してよい範囲・任意）, \"out_of_scope\": str（やらないこと・任意）, "
        "\"constraints\": str（タスク固有の制約・任意）, \"hints\": str（実装の手がかり・任意）, "
        "\"demo\": str（人の確認観点・任意）}。"
        "指摘がスコープ・制約・意図に関わるなら該当フィールドへ反映し、"
        "空欄で内容を補える項目（why/scope 等）は妥当な範囲で補完してよい（各 1 行・改行は ⏎）。"
        "変更不要のフィールドは現在の値をそのまま返すこと。")


def plan_rework(cfg: "Config", t: Task, feedback: str) -> None:
    """実行前レビューの差し戻し: エージェント CLI にタスク定義を修正させて**再び proposed** で提案し直す。
    エージェント CLI 不在/失敗時は指摘を note に追記してそのまま再提案（人が approve/revise で確定できる）。"""
    reworked = False
    try:
        out = _run_agent_cli(_plan_rework_prompt(t, feedback), cfg.model, purpose="plan")
        obj = _extract_json_object_loose(out)
        if isinstance(obj, dict) and str(obj.get("title", "")).strip():
            t.title = str(obj["title"]).strip()
            t.verify = _strip_code(str(obj.get("verify", "") or "").strip())
            for k in ("accept", "after", "note", *TASK_GUIDE_KEYS):
                if k in TASK_GUIDE_KEYS and k not in obj:
                    continue                   # 誘導記述は「キーあり」だけ置換（旧応答でも既存値を消さない）
                v = str(obj.get(k, "") or "").strip().replace("\n", " ⏎ ")
                t.drop(k)
                if v:
                    t.extra.append((k, v))
            try:
                t.priority = int(obj.get("priority", t.priority) or 0)
            except (TypeError, ValueError):
                pass
            reworked = True
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"plan-review 差し戻しの修正に失敗（生のまま再提案）: {t.id}: {e}")
    if not reworked:                       # 修正できなくても指摘は失わない（note に残して人が確定）
        note = (t.get("note") or "").strip()
        t.drop("note")
        t.extra.append(("note", (note + " ⏎ " if note else "") + f"[差し戻し] {feedback}"))
    t.status = "proposed"
    persist_task(cfg, t)
    write_needs_file(cfg, t, f"差し戻しを反映して再提案（指摘: {feedback[:200]}）",
                     evidence=_task_definition_block(t), kind="plan-review")
    append_decision(cfg, t.id, cfg.actor, context=f"{t.id}（{t.title}）を差し戻しで修正",
                    action="plan-rework", reason=feedback[:200],
                    affects=f"{t.id} → proposed（再提案）", learn=(t.title, feedback))
    append_journal(cfg.journal, f"plan-review 差し戻し: {t.id} を修正して再提案")


# ---------------------------------------------------------------------------
# 依存の影響範囲（after 逆辺）と却下（reject）
# ---------------------------------------------------------------------------
def dependents_of(tasks: "list[Task]", tid: str, transitive: bool = True) -> "list[Task]":
    """tid に依存する（after に tid を含む）タスク。transitive で推移閉包（影響範囲の一覧提示用）。"""
    out: "list[Task]" = []
    seen = {tid}
    frontier = {tid}
    while frontier:
        nxt: set = set()
        for t in tasks:
            if t.id in seen:
                continue
            if any(d in frontier for d in task_deps(t)):
                out.append(t)
                seen.add(t.id)
                nxt.add(t.id)
        if not transitive:
            break
        frontier = nxt
    return out


def prerequisites_of(tasks: "list[Task]", tid: str, transitive: bool = True) -> "list[str]":
    """tid の前提（after 上流）の ID 一覧。backlog に無い ID（done/外部）も含めて返す。"""
    by_id = {t.id: t for t in tasks}
    out: "list[str]" = []
    seen = {tid}
    frontier = [tid]
    while frontier:
        cur = frontier.pop(0)
        t = by_id.get(cur)
        if t is None:
            continue
        for d in task_deps(t):
            if d not in seen:
                seen.add(d)
                out.append(d)
                if transitive:
                    frontier.append(d)
    return out


def cmd_impact(cfg: Config, tid: str, as_json: bool = False) -> int:
    """タスクの依存関係（前提／依存先・推移）を一覧表示する。変更・却下の影響範囲を人が辿る用。"""
    tasks = load_tasks(cfg.backlog)
    if not any(t.id == tid for t in tasks):
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    ups = prerequisites_of(tasks, tid)
    downs = dependents_of(tasks, tid)
    if as_json:
        print(json.dumps({"id": tid, "prerequisites": ups,
                          "dependents": [{"id": t.id, "title": t.title,
                                          "status": t.norm_status()} for t in downs]},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"=== impact: {tid} ===")
    print(f"前提（after 上流・推移）: {', '.join(ups) or '（なし）'}")
    if downs:
        print("依存先（このタスクの変更が影響する・推移）:")
        for t in downs:
            print(f"  - {t.id} [{t.norm_status()}]: {t.title}")
    else:
        print("依存先: （なし）")
    return 0


def _rejected_record(t: Task, reason: str) -> str:
    return (f"\n## 却下記録\n- 却下: {reason}\n- 却下時の状態: {t.norm_status()}\n"
            f"- 却下時刻: {_now_ts()}\n")


def cmd_reject(cfg: Config, tid: str, reason: str) -> int:
    """タスクの却下: 廃止（rejected として archive へ退避）し、依存先を proposed に戻して再審査に
    かける。実行前（proposed）にも成果物レビュー段（review）にも使える。再計画は要求しない
    （分解は人の明示操作）。理由は avoid（回避知識）として蓄積し、同種の再提案を予防リコールが
    弾くほか、次の分解時に backlog-planner へ「却下済み」として渡り、意図の似た再提案を抑える。"""
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    if t.norm_status() == "doing":
        print(f"エラー: {tid} は実行中（doing）です。先に revise で止めるか完了を待ってください。",
              file=sys.stderr)
        return 2
    # 委譲中も live work：先に flow を止めてから廃止する（放置＝二重書き込み）
    if t.norm_status() == "offloaded" or t.get("flow_run"):
        detach_flow_run(cfg, t, reason[:120] or "reject により委譲から切り離し")
        persist_task(cfg, t)
    release_claim(cfg, t)
    # 影響範囲（after 逆辺・推移）: 依存先は前提を失うため proposed に戻して人の再審査へ
    downs = dependents_of(tasks, tid)
    for d in downs:
        deps = [x for x in task_deps(d) if x != tid]
        d.drop("after")
        if deps:
            d.extra.append(("after", ", ".join(deps)))
        if d.norm_status() not in ("done", "doing"):
            d.status = "proposed"
            clear_needs_file(cfg, d.id)
            persist_task(cfg, d)
            write_needs_file(cfg, d, f"前提タスク {tid} が却下されたため再審査",
                             evidence=_task_definition_block(d), kind="plan-review")
        else:
            persist_task(cfg, d)
    close_task_mr(cfg, t, reason)   # タスク MR があればクローズ＋ブランチ削除（best-effort）
    # 本体を rejected として archive へ退避（納品ではないので DELIVERY には載せない）。
    # run ブリーフはここで退役させ、蓄積を archive 記録へ転記する（done の archive_task と同じ
    # 理屈——brief/ に残すと同じ task-id を再利用したとき古い内容が新タスクへ注入される）。
    t.status = "rejected"
    body = serialize_task(t) + _rejected_record(t, reason)
    brief = retire_brief(cfg, t)
    if brief:
        body += f"\n## run ブリーフ（却下時点の蓄積。learn 射影済み）\n{brief}\n"
    _archive_write(cfg, t.id, body)
    delete_task_file(cfg, t)
    clear_needs_file(cfg, tid)
    affected = ", ".join(d.id for d in downs) or "（なし）"
    # 墓標を残す（S6-4）。archive の rejected も `_existing_titles` に効くが、それだけだと
    # (a) プランナーは毎回同じものを出し続けて投入側で黙って落とされる（＝人には「再分解しても
    # 何も起きない」に見える）(b) 却下理由がプランナーに届かない (c) 人が手で墓標を足せない。
    # 人が直した題ではなく**原題も**残す——プランナーは原題で出し直してくるため。
    for title in dict.fromkeys(x for x in (t.title, str(t.get("planned_title") or "").strip()) if x):
        append_tombstone(cfg, title, reason, charter=(t.get("charter") or "").strip())
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を却下（廃止）",
                         action="reject", reason=reason,
                         affects=f"{tid} → rejected ／ 依存先を再審査へ: {affected}",
                         avoid=(t.title, reason) if cfg.learn_capture and reason else None)
    # 再計画は要求しない（分解は人の明示操作だけ、の契約）。かつては却下のたびに replan を
    # 自動発行して「穴を埋め直して」いたが、それが「消しても似たタスクが復活する」体験の
    # 直接の原因だった。却下済みは archive（rejected）と墓標に残り、次に人が分解を要求した
    # ときに backlog-planner への入力として渡る＝意図の似た再提案はそこで抑止される。
    append_journal(cfg.journal, f"reject: {tid} を却下（依存先 {len(downs)} 件を再審査へ）")
    print(f"{dr}: {tid} を却下しました。影響（依存先→再審査）: {affected}")
    return 0


def human_worklist(tasks: "list[Task]") -> "tuple[list[Task], list[Task], list[Task], list[Task]]":
    blocked = [t for t in tasks if t.norm_status() == "blocked"]
    intake = [t for t in tasks if t.norm_status() == "inbox" and not t.verify.strip()]
    review = [t for t in tasks if t.norm_status() == "review"]   # verify=PASS の承認待ち
    proposed = [t for t in tasks if t.norm_status() == "proposed"]   # 実行前レビュー待ち
    return blocked, intake, review, proposed


def render_digest(blocked, intake, reasons: dict, budget_stop: bool, review=None,
                  proposed=None) -> str:
    review = review or []
    proposed = proposed or []
    lines = ["# 要対応（agent-project）", ""]
    if budget_stop:
        lines += ["⚠ 予算切れで未消化のまま停止しました。", ""]
    if proposed:
        lines.append("## 実行前レビュー待ち（proposed・承認されるまで実行しません）")
        for t in proposed:
            lines.append(f"- {t.id}: {t.title}")
            lines.append(f"    対応: `agent-project approve {t.id}`（承認）／needs に修正指示を書いて差し戻し"
                         f"／`agent-project reject {t.id} --reason ...`（却下）")
        lines.append("")
    if review:
        lines.append("## 検収待ち（verify=PASS・承認で done 確定）")
        for t in review:
            lines.append(f"- {t.id}: {t.title}")
            lines.append(f"    成果: {t.get('gate_ref', '')}")
            lines.append(f"    対応: `agent-project approve {t.id}`（承認）／needs に方針を書いて差し戻し")
        lines.append("")
    if blocked:
        lines.append("## 判断待ち（blocked）")
        for t in blocked:
            why = reasons.get(t.id, "検証 NG / 判断不能")
            lines.append(f"- {t.id}: {t.title}\n    なぜ: {why}\n"
                         f"    対応: needs/{t.id}.md に方針を書く、または `approve {t.id}` / `hold {t.id}`")
    if intake:
        lines += ["", "## acceptance 未定義（need_intake）"]
        for t in intake:
            lines.append(f"- {t.id}: {t.title}\n    なぜ: verify 未定義 → verify を定義して ready 化")
    if not blocked and not intake and not review and not proposed:
        lines.append("（対応待ちなし）")
    return "\n".join(lines) + "\n"


def notify(cfg: "Config", tasks, reasons: dict, newly_blocked: set, budget_stop: bool) -> bool:
    """状態遷移時だけ stdout / notify-cmd へ要約を出す（案件毎の needs/<id>.md は別途書込済）。"""
    if not newly_blocked and not budget_stop:
        return False
    blocked, intake, review, proposed = human_worklist(tasks)
    digest = render_digest(blocked, intake, reasons, budget_stop, review, proposed)
    print("\n--- 通知（要対応）---\n" + digest, flush=True)
    if cfg.notify_cmd:
        try:
            subprocess.run(cfg.notify_cmd, shell=True, input=digest, text=True, encoding="utf-8", errors="replace",
                           cwd=str(cfg.workdir), timeout=60)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] notify-cmd 失敗: {e}", file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
