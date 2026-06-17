# kiro-steward

**Loop Engineering MVP** — `backlog.md` に並んだタスクを 1 件ずつ拾い、kiro-flow に実行させ、
**タスク自身が持つ `verify` コマンドをローカルで実行して PASS したものだけを done に確定**する
バックログの制御層。人間がプロンプトを毎回投げ込まなくても、バックログが枯れるか停止条件に
達するまで回り続け、人の判断が要る時だけ手を止める。

> 規約は [`.github/instructions/kiro-steward.instructions.md`](../../.github/instructions/kiro-steward.instructions.md)、
> 設計は [`docs/designs/2026-06-16-kiro-steward-mvp-design.md`](../../docs/designs/2026-06-16-kiro-steward-mvp-design.md)。
>
> **実装状況**: 本 README は現状の **loop コア**（消化ループ・verify ゲート・停止条件）を記述する。
> 判断（triage / `policy.md` 上書き）・通知（`NEEDS_YOU.md`）・決定記録（`DECISIONS.md`）は
> 設計書に基づき順次追加する。`kiro-` 接頭辞は実行を kiro-flow＝kiro-cli に委譲するため。

## なぜ二層か

| 層 | 担当 | 実体 |
|----|------|------|
| 外側（制御） | backlog.md の状態管理 / 停止条件 / **真の verify ゲート** | `kiro-steward`（本ツール） |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `kiro-flow run` |

kiro-flow が「頭脳（実行）」を担い、kiro-steward が「バックログの状態・停止・真の合否判定」を担う。
done を**自己申告で確定させない**ことが MVP の存在意義。

## 依存

- `python3`（標準ライブラリのみ。pip 依存なし）
- `kiro-flow`（act の委譲先。PATH か `tools/kiro-flow/kiro-flow.py` を自動解決。`--dry-run` なら不要）

## インストール

```bash
bash tools/kiro-steward/install.sh           # ~/.local/bin/kiro-steward
bash tools/kiro-steward/install.sh --prefix /usr/local/bin
```

未インストールでも `python3 tools/kiro-steward/kiro-steward.py ...` で代用可。

## クイックスタート

```bash
cp tools/kiro-steward/backlog.md.example backlog.md   # 編集してタスクを並べる
kiro-steward --backlog backlog.md --executor kiro     # 自律消化（act を kiro-flow に委譲）
```

kiro-cli が無い環境ではプロトコルだけ確認できる:

```bash
kiro-steward --backlog backlog.md --executor stub --planner stub
```

`act` を飛ばし、verify だけで現状の backlog.md を整合させる（棚卸し・再開前点検）:

```bash
kiro-steward --backlog backlog.md --dry-run
```

## backlog.md フォーマット

```markdown
## T1: README に概要見出しを追加する
- status: todo            # inbox | ready | doing | done | blocked（現状コアは todo/done/blocked）
- source: human          # human | triage | followup（出自の系。省略時は human）
- verify: `grep -q "## 概要" README.md`   # 終了コード0をPASSとみなす。done確定の唯一の根拠
- retries: 0             # kiro-steward が自動更新
- note: 任意（保持される）
```

- `todo`/`ready` を上から順に消化。`done`/`blocked` は飛ばす。
- **verify を持たないタスクは done 不能 → 即 blocked**（自己申告 done の禁止）。

## ループ本体

```
while 停止条件に未到達:
    task = 次の todo を1件 claim       # State（backlog.md）
    if task 無し: → drained で終了
    task.status = doing
    act_via_kiro_flow(task)           # Act（kiro-flow に委譲。--dry-run では skip）
    ok = run_verify(task.verify)      # Verify gate（終了コード0だけが done の根拠）
    if ok: task.status = done
    else:  retries++ ; retries>K なら blocked / それ未満なら todo に戻す
    journal に1行追記                 # Memory（申し送り）
```

## 停止条件

| 理由 | フラグ / 既定 | 意味 |
|------|--------------|------|
| `drained` | — | 消化対象が尽きた（実質完了） |
| `max_cycles` | `--max-cycles 20` | 外側ループのサイクル上限 |
| `no_progress` | `--no-progress 3` | `done` が N サイクル増えない（停滞） |
| `blocked_ratio` | `--blocked-ratio 0.5` | `blocked` 比率がこれ以上 |
| `budget` | `--max-seconds 0`（無制限） | 実時間予算超過 |

タスク単位は `--max-retries 2` を超えると `blocked`。

## 終了コード

| code | 意味 |
|------|------|
| 0 | `drained` かつ `blocked` 無し（完走） |
| 1 | `blocked` タスクあり（人間の判断が必要） |
| 2 | ガード（max_cycles 等）で停止 |

CI のステップに組める。停止後は `blocked`/`todo` の残タスクと停止理由を標準出力に出す。

## 主なオプション

| フラグ | 既定 | 説明 |
|--------|------|------|
| `--backlog` | `backlog.md` | バックログファイル |
| `--journal` | `journal.md` | 申し送りログ（追記） |
| `--workdir` | `.` | verify / act の作業ディレクトリ |
| `--bus` | `.kiro-steward-bus` | kiro-flow のバス |
| `--executor` | `kiro` | `kiro` / `stub` |
| `--planner` | `flow-planner` | `flow-planner` / `kiro` / `stub` |
| `--max-iterations` | 3 | kiro-flow 内側の再計画上限 |
| `--dry-run` | off | act を飛ばし verify のみ |
| `--once` | off | 1 タスクだけ処理して終了 |

## テスト

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-steward/tests -v
```

状態機械・停止条件・verify ゲートを kiro-flow 抜きで検証し、kiro-flow stub を 1 回叩く
統合テストも含む（kiro-flow が無ければ skip）。
