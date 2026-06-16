# task-loop — Loop Engineering MVP 設計書

> 作成日: 2026-06-16
> 対象ブランチ: `claude/determined-cray-dthvbi`
> 関連ファイル: `tools/task-loop/task-loop.py`, `tools/task-loop/tests/test_task_loop.py`,
> `tools/task-loop/README.md`, `tools/task-loop/queue.md.example`,
> `.github/instructions/task-loop.instructions.md`, `tools/kiro-flow/`

---

## 1. 概要

task-loop は、Loop Engineering（「プロンプトを書く人」をやめ「プロンプトを出し続ける
ループ＝システム」を設計する）の **MVP**。仕様駆動（SDD）の枠組みを意図的に外し、
ループとして閉じるのに**必須な要素だけ**を残した。

> **MVP の定義**: 人間が毎サイクル介入せずに、エージェントが
> 「仕事を1つ拾う → やる → 検証 → 状態を更新 → 続けるか止めるか判断」を
> 回し続ける最小の閉ループ。

`queue.md` に並んだタスクを 1 件ずつ拾い、実行を `kiro-flow` に委譲し、
**タスク自身の `verify` コマンドをローカルで実行して PASS したものだけを done に確定**する。
キューが枯れるか停止条件に達するまで自律的に回り、停止時は人間にエスカレーションする。

```
                 ┌──────────────── task-loop（外側ループ）────────────────┐
                 │  queue.md（State）  journal.md（Memory）                │
   起動 ───────▶ │   while 停止条件に未到達:                                │
                 │     1) 次の todo を1件 claim                            │
                 │     2) act ──────────────┐                             │
                 │     3) verify ゲート       │   ┌──────────────────────┐  │
                 │     4) done/blocked 更新   └─▶ │ kiro-flow run（内側）  │  │
                 │     5) journal 追記            │  分解→act→内側verify   │  │
                 │   停止理由を報告 ◀──────────── └──────────────────────┘  │
                 └────────────────────────────────────────────────────────┘
```

---

## 2. 背景・目的

このリポジトリは Loop Engineering の 6 プリミティブ（Scheduling / Worktrees / Skills /
MCP / Sub-agents / Memory・State）を満たす部品をすでに大量に持つ（`kiro-loop`,
`kiro-flow`, `ltm-use`, `council-system`, `statemachine-use` 等）。一方で
**「部品はあるが、自律ループとして閉じていない」**——人間がトリガを引き、合否を見て、
次を指示する手作業が残っていた。

task-loop はこの隙間を埋める。新しい巨大な機構ではなく、**既存の `kiro-flow` を実行系として
再利用し、その上に「キューの状態管理・停止条件・真の合否判定」という薄い外側ループを
被せる**ことを狙う。

| 要件 | 実現方法 |
|------|---------|
| 自己申告 done を防ぐ | done 確定の根拠を `verify` コマンドの終了コード 0 **のみ**に限定 |
| 暴走・予算溶かしを防ぐ | 4 系統の停止条件（max_cycles / no_progress / blocked_ratio / budget） |
| サイクル間で状態を引き継ぐ | `queue.md`（State）と `journal.md`（申し送り）をファイルで永続化 |
| 実行の頭脳を作り直さない | act を `kiro-flow run`（loop-until-done パターン）に委譲 |
| kiro-cli 無しでも検証可能 | `--executor stub`（kiro-flow stub）／`--dry-run`（verify のみ） |

### スコープ外（MVP に入れない）

SDD 成果物連携（requirements/design/tasks）、オブザーバビリティ、council 合議停止、
ドリフト自己修復、マルチノード分散、メタ自己改善。**ループが 1 本回ってから**乗せる。

---

## 3. 二層アーキテクチャ

| 層 | 担当 | 実体 |
|----|------|------|
| 外側ループ | queue.md の状態管理 / 停止条件 / **真の verify ゲート** | `task-loop.py` |
| 内側実行 | タスクの分解 → act → 内側 verify ループ | `kiro-flow run` |

役割分離の肝: **「何をやるか（queue）」と「終わったか（verify）」を外側が握り、
「どうやるか（act）」を内側に委ねる。** 内側 kiro-flow が成果を返しても、
done を確定するのは外側が独立に走らせる `verify` だけ。これにより内側 LLM の
過信 done が外側ゲートで物理的に止まる。

### act の委譲（kiro-flow 連携）

`act_via_kiro_flow()` はタスクから要求文を組み立て、同期実行する:

```
kiro-flow --bus <bus> run "<request>" --planner <p> --executor <e> --max-iterations <m>
```

要求文（`build_request`）には**完了条件として `verify` コマンドを明示**し、
「完了条件を満たすまで反復（loop-until-done）」を促す。これは kiro-flow の
パターン選択（`loop`/`反復`/`通るまで` 等のキーワードで loop-until-done を選ぶ）に
乗るための誘導。kiro-flow 実行体は `--kiro-flow` > `PATH` > リポジトリ同梱
`tools/kiro-flow/kiro-flow.py` の順で解決する。

---

## 4. データモデル

### queue.md（State）

```markdown
## <id>: <タイトル>
- status: todo | doing | done | blocked
- verify: `終了コード0をPASSとみなすシェルコマンド`
- retries: 0
- note: 任意（保持される）
```

- パーサ（`parse_queue`）は `## <id>: <title>` を見出し、直後の `- key: value` を
  メタデータとして読む。`verify` のバッククォートは除去してそのまま実行可能にする。
- `status`/`verify`/`retries` 以外のフィールド（`note` 等）は `extra` に**順序保持**で
  退避し、`serialize_queue` で書き戻す（正準形へ寄せつつ自由記述を失わない）。
- 見出しより前のプレアンブル（タイトル・コメント）はそのまま保持。

### journal.md（Memory／申し送り）

各サイクルの結果（DONE / FAIL retry / BLOCKED）と開始・停止イベントを 1 行ずつ追記。
次サイクル・次セッションが読む短期ワーキングメモリ。価値ある申し送りは将来 `ltm-use`
へ promote する余地を残す（MVP ではファイル追記のみ）。

---

## 5. ループ本体（`run_loop`）

```
preamble, tasks = load_queue()
while True:
    reason = check_guards(...)         # 停止条件をサイクル先頭で評価
    if reason: break
    task = pick_next(tasks)            # 先頭の todo を1件
    if task is None: reason = drained; break
    task.status = doing; save_queue()
    act_ok = act(task)                 # dry-run では skip
    ok = run_verify(task.verify)       # ★ done 確定の唯一の根拠
    if ok:
        task.status = done
    else:
        task.retries += 1
        task.status = blocked if (verify無 or retries>max_retries) else todo
    save_queue(); append_journal()
    no_progress を done 件数の増分で更新
```

- **claim は「先頭の todo」**。MVP は単一プロセス前提なので分散 claim は持たない
  （必要になれば kiro-flow の名前空間 claim 機構へ寄せる）。
- **verify ゲート**（`run_verify`）はシェル実行し終了コード 0 を PASS とする。
  verify が空文字なら即 FAIL（=「自己申告では done にできない」を構造で表現）。
- **act は差し替え可能**（`run_loop(cfg, act=...)`）。テストはここに偽 act を注入して
  kiro-flow 抜きで状態機械を検証する。

---

## 6. 停止条件（収束ガード）

Loop Engineering 最大の事故＝無限ループ・予算溶かし・過信 done を、4 系統＋タスク単位で潰す。

| 停止理由 | 既定 | 判定 |
|----------|------|------|
| `drained` | — | `todo` が尽きた（実質完了） |
| `max_cycles` | 20 | 外側サイクル数の上限 |
| `no_progress` | 3 | `done` 件数が N サイクル連続で増えない |
| `blocked_ratio` | 0.5 | `blocked / 全タスク` がしきい値以上 |
| `budget` | 無制限 | 実時間（`--max-seconds`）超過 |

タスク単位では `retries > max_retries`（既定 2）で `todo → blocked`。
verify 未定義タスクは 1 回で即 `blocked`。

### 終了コード（CI 連携）

| code | 条件 |
|------|------|
| 0 | `drained` かつ `blocked` 無し（完走） |
| 1 | `blocked` あり（人間判断が必要） |
| 2 | ガード停止（max_cycles 等） |

---

## 7. 検証（テスト）

`tools/task-loop/tests/test_task_loop.py`（標準 `unittest`、11 ケース）:

- **パース／書き戻し**: フィールド抽出・バッククォート除去・`extra` 保持・round-trip 等価。
- **verify ゲート**: PASS/FAIL、空 verify=FAIL。
- **状態機械**: 全 PASS で `drained`／失敗で `retries` 超過 → `blocked`／verify 無で即 `blocked`。
- **停止条件**: `max_cycles` / `no_progress` / `blocked_ratio` の各ガード発火。
- **act 注入**: act が verify と独立に呼ばれ、成果物 → verify PASS → done。
- **kiro-flow 統合**: `--executor stub` で実際に kiro-flow を 1 回叩く端から端まで
  （kiro-flow が無ければ skip。`KIRO_FLOW_STUB_SLEEP_MAX=0` で高速化）。

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/task-loop/tests -v
# → Ran 11 tests OK
```

---

## 8. 成功基準（MVP の達成定義）

1. 人間がプロンプトを **0 回**追加投入して、`queue.md` のタスクが N 件 `done` になる。
2. **誤った done が verify ゲートで止まる**（自己申告では通らない）。
3. 暴走せず**有限回で必ず止まり**、停止理由と残タスクを報告する。

いずれも §7 のテストと CLI スモークで満たすことを確認済み。

---

## 9. 既存資産との接続と今後

- **接続**: 実行系は `kiro-flow`（loop-until-done / stub）。規約は
  `.github/instructions/task-loop.instructions.md`。常駐させるなら `kiro-loop`（tmux 定期送信）で
  `task-loop` を周期起動できる。
- **発展（スコープ外→次段）**:
  - `journal.md` の価値ある申し送りを `ltm-use` へ promote（サイクル間→セッション間記憶）。
  - 停止時のエスカレーションを `issue-mailbox` / `gitlab-idd` に流して人間/別ノードへ。
  - `verify` の自動生成・タスク分解を `decomposition` / `requirements-definer` と接続（SDD 再導入）。
  - 複数 todo の並列消化（kiro-flow の名前空間 claim を外側にも適用）。
