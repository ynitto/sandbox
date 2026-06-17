# kiro-marshal

**Loop Engineering MVP** — `backlog/`（案件毎ファイル）を優先順位付けし、最優先タスクを kiro-flow に
実行させ、**`verify` をローカルで実行して PASS したものだけ done に確定**（ファイル削除）、NG なら
積み直す。backlog が尽きるか予算が尽きるまで繰り返し、人の判断が要った分は案件毎の
`needs/<id>.md`（フィードバック欄つき）で差し出し、判断は `decisions/<id>.md` に残す。

> 規約は [`.github/instructions/kiro-marshal.instructions.md`](../../.github/instructions/kiro-marshal.instructions.md)、
> 設計は [`docs/designs/2026-06-16-kiro-marshal-mvp-design.md`](../../docs/designs/2026-06-16-kiro-marshal-mvp-design.md)。
> `kiro-` 接頭辞は実行を kiro-flow＝kiro-cli に委譲するため。

## 正準ループ（5点）

1. `backlog/<id>.md` を読み優先順位をつけ、最優先を kiro-flow に投げる。
2. 優先順位付けは原則 kiro-cli。`--planner stub` なら最古優先（FIFO）。人間は `policy.md` で上書きできる。
3. kiro-flow の結果を verify ゲートで検証。done はファイル削除、NG なら積み直す。
4. backlog が尽きるか予算が尽きるまで繰り返す（`--watch` なら尽きても監視を続ける）。
5. ユーザーの判断・フィードバックは案件毎 `decisions/<id>.md` に保存する。

## 二層構成

| 層 | 担当 | 実体 |
|----|------|------|
| 外側（制御） | 優先順位付け / 検証ゲート / 積み直し / 収束 / 決定記録 | `kiro-marshal` |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `kiro-flow run` |

done を**自己申告で確定させない**（verify の終了コード0のみが根拠）ことが MVP の存在意義。

## 依存

- `python3`（標準ライブラリのみ。pip 依存なし）
- `kiro-flow`（act の委譲先。PATH か `tools/kiro-flow/kiro-flow.py` を自動解決。`--dry-run` なら不要）
- `kiro-cli`（優先順位付けの既定。`--planner stub` なら不要）

## インストール

```bash
bash tools/kiro-marshal/install.sh           # ~/.local/bin/kiro-marshal
```

未インストールでも `python3 tools/kiro-marshal/kiro-marshal.py ...` で代用可。

## ファイル/ディレクトリ構成

```
backlog/<id>.md      タスク本体（案件毎・人が追加できる。done で削除される）
policy.md            優先順位・実行先の上書き（人だけが書く）
needs/<id>.md        判断待ちの通知＋フィードバック記入欄（人が記入→自動再開）
decisions/<id>.md    人の判断・承認・フィードバックの決定記録（append-only）
journal.md           機械のサイクルログ
```

## サブコマンド

| コマンド | 役割 |
|----------|------|
| `run` [`--watch`] | 正準ループ。`--watch` で終了条件後も常駐監視（idle はエージェント非起動） |
| `triage` | 優先順位付けのみ（inbox→ready 昇格・policy 適用）。順位を表示 |
| `needs` | 人の判断待ち（blocked / acceptance 未定義）を表示 |
| `approve <id> --reason …` | 判断待ちを修正承認して積み直し（決定記録） |
| `hold <id> --reason …` | `policy.md` に `deny` 追加し保留（決定記録） |
| `reprioritize <id> --pin\|--defer --reason …` | `policy.md` に `pin`/`defer` 追加（決定記録） |

## クイックスタート

```bash
mkdir backlog
cp tools/kiro-marshal/backlog.md.example backlog/T1.md   # 1タスク=1ファイル
kiro-marshal run --executor kiro                         # 自律消化（backlog/ を消化）

# 常駐: 新規タスク/フィードバックを監視して自動消化（idle 中はエージェントを起動しない）
kiro-marshal run --watch --poll 10 --executor kiro

# kiro-cli が無い環境（プロトコル確認）
kiro-marshal run --planner stub --executor stub
```

## 人の判断とフィードバック往復

タスクが判断待ち（blocked）になると `needs/<id>.md` が生成される。**そのファイルの
「## フィードバック」欄に方針を書いて保存**すると、次パス（`--watch` なら次 poll）で拾われ、
ブロック解除＋内容を次の実行に反映し、`decisions/<id>.md` に記録される。コマンドでも操作できる:

```bash
kiro-marshal needs                                  # 何が判断待ちか
kiro-marshal approve T12 --reason "テスト側を修正"
kiro-marshal hold prod-deploy --reason "本番は手動"
```

## policy.md（優先順位・実行先の上書き）

```yaml
deny:    prod      # "prod" を含むタスクは自動実行しない（人の判断待ち）
pin:     T3        # T3 を最優先
defer:   cleanup   # "cleanup" を含むタスクは後回し
offload: heavy     # "heavy" を含むタスクは分散環境へ移譲（--git-bus 設定時）
```

## 分散移譲（location）

`--git-bus <共有gitリポジトリ>` を設定し、`policy.md` に `offload: <パターン>` を書くと、一致した
タスクの実行を kiro-flow の `--git` 分散バス越しに**別環境へ移譲**する。それ以外は local 実行。

## 収束（必ず止まる）

| 停止理由 | 意味 | フラグ |
|----------|------|--------|
| `drained` | 消化可能タスクが尽きた | — |
| `budget` | 予算が尽きた（サイクル数 / 実時間） | `--max-cycles 20` / `--max-seconds 0` |

検証 NG は積み直して再挑戦。`--max-retries 2` を超えると人の判断（blocked）へ回す。
`--watch` の場合は終了条件後もプロセスは生存して backlog/ を監視する（**idle 中は kiro-cli/flow を
起動しない**＝エージェントは待機しない）。

**レーン減速（pace）**: `--pace <秒>` で1サイクルの下限間隔を設けてバーストを防ぐ。`--max-seconds`
を併用すると `max_seconds/max_cycles` のペースに均す。

## 通知

人の判断待ちへの**遷移時だけ**、要約を標準出力に出す（毎サイクルでは鳴らさない）。
案件毎の `needs/<id>.md` が永続的な対応窓口。`--notify-cmd '<cmd>'` で teams-use / outlook-use /
issue-mailbox 等へダイジェストをパイプできる。

## 終了コード（非 watch 時）

| code | 意味 |
|------|------|
| 0 | `drained` かつ判断待ち無し（完走） |
| 1 | 判断待ち（blocked）あり |
| 2 | `budget` で停止 |

## テスト

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-marshal/tests -v
```

優先順位付け・検証ゲート・積み直し・収束・location/pace・フィードバック往復・watch・案件毎の
決定記録を kiro-flow 抜きで検証し、kiro-flow stub を 1 回叩く統合テストも含む（無ければ skip）。
