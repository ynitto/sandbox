# kiro-autonomous

**Loop Engineering MVP** — `backlog/`（案件毎ファイル）を優先順位付けし、最優先タスクを kiro-flow に
実行させ、**`verify` をローカルで実行して PASS したものだけ done に確定**（archive/ へ退避）、NG なら
積み直す。backlog が尽きるか予算が尽きるまで繰り返し、人の判断が要った分は案件毎の
`needs/<id>.md`（フィードバック欄つき）で差し出し、判断は `decisions/<id>.md` に残す。

> 規約は [`.github/instructions/kiro-autonomous.instructions.md`](../../.github/instructions/kiro-autonomous.instructions.md)、
> 設計は [`docs/designs/2026-06-16-kiro-autonomous-mvp-design.md`](../../docs/designs/2026-06-16-kiro-autonomous-mvp-design.md)。
> `kiro-` 接頭辞は実行を kiro-flow＝kiro-cli に委譲するため。

## 正準ループ（5点）

1. `backlog/<id>.md` を読み優先順位をつけ、最優先を kiro-flow に投げる。
2. 優先順位付けは `--planner kiro`（エージェントが外部 `priority` も加味）/ `none`（priority 降順→最古）。人間は `policy.md` で上書きできる。
3. kiro-flow の結果を verify ゲートで検証。done は `archive/` へ退避、NG なら積み直す。
4. backlog が尽きるか予算が尽きるまで繰り返す（`--watch` なら尽きても監視を続ける）。
5. ユーザーの判断・フィードバックは案件毎 `decisions/<id>.md` に保存する。

## 二層構成

| 層 | 担当 | 実体 |
|----|------|------|
| 外側（制御） | 優先順位付け / 検証ゲート / 積み直し / 収束 / 決定記録 | `kiro-autonomous` |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `kiro-flow run` |

done を**自己申告で確定させない**（verify の終了コード0のみが根拠）ことが MVP の存在意義。

## 依存

- `python3`（標準ライブラリのみ。pip 依存なし）
- `kiro-flow`（act の委譲先。PATH か `tools/kiro-flow/kiro-flow.py` を自動解決。`--dry-run` なら不要）
- `kiro-cli`（`--planner kiro`＝既定の優先順位付け／実行 executor=kiro 用。`--planner none` なら順位付けには不要）

## インストール

```bash
bash tools/kiro-autonomous/install.sh           # ~/.local/bin/kiro-autonomous
```

未インストールでも `python3 tools/kiro-autonomous/kiro-autonomous.py ...` で代用可。

## ファイル/ディレクトリ構成

すべて **cwd の `./.kiro-autonomous/` 配下に集約**される（`--root` で変更可。各パスは `--backlog` 等で個別上書きも可）。

```
.kiro-autonomous/
  backlog/<id>.md      タスク本体（案件毎・人が追加できる。done で archive/ へ退避）
  archive/<id>.md      完了タスクの保全先（done で backlog から移動）
  policy.md            優先順位・実行先の上書き（人だけが書く）
  needs/<id>.md        判断待ちの通知＋フィードバック記入欄（人が記入→自動再開）
  decisions/<id>.md    人の判断・承認・フィードバックの決定記録（append-only）
  journal.md           機械のサイクルログ
  bus/                 kiro-flow バス（一時。run 後に自動クリーンアップ。--no-cleanup で保持）
```

## kiro-flow への委譲（`--location` で local / daemon / remote）

「どこで・どう動かすか」は `--location`（既定 `auto`）に集約：

| location | 委譲方法 | daemon | 用途 |
|----------|---------|--------|------|
| `local` | `kiro-flow run`（単発・同期） | 不要 | 既定の実体 |
| `daemon` | `kiro-flow submit` → `result` で done 待ち | ローカル daemon（無ければ local にフォールバック） | warm worker 再利用 |
| `remote` | `submit`（`--git`）→ `result` で done 待ち | 共有 git バスの remote daemon が必須 | 別マシンへオフロード |

`auto` は「offload 一致＋`--git-bus` → remote ／ ローカル daemon 稼働 → daemon ／ 他 → local」。
daemon 検知は kiro-flow と同じロック（`flock`）。逐次処理では **local（run）で十分＝daemon 不要**。

```bash
# 既定（local: 単発 run）
kiro-autonomous run --executor kiro

# warm worker を再利用したいなら daemon を立てて submit 経路に
kiro-flow --bus .kiro-autonomous-bus daemon &
kiro-autonomous run --location daemon --executor kiro
```

## サブコマンド

| コマンド | 役割 |
|----------|------|
| `run` [`--watch`] | 正準ループ。`--watch` で終了条件後も常駐監視（idle はエージェント非起動） |
| `triage` | 優先順位付けのみ（inbox→ready 昇格・policy 適用）。順位を表示 |
| `needs` | 人の判断待ち（blocked / acceptance 未定義）を表示 |
| `rot` [`--fix`] | 古い/重複/実行不能タスクを検出して報告（`--fix` で人の判断へ回す） |
| `approve <id> --reason …` | 判断待ちを修正承認して積み直し（決定記録） |
| `hold <id> --reason …` | `policy.md` に `deny` 追加し保留（決定記録） |
| `reprioritize <id> --pin\|--defer --reason …` | `policy.md` に `pin`/`defer` 追加（決定記録） |

## クイックスタート

```bash
mkdir backlog
cp tools/kiro-autonomous/backlog.md.example backlog/T1.md   # 1タスク=1ファイル
kiro-autonomous run --executor kiro                         # 自律消化（backlog/ を消化）

# 常駐: 新規タスク/フィードバックを監視して自動消化（idle 中はエージェントを起動しない）
kiro-autonomous run --watch --poll 10 --executor kiro

# 優先度＋古さで決定的に（kiro-cli 不要）。kiro-flow も stub に
kiro-autonomous run --planner none --flow-planner stub --executor stub
```

`backlog/<id>.md` に `- priority: N`（大きいほど高優先）を書くと外部から順序を制御できる。
`--planner none` は priority 降順→同値は最古、`--planner kiro`（既定）はエージェントが priority も加味する。

## 人の判断とフィードバック往復

タスクが判断待ち（blocked）になると `needs/<id>.md` が生成される。**そのファイルの
「## フィードバック」欄に方針を書いて保存**すると、次パス（`--watch` なら次 poll）で拾われ、
ブロック解除＋内容を次の実行に反映し、`decisions/<id>.md` に記録される。コマンドでも操作できる:

```bash
kiro-autonomous needs                                  # 何が判断待ちか
kiro-autonomous approve T12 --reason "テスト側を修正"
kiro-autonomous hold prod-deploy --reason "本番は手動"
```

## DR 学習（通知を減らす）

`feedback`/`approve` の決定記録には `- learn: <タイトル> :: <指示>` が残る。タスクが繰り返し NG で
人へ回りそうになると、他案件の `learn` から**タイトルが十分似た過去の指示**（Jaccard ≥ `--learn-threshold`、
既定 0.5）を探し、見つかれば **blocked にせず**その指示を反映して自動的に再実行する（`auto-resolve` を
決定記録に残し通知を抑制）。自動適用は **1 タスク 1 回**まで。`--no-learn` で無効化。

## rot 検知（バックログの掃除）

古い/重複/実行不能タスクを検出して**人の判断へ回す**（消さず棚卸し）:

```bash
kiro-autonomous rot           # 検出して報告（unverifiable / duplicate / stale）
kiro-autonomous rot --fix     # 検出した rot を blocked にして needs/ へ
kiro-autonomous run --rot     # 毎 run の triage に組み込む（--rot-age-days で stale しきい値）
```

## policy.md（優先順位・実行先の上書き）

```yaml
deny:    prod      # "prod" を含むタスクは自動実行しない（人の判断待ち）
pin:     T3        # T3 を最優先
defer:   cleanup   # "cleanup" を含むタスクは後回し
offload: heavy     # "heavy" を含むタスクは分散環境へ移譲（--git-bus 設定時）
```

## 分散移譲（remote）

`--git-bus <共有gitリポジトリ>` を設定し、`policy.md` に `offload: <パターン>` を書くと、一致した
タスクは `--location` が `remote` に解決され、kiro-flow の `--git` 分散バス越しに別マシンの daemon へ
**submit してオフロード**する（その run の完了を待ってから verify）。それ以外は local 実行。

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
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-autonomous/tests -v
```

優先順位付け・検証ゲート・積み直し・収束・location/pace・フィードバック往復・watch・案件毎の
決定記録を kiro-flow 抜きで検証し、kiro-flow stub を 1 回叩く統合テストも含む（無ければ skip）。
