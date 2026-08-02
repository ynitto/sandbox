# agent-tools ファミリー バグ調査レポート（2026-08-02）

- 状態: 調査完了。修正済み項目と最新の残件優先順は §7 / §8 を参照
- 対象: `tools/{agent-tools/agentcore, agent-loop, agent-flow, agent-project, agent-amigos, agent-board}`、
  `schemas/`、`docs/designs/agent-*.md`
  （`.github/instructions/agent-tools.instructions.md` の `applyTo` 範囲）
- 基準リビジョン: `main` = `766f097`（PR #653 / #654 / #655 取り込み後）＋ PR #652
- 関連文書: [`2026-08-02-agent-tools-audit-findings.md`](../plans/2026-08-02-agent-tools-audit-findings.md)
  （agentcore 横断監査。本書はその**範囲外**——agent-loop / agent-flow / agent-amigos /
  設計書・スキーマ全体——を主に埋める。重複部分は §6 で対応関係を示す）

---

## 1. 結論

ファミリー全体を横断して調査し、実行時に誤動作する不具合と、設計書・スキーマの矛盾を
洗い出した。既存の agentcore 監査（PR #653）は品質が高く未修正項目も正直に文書化されて
いるが、**監査範囲が agentcore 中心**だったため、agent-loop / agent-flow / agent-amigos の
実装バグと、設計書間の矛盾はほぼ手つかずで残っている。

重大度の内訳:

| 区分 | 件数 | 代表例 |
| --- | --- | --- |
| Critical | 1 | agent-loop が tmux 外の標準手順で起動できない |
| Major（実装） | 12 | 非組み込み `agent_cli` で agent-flow の子プロセスが全滅、base-sync の偽 `done` |
| Major（設計・スキーマ） | 9 | `reply_to` の非互換定義、作業ゲート文書の誤参照、板 state enum の欠落 |
| Minor | 24 | 片側だけの正規化、dead config、doc 齟齬 |

柱への効き方: Critical と Major（実装）の大半は柱 1（チーム分担の正しさ——claim / 板 /
転送 / 委譲の終端）に、設計・スキーマの矛盾は柱 2（人が介在する前に機械が fail-close
すること）に触る。

---

## 2. 調査方法

1. 各ツールの全ソース・`*.yaml.example`・README・テストを読む
2. 設計書（`docs/designs/agent-*.md`・関連 `docs/plans/`）を読み、実装と突き合わせる
3. スキーマ 15 本を相互および生成・消費コードと突き合わせる
4. 各テストスイートを実行し、赤があれば実装とテストのどちらが正かを commit 履歴で確定する
5. 高確度の所見は再現コードを書いて確認する（本書で「再現確認済み」と記したもの）

報告は「コードを引用でき、失敗シナリオを具体的に書けるもの」に限った。スタイル指摘・
型注釈の欠落・仮定的な hardening は含めない。

---

## 3. テスト実行状況（基準リビジョン時点）

| ツール | 結果 |
| --- | --- |
| agentcore | 213 passed |
| agent-loop | 36 passed |
| agent-flow | 677 passed |
| agent-project | 1155 passed / 2 failed（※1） |
| agent-amigos | 180 passed / 1 failed（※1） |

※1 いずれも pytest 実行系に PyYAML が無いことによる**環境依存**で、system python の
`unittest` では通る。コードの不具合ではない。ただし「PyYAML 非依存を謳う（JSON
フォールバックがある）ツールのテストが PyYAML 必須」という点は、CI 構成として整理の余地がある。

**構造的な注意**: 複数ツールを一括で `pytest tools/` すると、各ツールの `tests/_shared.py`
が同名でトップレベルにあるため相互に上書きされ、23 ファイルが collection error になる。
検証はツール単位で実行する必要がある。

---

## 4. 実装バグ

### 4.1 Critical

#### C1. agent-loop は tmux 外から起動すると即クラッシュする（修正済み）

- 位置: `tools/agent-loop/agent_loop/interactive.py:331`
- `script_path = Path(__file__).resolve()` を再 exec 対象にするが、フラグメントは
  `agent_loop/__init__.py` の名前空間へ exec 合成されるため `__file__` は `__init__.py` を指す。
  これを直接実行すると `__main__.__spec__` が None になり、`__init__.py:32` の
  `pkgutil.get_data` が `ValueError` を投げて `main()` に到達しない。
- 失敗シナリオ: README:48 / README:147（タスクスケジューラ用法）が案内する
  `python3 agent-loop.py` を素の端末から実行 → tmux へ exec → コントローラペインが即死 →
  セッションごと終了。tmux 内で起動するか `--no-auto-attach` を付けたときしか動かない。
  zipapp インストール（`install.sh:206`）でも `__file__` が zip 内を指すため同様に壊れる。

### 4.2 Major — agent-loop

| ID | 位置 | 内容 |
| --- | --- | --- |
| L1（修正済み） | `config.py:129` | JSONC の末尾カンマ除去 `re.sub(r"(\s*[}\]]),", r"\1", s)` が**逆**。`}, {` の区切りカンマを消して正常な JSON を壊し、末尾カンマは消せない。コメント付き settings.json に prompt が 2 件以上あると必ず `[]` へフォールバックする |
| L2 | `config.py:195-197` / `config.py:8` | 保存先は `.agents/agent-loop.yml` 固定、読込は `.yaml` 優先。`.yaml` 運用のワークスペースでは `prompt-add` が再起動で消え、`prompt-remove` した prompt が復活する |
| L3（修正済み） | `hooks/gitlab-issue-hook.py:144` / `scheduler.py:536-543` | hook がイベントを先に seen 化してから 1 件だけ返し、スロット不足時は破棄。設計（`agent-loop-event-hook-design.md:26`）の「次サイクルへ持ち越す」が守られず**イベントが恒久消失**。同時変更 N 件のうち N−1 件も同様 |
| L4 | `inbox.py:92-95` | メッセージごとに一意 `prompt_id` で tmux ペインを作るが破棄経路が無い。さらに `restart_if_dead`（`interactive.py:272`）が死んだ使い捨てペインを蘇生し続ける |
| L5 | `session.py:33-35` | `startup_timeout` / `response_timeout` / `echo_output` は格納されるだけでどこからも読まれない **dead config**。実際の待ちは `_head.py:151` の `_SEND_STARTUP_TIMEOUT = 60` 固定。README:280-287 は「タイムアウトが起きたら `response_timeout: 600` に」と案内している |
| L6 | README 全般 | `--config` / `--no-daemon` は argparse に存在せず（README:147 等の手順が exit 2）、対話コマンド `add/remove/default/attach/list/save` は未実装、設定探索の「cwd → HOME」は実際には `~/.agents` のみ、PID ロック `/tmp/agent-loop-<hash>.pid` は存在しない |

### 4.3 Major — agent-flow

#### FL1. 非組み込みの `agent_cli` を設定すると子プロセスが全滅する（修正済み）

- 位置: `cli.py:57`（`choices=["kiro","claude","copilot","codex"]`）、`run.py:27-28`
- `_child_base` が子へ `--agent-cli <name>` を渡すため、`agent-flow.yaml.example:73-74` が
  公式に案内するプラグイン CLI（cursor / ollama / hermes）を設定すると、orchestrator と
  全 worker が `invalid choice` で exit 2 する。親は正常に解決できるので、症状は
  「orchestrator が非終端のまま終了」になり原因が見えにくい。
- 再現確認済み。`tests/test_agent_cli.py:307` は `--agent-cli claude`（組み込み）しか
  通しておらず、この経路は未カバー。

#### FL2. `data.ok is False → failed` が verify ゲートにも適用され、継続ルールが二重発火する（修正済み）

- 位置: `work.py:180-186`、`waits.py:277-291`、`continuation.py:185-211`
- `_normalize_verify` は失敗した verify ゲートに必ず `{"ok": False}` を付けるため、
  設計（`agent-flow-design.md:236`）が「通常 work node」に限定した規則が verify にも命中する。
  結果、`kind == "verify" and "fail" in output` のルール 2 と `status == "failed"` の
  ルール 3 が同時に発火する。
- 再現確認済み: 1 回の verify 失敗で `gen1-r1` / `verify1-r1` / `verify1r` の 3 タスクが生まれ、
  最後の 1 つは deps 空・`replaces` 消費済みの**孤児 verify ノード**として残る。
  `executor=agent` なら失敗ごとに無駄な LLM 呼び出し、`executor=gitlab` なら人が読む
  GitLab イシューが 1 件増える。

#### FL3. workspace clone を作れないと base-sync が偽の `done` になる（修正済み）

- 位置: `workspace.py:171-173`、`work.py:158-174`、`workspace.py:226-228`
- `provision_tree` 失敗 → `clone: ""` → `sync_workspace_base` が `{"status":"noop"}` を返し、
  base-sync ノードは「target … は統合済み」として `done` 記録される。fetch も ancestry 検査も
  実行されない。base-sync 設計 §4.2 step 8 は「検査成功時だけ commit・push と node `done`」、
  §6 は「fetch 失敗 → `failed`, `error_class=transient`」と規定している。
- 失敗シナリオ: `branch != target` の run で clone provisioning 中に瞬断 → base-sync が黙って
  `done` → 下流ノードが未統合ブランチ上で走り、plan v1 の run では誰も気付かないまま
  「成功」として納品される。

### 4.4 Major — agent-amigos / agent-board

#### AM1. `poll_board` が落札しても `inflight` を加算せず `max_concurrent` を超過する（修正済み）

- 位置: `agent_amigos/board.py:289-291`
- コメントは「この 1 巡で落札するたびに +1 する」と契約を明記しているのに、ループ本体
  （292-344）に `inflight += 1` が無い。兄弟実装 `agent_flow/board.py:372` は正しく加算する。
- 失敗シナリオ: `budget.max_concurrent: 1` のノードが、板に 3 件の eligible な
  `workload=amigos` 公示があると、1 回の `poll_board()` で 3 件すべて落札・dispatch する
  （各反復が古い `inflight=0` で判定するため）。`board.schema.json` の
  「超過時は新規入札を控える」契約違反。

#### AM2. メッセージカーソルが遅延 push / 時計ずれで回答を恒久的に取りこぼす（修正済み）

- 位置: `agent_amigos/messages.py:69-74`、ULID 生成は `util.py:35-39`
- カーソルを既読集合の**最大 ULID** へ進めるが、ULID はプロセスローカルの実時刻由来で
  グローバル単調ではない。GitBus は push 遅延を明示的に許容している（`gitbus.py:153`）。
- 失敗シナリオ: ノード B の `answer`（ULID t1）が push リトライ中に、ノード C の `info`
  （t2 > t1）が先に届いてカーソルが t1 を追い越す。後から B の回答が届いても
  `m["id"] > cursor` が偽になり `fresh` に入らない → `open_questions` が閉じず、回答が板上に
  あるのに `question_timeout` でオーナーへ誤エスカレーションする。回復経路は無い。

#### AB1. agent-board README が `result.json` の書き手について自己矛盾している（修正済み）

- 位置: `tools/agent-board/README.md:11`（「依頼側 — dashboard / CLI が書く」）と
  同 README:42-44（「落札ノード自身が直接書く」）
- スキーマ（`board.schema.json:106`）と両実装（`agent_amigos/board.py:189-227`、
  `agent_flow/board.py:129`）は後者。dashboard アダプタは読むだけで、書くのは
  `award.json` / `cancelled.json` のみ（`board-adapter.js:54-66`）。
- 失敗シナリオ: 役割表の行に従って依頼側ツールを実装すると、`result.json` は終端・冪等性
  マーカー（`agent_amigos/board.py:201, 296`）なので、走行中の委譲を偽終端させ、受託側の
  本当の報告を塞ぎ、ミッション継続中に `max_concurrent` の枠だけ解放してしまう。

### 4.5 Minor（抜粋）

| ID | 位置 | 内容 |
| --- | --- | --- |
| m1 | `agentcore/repolocal.py:57-67` | `.git` 除去が小文字化より先なので `.GIT` / `.Git` を吸収できず、`same_repo` が偽になる。該当ノードは板に永久に入札しない |
| m2 | `agentcore/agentcli.py:205` | `load_cli` のキャッシュキーに cwd を含めないが、`project_dir=None` のとき探索先は `Path.cwd()/agents` を含む。長寿命プロセスが `chdir` すると別プロジェクトの定義を返す |
| m3 | `agentcore/tests/` | `__init__.py` が無く、ルートからの `unittest discover` が 213 件中 87 件しか走らない（エラーは出ない）。内側テストの docstring が案内する `tools/agentcore/...` は存在しないパス |
| m4 | `agentcore/tests/test_transport.py:415` | `unittest.main()` が `TestBackoffSeam` の定義より前にあり、直接実行だと回帰ガード 2 件が黙ってスキップされる |
| m5 | `agent-flow/cleanup.py:15-16` | 掃除対象が旧 `$TMPDIR/agent-flow-locks`。実際の生成先は agentcore の `agentcore-claim-locks`（`protocol.py:110`）なので、`cleanup` は永遠に `locks=0` を報告し実ロックは溜まり続ける |
| m6 | `agent-flow/workspace.py:257-277` | target ancestry 検査が push リトライ**前**にあり、競合時の `git rebase` が base-sync のマージコミットを潰しても再検査しないまま push する |
| m7 | `agent-flow/work.py:160-170` | base-sync の競合解消を `executor` 任せにするため、`gitlab` executor だとリモート作業者が触れないローカル worktree の手順書が発行される。`defer_waits` 経路では worktree を消したうえで `_finish_wait` が step 7 の検査を飛ばして `done` を書く |
| m8 | `agent-flow/patterns.py:346`, `doctor.py:243`, `executors/gitlab.py:202` | スキル探索先に現行ホーム `~/.agents/skills` が無い。新ホームのみの環境で planner が黙って劣化する |
| m9 | `agent-flow/work.py:118`, `waits.py:182-193` | `max_open_issues` が run スコープ集計。8 run 並走で上限 × 8 まで発行され、`yaml.example:262` が謳うペーシングが成立しない |
| m10 | `agent-amigos/agent-amigos.py:7-8` | 存在しない `tools/agentcore` を `sys.path` に挿入（コメントも誤り）。動いているのは `agent_amigos/__init__.py:17-21` が正しいパスを入れているため |
| m11 | `agent-amigos/cli.py:376-391` | `collect` は README:302 と help が「オーナー限定」と書くが `_require_owner` を呼ばない |
| m12 | `agent-amigos/mission.py:541-542` | 設計書 §4.1 は `failed` の 2 条件とも「まだ誰も手番を取っていないミッションに限る」とするが、予算枯渇は定義上その後にしか起きない（実装が正・doc が誤り） |

---

## 5. 設計・スキーマの矛盾

### 5.1 Major

| ID | 双方の位置 | 内容 |
| --- | --- | --- |
| D1（修正済み） | `.github/instructions/agent-tools.instructions.md:8-13` ↔ `agent-tools-concept.md:223,289` | 作業ゲート文書が正典の「§7 このリポジトリでの強制」「原則 C1〜C7」を参照するが、実際は**強制が §8、原則は C8 まで**（§7 はモジュール別方針の表）。C8（知識共有クロージャ）がレビュー対象から抜ける。正典 §8 自身がこの文書へゲートを委譲しているだけに影響が大きい |
| D2（修正済み） | `kiro-loop-agent-messaging-design.md:101` ↔ `agent-loop-agent-messaging-design.md:108-112` | `reply_to` が「返信先エージェント名」と「メッセージ ID・フォールバックしない」で非互換。実装も `kiro-loop.py:3853`（`reply_to_id or from_agent`）と `sendcmd.py:501`（`reply_to_id or None`）で分裂。**同一の `~/.kiro/agents/<name>/inbox/` を共有**し、rename 設計が kiro-loop 残置を明言しているため現役の相互運用バグ。kiro-loop 設計は §4 と §5.2/§6 で自己矛盾もしている |
| D3 | `agent-dashboard-design.md:278` / `tmux.js:89` ↔ `agent-tools-rename-design.md:39` | dashboard が読む loop-state は `~/.kiro` と `~/.agent` のみで、agent-loop の現行ホーム `~/.agents/loop-state` を読まない。標準インストール環境では定期実行が dashboard から不可視 |
| D4 | `agent-tools-concept.md:264` ↔ `agent-project-design.md:206` | agent-project の停止理由が正典で「5 つ」、設計書で「6 つ」。正典 §0 の「矛盾したら作業を止める」に該当し、しかも C7 の実例として引かれている |
| D5（修正済み） | `2026-05-11-agent-loop-oneshot-design.md:180` ↔ 同 `:530` | 「デーモンは tmux 外」と「デーモンは常に tmux 内」を両方規定。アタッチ機構（`switch-client` か `attach-session` か）が決まらず実装不能 |
| S1（修正済み） | `board.schema.json:93` ↔ `agent_amigos/board.py:218`, `agent_flow/board.py:153` | `status.state` の enum に `cancelled` が無いのに両エンジンが書く（`vocab.TERMINAL` に含まれる） |
| S2（修正済み） | `board-adapter.js:108` ↔ `board.schema.json:111` | `result.status === 'cancelled'` を `done` に写像（`'failed'` 以外は全部 done）。中止した委譲が完了として計上される。agent-project 側（`flow.py:703-725`）は正しく ok=False |
| S3（修正済み） | `board-adapter.js:98-102` ↔ `delegation.schema.json:259` | 入札状態が**逆転**（award 確定前が `lost`、確定後の非落札が `applied`）。`amigos-adapter.js:117` は正しい。既存テストは winner/expired しか見ておらず未検出 |
| S4（修正済み） | `delegation.schema.json`（post 分岐） ↔ `flow.py:828` / `agent_flow/board.py:363` | 委譲 post に `verification_plan` が無い。コードは書いて読む必須フィールドで、スキーマ準拠の第三実装は receipt を返せず、タスクが永久に `done` にならない |

### 5.2 Minor（抜粋）

- `schemas/README.md` が `agent-cli.schema.json` を 1 行も記載していない（14 件表に非掲載）
- `task.schema.json` の status enum に `todo` が無いが、`model.py:21` と `CONSUMABLE` は受理する
- `delegation.schema.json` の reject は `feedback` 任意、`contract.js:118` は必須
- `mission.schema.json` の `budget` / `convergence` が `additionalProperties: false` だが、
  正典バリデータ（`mission.py:256,261`）は未知キーを通す
- `delivery.schema.json:14` が acceptance 値 `codd-gate` を文書化しているが、
  `mission.py:253-255` は `manual` / `agent` 以外を拒否するので発生し得ない
- `board.schema.json` / `delegation.schema.json` は repos 照合を `(url, path, base)` の
  同一性と説明するが、`agentcore/board.py:73-109` は名前と正規化 URL しか見ない
- `agent-tools-rename-design.md` が「旧系統は削除する」（§1/§4）と「kiro-loop は残置」
  （§3/§6）を両方規定し、どちらが勝つかの基準が無い
- `~/.kiro/agents/` を「CLI 定義の探索先」「メッセージング名前空間」「kiro-cli の
  agent 設定」の 3 者が競合所有し、衝突時の規則が未定義
- `agent-loop-gitlab-webhook-design.md:187` と `:189` が hook 例外時の応答を 200 と 500 で
  併記（実装は 200）
- `agent-amigos-design.md:231-237` の状態遷移図に `failed` 終端が無いが、同 §4.1 の本文は
  3 終端を定義している

---

## 6. 既存 agentcore 監査（PR #653）との対応

| 本書の所見 | PR #653 での扱い |
| --- | --- |
| `board` の `contract_version` fail-open | **修正済み**（F6）。ただし NaN / Inf で例外になる取りこぼしを本 PR で追修正（§7 G2） |
| `protocol.renew_lease` の壊れた `lease_until` | **修正済み**（F2）。同じ性質の `winner` 側は残存 → 本 PR で追修正（§7 G3） |
| `transport.ensure_clone` の salvage 欠落 | 未修正。監査文書 §3.1 U2 / U3 として認識・記録済み |
| `repolocal` の `.GIT` 大文字 | 未修正（U5 は path 小文字化の別論点） |
| agent-flow の一時ファイル残骸 | 掃除側のみ修正（F16）。除外側は残存 → 本 PR で追修正（§7 G4） |
| agent-loop / agent-flow / agent-amigos の実装バグ、設計書・スキーマの矛盾 | **監査範囲外**（agentcore 中心のため）。本書 §4.2〜§5 が該当 |

---

## 7. 修正状況

修正済み項目を実装・契約単位で集約する。実装変更には修正前に失敗する回帰テストを追加した。

| ID | 位置 | 修正 |
| --- | --- | --- |
| G1 | `agentcore/transport.py` | subdir 未作成の初回 `sync_push` が pathspec エラーで RuntimeError になっていた。ステージ対象が作業ツリーにも index にも無いときだけ no-op（`_scope_absent`）。削除だけのパスは従来どおり push する |
| G2 | `agentcore/board.py` | `contract_version` が `NaN` / `Infinity` だと `int()` の例外が `eligible()` を貫通し、入札巡回ごと停止していた。読めない値として不参加へ倒す |
| G3 | `agentcore/protocol.py` | `winner()` 側の `_as_float` が `ts` 欠落・`null` を 0.0 と読み、壊れた claim が恒久的に勝っていた。`NaN` も決定性を壊すため無視する |
| G4 | `agent-flow/stategit.py` | 同期除外が `.tmp` 末尾のみで、実生成名 `<name>.tmp.<pid>[.<unique>]` の残骸を共有状態リポジトリへ push していた |
| C1 / L1 | agent-loop 起動・設定読込 | 元 entrypoint の再実行と JSONC 末尾カンマ除去を修正した |
| L3 | agent-loop event hook | 返却した更新だけを既読化し、既存の外部イベントキューで session / slot / 送信失敗を再試行するようにした |
| FL1 / AM1 / AB1 | agent-flow / agent-amigos / agent-board | plugin CLI の子プロセス引継ぎ、同一 poll 内の同時実行上限、README の result 所有者を修正した |
| D1 / S1〜S4 | 作業ゲート・schema・dashboard | 正典参照、cancelled 語彙、入札表示、verification plan 契約を一致させた |
| FL2 | `agent-flow/work.py`, `continuation.py` | verify の実行完了と判定不合格を分離し、継続分岐を排他的にした |
| FL3 | `agent-flow/workspace.py`, `work.py` | workspace 準備を fail-close し、base-sync の conflict 解消も制御層内へ限定した |
| AM2 | `agent-amigos/messages.py`, `runner.py` | ULID 大小カーソルを既読 ID 集合へ移行した |
| D2 | `kiro-loop.py`, messaging 設計 | `reply_to` を返信元メッセージ ID または `null` に統一した |
| D5 | agent-loop oneshot 設計 | controller は既定 tmux 内、oneshot は detached・自動画面切替なしに統一した |

G1 は `state_git_subdir` 運用（バスが毎パス `sync_push` を呼ぶ）で初回パスが必ず止まるため
影響が最も大きい。詳細と教訓は
[`2026-08-02-agent-tools-audit-findings.md` §2b](../plans/2026-08-02-agent-tools-audit-findings.md) に記録した。

---

## 8. 推奨する次の一手

優先順に:

1. **L2（設定保存先の `.yaml` / `.yml` 不一致）** — UI で追加・削除した prompt が再起動で
   消失・復活するため、読込に採用したパスへ保存する
2. **L4（inbox の使い捨て pane リーク）** — 長期運転で pane が増え続け、終了済み pane まで
   自動再起動されるため、配送完了時の破棄と restart 対象外化を同じライフサイクルで直す
3. **D3（dashboard が現行 loop-state を見ない）** — `~/.agents/loop-state` を探索対象へ加え、
   標準インストールの定期実行を可視化する
4. **L5（timeout / echo_output が dead config）** — 設定値を実処理へ配線し、README の案内を実効化する
5. **D4 / L6（文書・CLI 契約の矛盾）** — 実装を正として正典の停止理由数と README の未実装
   オプション・コマンドを整理する

C1 / L1 / L3 / FL1〜FL3 / AM1〜AM2 / AB1 / D1〜D2 / D5 / S1〜S4 は修正済み。
