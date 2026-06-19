# kiro-autonomous

**Loop Engineering MVP** — `backlog/`（案件毎ファイル）を優先順位付けし、最優先タスクを kiro-flow に
実行させ、**`verify` をローカルで実行して PASS したものだけ done に確定**（archive/ へ退避）、NG なら
積み直す。backlog が尽きるか予算が尽きるまで繰り返し、人の判断が要った分は案件毎の
`needs/<id>.md`（フィードバック欄つき）で差し出し、判断は `decisions/<id>.md` に残す。

> タスク書式（backlog/<id>.md）の規約は [`backlog.md.example`](backlog.md.example)、
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
  decisions/<id>.md    人の判断・承認・フィードバックの決定記録（learn＝学習材料。append-only）
                       └ --ltm 時、実績ある learn は ltm-use home へ昇格（横断再利用）
  archive/<id>.md      ↑ done の保全先。検収用の「納品書」付き（backlog と1:1）
  DELIVERY.md          納品一覧（受領書）。done を1行ずつ追記
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
| （省略） | **`run --watch` と同義**。常駐監視で起動し backlog 投入を待ち続ける（PC 起動時の常駐用） |
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

## 常駐起動（PC 起動時から待ち受ける）

サブコマンドを**省略して呼ぶと `run --watch` と同義**になり、常駐監視で起動して backlog 投入を待ち続ける。
PC 起動時に立ち上げっぱなしにしておき、`backlog/<id>.md` を置くだけで自動消化させる使い方を一級にしている。

```bash
kiro-autonomous                       # = run --watch（常駐。backlog 投入を待つ）
kiro-autonomous --poll 10             # フラグだけ渡しても常駐（run の各フラグはそのまま効く）
kiro-autonomous run                   # 明示 run は従来どおり単発（drained/budget で終了）
```

idle 中はエージェント（kiro-cli/flow）を起動しないので、待機中の常駐は安価。停止は `Ctrl-C` か SIGTERM。
`--root` は cwd 相対なので、**常駐は backlog を置きたい作業ディレクトリで起動**する（または `--root /abs/path`）。

### OS の自動起動に登録する

**Linux（systemd ユーザーユニット）** — `~/.config/systemd/user/kiro-autonomous.service`:

```ini
[Unit]
Description=kiro-autonomous（backlog を待ち受ける常駐ループ）

[Service]
WorkingDirectory=%h/work               # backlog を置く作業ディレクトリ
ExecStart=%h/.local/bin/kiro-autonomous --poll 10 --executor kiro
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now kiro-autonomous     # 今すぐ起動＋ログイン時に自動起動
loginctl enable-linger "$USER"                    # ログアウト後も常駐させたい場合
journalctl --user -u kiro-autonomous -f           # ログ追従
```

**macOS（launchd）** — `~/Library/LaunchAgents/local.kiro-autonomous.plist` に
`ProgramArguments=[kiro-autonomous の絶対パス, --poll, 10, --executor, kiro]`、`WorkingDirectory`、
`RunAtLoad=true`、`KeepAlive=true` を設定して `launchctl load` する。

**Windows** — タスク スケジューラで「ログオン時」トリガに
`python C:\path\to\kiro-autonomous.py --poll 10 --executor kiro`（`開始（作業フォルダ）` に backlog ディレクトリ）を登録する。

## 人の判断とフィードバック往復

タスクが判断待ち（blocked）になると `needs/<id>.md` が生成される。**「## フィードバック」欄に方針を
書き、`- [ ] 確定` を `- [x]` にして保存**すると、次パス（`--watch` なら次 poll）で拾われ、ブロック
解除＋内容を次の実行に反映し、`decisions/<id>.md` に記録される。

**書きかけでの誤発火を防ぐ仕組み**（途中保存しても発動しない）:
- **チェックボックス**: `[x]` にした時だけ確定（明示シグナル）。
- **draft 状態**: 新規タスクは `status: draft` にしておくと消化対象外（書き終えたら `ready` に）。
- **debounce**: `--watch` 中は最終保存から `--debounce`（既定 3 秒）経過するまで待つ。

コマンドでも操作できる:

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

### ltm-use への学習昇格（プロジェクト横断・エージェント不要）

`decisions/` の学習は**その作業ディレクトリ内**だけで効く。`--ltm` を付けると、これを
`ltm-use`（セッション横断の長期記憶）へ**昇格**し、別プロジェクトからも再利用できる。すべて
**決定的なファイル操作**で完結し、LLM／エージェントは一切起動しない:

- **昇格の根拠は実績**: ある `learn` ルールが `auto-resolve` で実際に効いた**回数**が
  `--promote-threshold`（既定 2）以上になったら昇格。`ltm-use` の home
  （`<ltm-home>/memory/home/memories/kiro-autonomous/`）へ frontmatter 付き Markdown を書く。
- **横断 recall**: 学習照合は「ローカル `decisions/` → ヒット無しなら **ltm-use home**」の順に
  フォールバック（同じ Jaccard 照合）。別リポジトリで同種の詰まりが起きると過去の指示を再利用する。
- **冪等・グレースフル**: 昇格済みは出典 DR に `- promoted:` マーカを残し二重昇格しない。
  `--ltm` 無し（既定）や home 未解決なら**何もしない**（home の外へ書かないのが既定）。

```bash
kiro-autonomous run  --ltm                 # run 末尾で実績のある学習を自動昇格＋横断 recall
kiro-autonomous promote                    # 昇格だけ手動実行（明示操作なので常に有効）
#   --ltm-home PATH   ストアのルート（既定 $KIRO_LTM_HOME → ~/.claude）
#   --promote-threshold N   昇格に要する実績回数（既定 2）
```

## 納品書（成果物の検収）

タスク完了時に、検収用のサマリーを2段で残す（人の検品向け。`backlog` と対になる）:
- **個票**: `archive/<id>.md` に「## 納品書」を付す（verify=PASS・**成果参照**・完了時刻）。
- **一覧（受領書）**: `DELIVERY.md` に1行追記（id・タイトル・検収・成果参照・完了）。

**成果参照**は決定的に取得：act 出力の **PR/MR URL** → **commit SHA** → workdir の `git log -1` の順。
成果物が kiro-flow 経由で各リポジトリへ push される前提で、その PR/コミットへ辿れる。

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
