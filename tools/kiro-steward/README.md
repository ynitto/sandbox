# kiro-steward

**Loop Engineering MVP** — `backlog.md` を優先順位付けし、最優先タスクを kiro-flow に実行させ、
**タスク自身の `verify` をローカルで実行して PASS したものだけ done に確定**し、NG なら積み直す。
これを backlog が尽きるか予算（サイクル数/実時間）が尽きるまで繰り返す制御層。
人の判断が要った時はそれを `DECISIONS.md` に残す。

> 規約は [`.github/instructions/kiro-steward.instructions.md`](../../.github/instructions/kiro-steward.instructions.md)、
> 設計は [`docs/designs/2026-06-16-kiro-steward-mvp-design.md`](../../docs/designs/2026-06-16-kiro-steward-mvp-design.md)。
> `kiro-` 接頭辞は実行を kiro-flow＝kiro-cli に委譲するため。

## 正準ループ（5点）

1. `backlog.md` を読み優先順位をつけ、最優先を kiro-flow に投げる。
2. 優先順位付けは原則 kiro-cli。`--planner stub` なら最古優先（FIFO）。人間は `policy.md` で上書きできる。
3. kiro-flow の結果を verify ゲートで検証。NG なら backlog に積み直す。
4. backlog が尽きるか予算が尽きるまで繰り返す。
5. ユーザーの判断は `DECISIONS.md` に保存する。

## 二層構成

| 層 | 担当 | 実体 |
|----|------|------|
| 外側（制御） | 優先順位付け / 検証ゲート / 積み直し / 収束 / 決定記録 | `kiro-steward` |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `kiro-flow run` |

done を**自己申告で確定させない**（verify の終了コード0のみが根拠）ことが MVP の存在意義。

## 依存

- `python3`（標準ライブラリのみ。pip 依存なし）
- `kiro-flow`（act の委譲先。PATH か `tools/kiro-flow/kiro-flow.py` を自動解決。`--dry-run` なら不要）
- `kiro-cli`（優先順位付けの既定。`--planner stub` なら不要）

## インストール

```bash
bash tools/kiro-steward/install.sh           # ~/.local/bin/kiro-steward
```

未インストールでも `python3 tools/kiro-steward/kiro-steward.py ...` で代用可。

## サブコマンド

| コマンド | 役割 |
|----------|------|
| `run` | 正準ループ（優先順位付け→実行→検証→積み直し→収束・通知） |
| `triage` | 優先順位付けのみ（inbox→ready 昇格・policy 適用）。順位を表示 |
| `needs` | 人の判断待ち（blocked / acceptance 未定義）を表示 |
| `approve <id> --reason …` | 判断待ちを修正承認して積み直し（決定記録） |
| `hold <id> --reason …` | `policy.md` に `deny` 追加し保留（決定記録） |
| `reprioritize <id> --pin\|--defer --reason …` | `policy.md` に `pin`/`defer` 追加（決定記録） |

## クイックスタート

```bash
cp tools/kiro-steward/backlog.md.example backlog.md
kiro-steward run --backlog backlog.md --executor kiro     # 自律消化

# kiro-cli が無い環境（プロトコル確認）
kiro-steward run --backlog backlog.md --planner stub --executor stub

# act を飛ばし verify だけで状態整合（棚卸し）
kiro-steward run --backlog backlog.md --dry-run
```

人の判断が要ったら:

```bash
kiro-steward needs --backlog backlog.md                  # 何が判断待ちか
kiro-steward approve T12 --reason "テスト側を修正" --backlog backlog.md
kiro-steward hold prod-deploy --reason "本番は手動" --backlog backlog.md
```

## 人間が触る3面

| ファイル | 役割 |
|----------|------|
| `backlog.md` | タスク本体（人が追加できる） |
| `policy.md` | 優先順位への上書きルール（`deny` / `pin` / `defer`。ID/タイトル部分一致） |
| `DECISIONS.md` | 人の判断・承認の決定記録（append-only） |

```yaml
# policy.md の例
deny:  prod        # "prod" を含むタスクは自動実行しない（人の判断待ち）
pin:   T3          # T3 を最優先
defer: cleanup     # "cleanup" を含むタスクは後回し
```

## 収束（必ず止まる）

| 停止理由 | 意味 | フラグ |
|----------|------|--------|
| `drained` | 消化可能タスクが尽きた | — |
| `budget` | 予算が尽きた（サイクル数 / 実時間） | `--max-cycles 20` / `--max-seconds 0` |

検証 NG は backlog に積み直して再挑戦。`--max-retries 2` を超えると人の判断（blocked）へ回す。

## 通知

人の判断待ちへの**遷移時だけ**、要対応ダイジェストを `NEEDS_YOU.md` と標準出力に出す
（毎サイクルでは鳴らさない）。`--notify-cmd '<cmd>'` で teams-use / outlook-use / issue-mailbox 等へパイプできる。

## 終了コード

| code | 意味 |
|------|------|
| 0 | `drained` かつ判断待ち無し（完走） |
| 1 | 判断待ち（blocked）あり |
| 2 | `budget` で停止 |

## テスト

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-steward/tests -v
```

優先順位付け（stub=最古 / policy 上書き）・検証ゲート・積み直し・収束・決定記録・通知 dedup を
kiro-flow 抜きで検証し、kiro-flow stub を 1 回叩く統合テストも含む（無ければ skip）。
