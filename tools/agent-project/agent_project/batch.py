from __future__ import annotations
# batch.py — 元 agent-project.py の 5063-5362 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# 正準ループ（run）
# ---------------------------------------------------------------------------
def summarize(tasks: "list[Task]") -> "dict[str, int]":
    c = {s: 0 for s in VALID_STATUS}
    for t in tasks:
        c[t.norm_status()] = c.get(t.norm_status(), 0) + 1
    return c


# journal のローテーション閾値（バイト。0 以下で無効）とアーカイブ保持世代数（0 以下で無制限）。
# build_config が設定 journal_max_bytes / journal_keep をここへ確定する（_AGENT_CLI と同じ流儀）。
_JOURNAL_MAX_BYTES: int = 262144
_JOURNAL_KEEP: int = 20


def _journal_lock_path(path: Path) -> str:
    h = hashlib.sha1(str(path).encode()).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), f"agent-project-journal-{h}.lock")


def rotate_journal(path: Path, max_bytes: "int | None" = None,
                   keep: "int | None" = None) -> "Path | None":
    """journal が閾値を超えていたら journal-archive/ へ退避し、新しい journal を始める。
    退避名はタイムスタンプ＋ホスト名で一意（複数ホストの direct 同期でも rename が衝突しない・
    退避ファイルは以後不変＝マージ衝突源にならない）。保持世代を超えた古いアーカイブは削除する。
    ローテーションしたら退避先を返す（しなければ None）。呼び出し側でロックを取ること。"""
    mx = _JOURNAL_MAX_BYTES if max_bytes is None else max_bytes
    if mx <= 0:
        return None
    try:
        if not path.is_file() or path.stat().st_size < mx:
            return None
    except OSError:
        return None
    arch_dir = path.parent / "journal-archive"
    try:
        arch_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        host = re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname())[:24] or "host"
        dest = arch_dir / f"{path.stem}-{ts}-{host}{path.suffix}"
        n = 1
        while dest.exists():
            dest = arch_dir / f"{path.stem}-{ts}-{host}.{n}{path.suffix}"
            n += 1
        path.replace(dest)                 # 同一ファイルシステム内の原子的 rename
    except OSError:
        return None
    keep_n = _JOURNAL_KEEP if keep is None else keep
    if keep_n > 0:
        try:
            arch = sorted(p for p in arch_dir.iterdir()
                          if p.is_file() and p.name.startswith(path.stem + "-"))
            for old in arch[:-keep_n]:
                old.unlink()
        except OSError:
            pass
    return dest


def append_journal(path: Path, line: str) -> None:
    ts = _now_ts()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 多重プロセス（daemon・外部 CLI・別 watch）の追記とローテーションをホスト内で直列化する
    with _file_lock(_journal_lock_path(path)):
        rotated = rotate_journal(path)
        with path.open("a", encoding="utf-8") as f:
            if rotated is not None:
                f.write(f"- {ts} journal をローテーション（→ journal-archive/{rotated.name}）\n")
            f.write(f"- {ts} {line}\n")


def append_runlog(path: "Path | None", record: dict) -> None:
    """構造化 run-log（JSONL）に1行追記。run 毎の機械可読な観測ログ（journal は人間可読、これは集計用）。"""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _block(cfg, task, reason, reasons, evidence: str = ""):
    # offloaded のまま blocked にすると、走っている flow が放置され reap も拾えない。
    if task.norm_status() == "offloaded" or task.get("flow_run"):
        detached = detach_flow_run(cfg, task, reason[:120] or "hold/block により委譲から切り離し")
        if detached:
            # canceled な同一 run-id を approve 後に作り直さない（cancel→ready と同じく retries を進める）
            task.retries += 1
    task.status = "blocked"
    reasons[task.id] = reason
    _remember_needs_reason(task, reason)  # 票を失っても ensure_needs が同じ理由で作り直せるように
    persist_task(cfg, task)
    # 失敗票でも検収画面が state worktree の内部差分へフォールバックしないよう、成功時と
    # 同じ run metadata 由来の delivery を添える。
    try:
        delivery = delivery_entries(cfg, task)
    except Exception:  # noqa: BLE001 — delivery 取得失敗で本来の blocked 遷移を壊さない
        delivery = None
    write_needs_file(cfg, task, reason, evidence=evidence, delivery=delivery)
    release_claim(cfg, task)              # blocked は doing でなくなる＝実行権（claim）を解放（人手 hold 含む）


def _revert_workdir(cfg) -> None:
    """回帰時の best-effort 巻き戻し: 追跡ファイルを HEAD に戻し未追跡を消す。
    **コミット済み/ push 済みの変更は対象外**（未コミットの作業ツリー変更のみ）。"""
    if not (cfg.workdir / ".git").exists():
        return
    for cmd in (["git", "-C", str(cfg.workdir), "checkout", "--", "."],
                ["git", "-C", str(cfg.workdir), "clean", "-fd"]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass


def _escalate(cfg, task, reason, reasons, cycle, evidence: str = ""):
    """ループ内で人の判断(needs)へ回す直前のフック。auto_adjudicate が有効なら、人へ送る前に
    エージェント CLI へ『自律的に積み直して解けるか』を諮り、可能なら needs を作らず ready に戻して回し続ける。
    verify を持たないタスク（acceptance 未定義）は対象外＝必ず人へ。adjudicate_max で有限回に制限。"""
    if cfg.auto_adjudicate and not cfg.dry_run and task.verify:
        done_n = int(task.get("adjudicated", "0") or "0")
        if done_n < cfg.adjudicate_max:
            decision, guide = adjudicate_escalation(cfg, task, reason)
            if decision == "requeue":
                task.drop("feedback", "adjudicated")
                if guide:
                    task.extra.append(("feedback", guide.replace("\n", " ⏎ ")))
                task.extra.append(("adjudicated", str(done_n + 1)))
                task.status = "ready"
                persist_task(cfg, task)
                append_decision(cfg, task.id, "auto",
                                context=f"{task.id}（{task.title}）を人の判断前に自律裁定",
                                action="auto-adjudicate",
                                reason=(f"エージェント CLI: requeue — {guide[:120]}" if guide
                                        else "エージェント CLI: requeue"),
                                affects=f"{task.id} → ready")
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} 自律裁定で積み直し"
                                            f"（人の判断を回避 {done_n + 1}/{cfg.adjudicate_max}）")
                return
    _block(cfg, task, reason, reasons, evidence=evidence)


# ---------------------------------------------------------------------------
# 並列消費（§11）— agent-flow の worker 並列へ寄せる。
#   prioritize が返す order は依存(after)解決済み＝互いに独立。daemon/remote へ submit する
#   タスクは実行が daemon 側の隔離ワーカで走るので、最大 concurrency 個まで並行 submit して
#   一括で待つ。verify と done/archive/decisions/派生など「ローカル状態の変更」は逐次のまま
#   （workdir/決定記録の競合を避け、不変条件をそのまま守る）。local act は逐次（並列化しない）。
# ---------------------------------------------------------------------------
def _submit_bound(location: str, cfg: "Config") -> bool:
    """その location が daemon/remote への submit（=隔離ワーカ実行）になるか。local 実行なら False。"""
    if location == "remote":
        return True
    if location == "daemon":
        return daemon_running(cfg, use_git=False)
    return False


def _select_batch(order: "list[Task]", cfg: "Config", policy, remaining: int) -> "list[Task]":
    """先頭から、並行 submit 可能（daemon/remote）なタスクを最大 width 個まとめる。
    先頭が local 実行なら従来どおり1件だけ（逐次）。残サイクル予算 remaining も超えない。"""
    width = cfg.concurrency if (cfg.concurrency > 1 and not cfg.once) else 1
    width = max(1, min(width, remaining))
    first_loc = decide_location(order[0], policy, cfg)
    if width == 1 or not _submit_bound(first_loc, cfg):
        return [order[0]]
    batch = []
    for t in order:
        if len(batch) >= width:
            break
        if not _submit_bound(decide_location(t, policy, cfg), cfg):
            break                      # local 実行が混ざったらそこで切る（逐次に落とす）
        batch.append(t)
    return batch or [order[0]]


# --- 原子的クレーム: 同一 backlog を複数 worker/インスタンスが回しても二重実行しないための claim。---
#   <root>/claims/<id>.lock を O_CREAT|O_EXCL で作れた者だけが実行権を持つ。owner 失踪時のため TTL で奪取可。
def _claims_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "claims"


def _claim_ttl(cfg: "Config") -> float:
    # act_timeout=0（無制限待ち）なら claim も期限なし＝長時間委譲中に他インスタンスへ
    # 奪われて二重実行するのを防ぐ（owner が生きている限り握り続ける）。
    if cfg.act_timeout <= 0:
        return float("inf")
    return cfg.act_timeout + cfg.verify_timeout + 60.0   # act+verify を十分に上回る猶予（失踪検知用）


def claim_task(cfg: "Config", task: "Task") -> bool:
    """task の実行権を原子的に取得できれば True。既に新鮮なクレームがあれば False（他者が実行中）。"""
    d = _claims_dir(cfg)
    p = d / f"{task.id}.lock"
    rec = json.dumps({"host": socket.gethostname(), "pid": os.getpid(),
                      "ts": time.time(), "id": task.id}).encode("utf-8")
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # 生死は _claim_alive に委ねる: 同一ホストなら pid の生死で即断する。TTL だけで見ると
        # kill / クラッシュで死んだ owner のロックが act_timeout+verify_timeout+60 秒（既定 41 分）
        # 居座り、その間そのタスクは「他者が実行中」と誤認されて誰にも拾われない。さらに
        # act_timeout<=0（無制限）では TTL が inf になり **永久に** 奪取できなくなる。
        if _claim_alive(cfg, task.id):
            return False                      # 実行者が生きている
        try:                                  # stale（owner 失踪）＝奪取を試みる
            p.unlink()
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except (FileExistsError, OSError):
            return False                      # 競合で他者が先取り
    except OSError as e:
        # フェイルクローズ: claim ファイルを作れない（権限・ディスクフル・共有違反等）のに
        # 「取得成功」と返すと、二重実行防止そのものが無効化される（複数インスタンスが同じ
        # タスクを同時に実行し、ブランチ push・backlog 書き込みが衝突する）。実行を見送り、
        # 原因を journal に残して次パスに委ねる（環境が直れば自然に再開する）。
        with contextlib.suppress(OSError):
            append_journal(cfg.journal, f"claim 不能のため {task.id} の実行を見送り: {e}")
        return False
    try:
        os.write(fd, rec)
    finally:
        os.close(fd)
    # クレーム後の再検証: 別インスタンスが既に消化（archive/削除）や状態変更をしていないか。
    # （ロック取得は「同時実行」を防ぐが、こちらの in-memory ビューが古い場合に二重実行を防ぐ）
    live = _load_task_file(cfg, task.id)
    # offloaded（非ブロッキング委譲・結果待ち）は reap が doing へ確定させる正当な遷移なので claim を許す。
    if live is None or (live.norm_status() not in CONSUMABLE and live.norm_status() != "offloaded"):
        release_claim(cfg, task)              # 既に done/review/blocked 等 → 実行しない
        return False
    # 実行直前のディスク内容を採用する（in-memory がパス開始時点で止まっていても、
    # 人の revise・直接編集をこの試行に反映し、doing 永続化で上書き消失させない）。
    live.drop("revised")                      # これから走る試行は最新内容を含む＝マーカー消化
    _adopt_task(task, live)
    return True


def release_claim(cfg: "Config", task: "Task") -> None:
    """実行権を解放する（done/review/blocked/積み直しのいずれでも、doing でなくなったら呼ぶ）。"""
    try:
        (_claims_dir(cfg) / f"{task.id}.lock").unlink()
    except OSError:
        pass


def _claim_alive(cfg: "Config", tid: str) -> bool:
    """その task を今も実行している者がいるか。

    同一ホストのクレームは pid の生死で即断する（TTL を待たずに済む＝再起動直後の取り残しを
    すぐ救える）。別ホストは生死を確かめられないので TTL に従う。"""
    p = _claims_dir(cfg) / f"{tid}.lock"
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False                                    # 票が無い/壊れている＝実行者はいない
    if str(rec.get("host", "")) == socket.gethostname():
        return _pid_alive(int(rec.get("pid", -1) or -1))
    return time.time() - float(rec.get("ts", 0) or 0) <= _claim_ttl(cfg)


def recover_stale_doing(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """実行者が失踪した doing を ready へ戻す（自己回復）。戻した ID を返す。

    agent-project が再起動・クラッシュ（あるいは stop）すると、実行中だったタスクは doing のまま
    残る。**doing は消化対象（CONSUMABLE = ready/todo）ではない**ので次のパスでも拾われず、
    claim ロックだけが残骸として残って永久に止まる — viewer には「実行中」と見えるのに何も
    進まない。実行していたプロセスがもういないなら、その試行は二度と結果を返さない。実行権を
    解放して ready へ戻し、次のパスで新しい試行として拾い直させる（retries は据え置き＝この
    取り残しは worker の失敗ではないので、人へ回すまでの猶予を削らない）。"""
    revived: "list[str]" = []
    for t in tasks:
        if t.norm_status() != "doing" or _claim_alive(cfg, t.id):
            continue
        release_claim(cfg, t)
        t.status = "ready"
        persist_task(cfg, t)
        append_journal(cfg.journal,
                       f"doing 回復: {t.id} を ready へ戻す（実行者が失踪＝結果は返らない）")
        revived.append(t.id)
    return revived


def _act_batch(batch: "list[Task]", cfg: "Config", act, policy) -> "dict[str, tuple[str, str]]":
    """batch のうち**クレームできたタスクだけ** doing にして act（2件以上は ThreadPool で並行）。
    返り値のキーはクレーム成功＝実際に実行したタスクのみ（取れなかったものは含めない）。"""
    claimed = [t for t in batch if claim_task(cfg, t)]   # 二重実行防止: 取れた者だけ進む
    for t in claimed:
        t.status = "doing"
        resolve_and_persist_workspace(cfg, t, policy)    # タスク→1つの書込先へルーティング（決定を md へ永続化）
        persist_task(cfg, t)
    locs = {t.id: decide_location(t, policy, cfg) for t in claimed}
    if cfg.dry_run:
        return {t.id: (locs[t.id], None, "(dry-run)", True) for t in claimed}
    if not claimed:
        return {}

    def _one(t):
        # act は (bool|_Pending, msg)。_Pending は「非ブロッキング submit 済み・未終端」＝offload。
        # bool は「act 自体の成否」。捨てると失敗 run でも verify=true で done になり得る。
        status, msg = act(t, cfg, locs[t.id])
        if isinstance(status, _Pending):
            return (locs[t.id], status, msg, None)
        return (locs[t.id], None, msg, bool(status))

    if len(claimed) == 1:
        return {claimed[0].id: _one(claimed[0])}
    results: "dict[str, tuple]" = {}
    with ThreadPoolExecutor(max_workers=len(claimed)) as ex:
        futs = {ex.submit(_one, t): t for t in claimed}
        for fut, t in futs.items():
            try:
                results[t.id] = fut.result()
            except Exception as e:     # noqa: BLE001 — act 失敗は verify=NG 相当として後段で扱う
                results[t.id] = (locs[t.id], None, f"act 失敗: {e}", False)
    return results



# ---------------------------------------------------------------------------
