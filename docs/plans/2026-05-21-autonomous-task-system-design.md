# 自律タスク処理システム 設計書（kiro-loop × multi-agent-shogun-kiro）

> 作成日: 2026-05-21
> 対象ブランチ: `claude/autonomous-task-system-design-TUNpD`
> 関連: `tools/kiro-loop/`, `tools/multi-agent-shogun-kiro/`

---

## 1. 目的とスコープ

ひとつの **north_star**（北極星目標）を渡したら、人の介在なしに丸1日（最大24時間）
kiro-cli 群が自律的に分解・並列実行・統合・報告を繰り返す系を、既存部材の結合のみで構築する。

### 不変原則（最重要）

1. **`multi-agent-shogun-kiro` は外部 OSS** として扱い、その中身を変更しない
   - `tools/multi-agent-shogun-kiro/` 配下のファイルを編集しない・追加しない
2. **起動責務は OSS が持つ**
   - shogun / karo / ashigaru / gunshi の tmux ペイン作成、kiro-cli 起動、`@agent_id` 設定、`inbox_watcher` 起動は
     OSS 公式の `shutsujin_departure.sh`（および周辺スクリプト）が担う
   - **kiro-loop は kiro-cli を自前で起動しない**
3. **人 / kiro-loop からの指示は Shogun ペインへの「テキスト入力」として伝える**
   - `queue/*.yaml` を外部から read/write しない（`shogun_to_karo.yaml` は Shogun が書く）
4. **kiro-loop はセッション管理をしない**
   - `/chat new`, `/clear`, queue 操作などの内部状態リセットは Shogun OSS（Redo Protocol 等）に任せる
   - **死んだペインの再起動も kiro-loop はしない**（観測してログを出すだけ）
5. **スマホ / ntfy 入力は使わない**
6. **人介在なしに自律動作を優先**（人介入は Shogun ペインへの直接入力のみ、緊急時のみ）

### スコープ外

- 新しいエージェント枠組みの設計
- 新規ループスケジューラの実装
- Shogun OSS 側への機能追加・ファイル変更
- ntfy / 人による日中の能動介入

## 2. 結論（採用案）

**「kiro-loop を薄い外殻 + 外部ペイン参照モードに改良」**して、Shogun OSS とは完全に独立に共存させる。

```
kiro-loop の責務（改良後・最小）
  (1) 既存の外部ペイン（OSS が立てた shogun ペイン）に対してハートビート文をテキスト送信する
  (2) ペインの生死を観測し、死んでいたら送信をスキップしてログを出す（再起動はしない）
  (3) 将来: 外部入力（GitLab Issue）をテキスト本文として注入する
```

kiro-loop は **kiro-cli を一度も起動しない**。ペインも作らない。ただ「既にある外部ペインへ
時刻になったらテキストを送る薄いスケジューラ」になる。

### kiro-loop に必要な改良

現在の kiro-loop はワークスペースごとに kiro-cli を tmux で起動して所有する設計。
本ユースケース用に **「外部ペイン参照モード」** を追加する（詳細 §11）。

採用理由:
- Shogun OSS の起動・通信機構を一切壊さない
- kiro-loop の責務を「鼓動の注入」だけに絞れる
- 結合点が send-keys 1か所に閉じ、片方が壊れても他方は独立に動ける

## 3. 全体構成

```
            ┌────────────────────────────────────────────────────────┐
            │ ホスト (WSL)                                           │
            │                                                        │
            │  shutsujin_departure.sh (OSS 公式起動スクリプト)         │
            │    └─ 起動時に 1 回だけ実行                              │
            │         ├─ tmux session "multiagent" を作成              │
            │         ├─ shogun / karo / ashigaru1-7 / gunshi ペインを │
            │         │   生やし、kiro-cli chat --agent X を起動        │
            │         ├─ @agent_id を各ペインにセット                    │
            │         └─ inbox_watcher.sh を背後で起動                   │
            │                                                        │
            │  ─── ここまでが OSS 領域。kiro-loop は不可侵 ───         │
            │                                                        │
            │  kiro-loop daemon (薄い外殻・改良後)                     │
            │    ├─ external_panes: ["multiagent:shogun.0"] を参照のみ │
            │    ├─ periodic-scheduler ( 1s)  ハートビート注入         │
            │    └─ pane-watcher       (10s)  生死観測のみ・蘇生しない │
            │            │                                             │
            │            │ send-keys (テキストのみ)                    │
            │            ▼                                             │
            │   tmux: multiagent:shogun.0  (← OSS が立てたペイン)       │
            │      └─ kiro-cli chat --agent shogun                    │
            │            │ (以降は OSS 内ループ)                       │
            │            ▼                                             │
            │   karo / ashigaru / gunshi ペイン群                      │
            │      └─ queue/inbox, tasks, reports, dashboard.md       │
            │         (Shogun 階層が自分で読み書き)                     │
            └────────────────────────────────────────────────────────┘
```

kiro-loop と Shogun 群の唯一の接点は **既存の shogun ペインへの send-keys**。逆向きはなし。
kiro-loop が落ちても OSS の自律ループは inbox 駆動で回り続ける。OSS が落ちても kiro-loop は
「pane が見えない」とログを出して空回りするだけで、互いに無関係。

## 4. 部材と責務

| 部材 | 責務 | 触ってよい範囲 |
|---|---|---|
| `shutsujin_departure.sh` (OSS) | tmux 全ペイン作成、kiro-cli 起動、`@agent_id` セット、watcher 起動 | OSS の全範囲 |
| kiro-loop daemon (改良後) | shogun ペインの生死観測、ハートビート送信 | 設定された `external_panes` への send-keys のみ |
| Shogun (kiro-cli) | north_star に対する戦略判断、cmd 発令、状況把握 | OSS 内部の全機構（自前で完結） |
| Karo / Ashigaru / Gunshi | OSS 既存仕様どおり | （外部から触らない） |
| `queue/*.yaml`, `dashboard.md` | Shogun 階層の内部状態 | **外から read/write しない** |

### 守るべき不変条件（kiro-loop 側）

- kiro-loop は **kiro-cli プロセスを起動しない**（`agent:`, `--trust-all-tools` 等の起動オプションは外部ペインモードでは使わない）
- kiro-loop は **tmux session / window / pane を新規作成しない**
- kiro-loop は **`external_panes` で指定されたペインだけに send-keys する**
- kiro-loop は **`queue/*.yaml` を直接 read/write しない**
- kiro-loop は **`/chat new` / `/clear` を送らない**（Shogun が自分で打つのは自由）
- ペイン死亡を検知しても **再起動しない**（ログを出してスキップ）

## 5. 1日のタイムライン（自律モード）

人がやるのは **(a) `shutsujin_departure.sh` を起動 → (b) shogun ペインに north_star をテキスト入力**の 2 ステップのみ。

```
T=-1m   (人間) shutsujin_departure.sh を起動 → OSS 流儀でペインが立ち上がる
T=0     (人間) shogun ペインに north_star をテキストで入力（後述 §7）
        Shogun が cmd_001 を queue/shogun_to_karo.yaml に書き、Karo に inbox_write
T=0+    (人間) kiro-loop daemon を起動（external_panes 設定済み）
        以降、Karo/Ashigaru/Gunshi の既存ループが回る
T=0+45m (kiro-loop) ハートビート注入 #1
        「north_star に対する現状を確認し、停滞・完了があれば次の手を打て」
T=0+90m (kiro-loop) ハートビート注入 #2  … 以下 90分おき
T=0+4h  (kiro-loop) 深いレビュー注入
        「これまでの成果を summarize し、north_star に対する残課題を整理せよ」
T=24h   最終レビュー注入 → daemon は走り続けるが新規 cmd は Shogun 判断で止まる
```

時刻ベースの「朝・昼・夕」のような人間サイクルは使わない（人介在なし前提）。
**経過時間ベースのハートビート + 深いレビュー** の2系列で十分。

## 6. ハートビート注入プロンプト

### 6.1 配置と原則

`<host>/.kiro/kiro-loop.yml`（または `--config` 指定）に prompts として記述。
**`agent`, `--trust-all-tools` 等の kiro-cli 起動オプションは書かない**（OSS 起動分を上書きしないため）。

設計原則:
- **「次に何をするか」を kiro-loop が決めない**。Shogun に判断を委ねる。kiro-loop は「考えろ」と促すだけ
- **「手を止めよ」を毎回明示**する（Shogun が暴走して ashigaru に直接 send-keys する F002 違反を防ぐ）
- **「動きがあるなら無動作で終わってよい」を許す**（idempotent。ナッジが動きを乱さない）
- **`fresh_context: false` を全エントリで強制**。kiro-loop はリセットを発火しない（§8）

### 6.2 設定例（改良後の external_panes モード）

```yaml
# kiro-loop 改良後の設定スキーマ（§11 で詳細）

external_panes:
  - name: shogun
    tmux_target: "multiagent:shogun.0"
    # 何も起動しない。生死観測と send-keys のみ。

prompts:
  - name: "ハートビート"
    target: shogun                # external_panes 参照
    interval_minutes: 90
    fresh_context: false
    prompt: |
      dashboard.md と queue/shogun_to_karo.yaml の状況を確認せよ。
      - north_star に対して進捗が出ているなら、何もせず手を止めてよい
      - Karo が停滞しているなら原因を特定し、必要なら cmd を再発令せよ
      - 未完 cmd がなく north_star も未達なら、次の cmd を書いて Karo に発令せよ
      cmd の発令が終わったら、または手を止めると決めたら、その時点で発話を終えよ。
      ashigaru に直接話しかけてはならない（F002）。

  - name: "深いレビュー"
    target: shogun
    interval_minutes: 240
    fresh_context: false
    prompt: |
      これまでの戦果を summarize せよ。
      - 完了 cmd / 失敗 cmd / 停滞 cmd を列挙
      - north_star に対する残課題を再評価
      - 残り時間で達成可能なゴールに絞り、必要なら方針を修正
      修正方針は新しい cmd として発令、または既存 cmd の方針見直しを Karo に伝えよ。
      終わったら手を止めよ。
```

`tmux_target` は `<session>:<window>.<pane>` 形式。OSS が `shutsujin_departure.sh` で生成するペインを
そのまま参照する。実際の target 名は OSS 仕様に合わせる（要確認: `multiagent:shogun.0` か別か）。

### 6.3 やらないこと

- 「朝・夕・夜」のような時刻ベース注入は **行わない**（人間サイクルを暗黙に仮定するため）
- 「沈黙監視」のようなメタ監視も **行わない**（ハートビートが兼ねる）
- `/chat new` / `/clear` の注入は **行わない**（§8）

## 7. 大目標（north_star）の投入方法

### 7.1 起動時（人による初回投入）

人が shogun ペインに **テキストで直接入力**する。kiro-loop は介在しない。

```
（shogun ペインに直接タイプ）
本日の north_star は以下である。

  「<具体的な目標を1-2文で>」

これを達成するための cmd を queue/shogun_to_karo.yaml に書け。
acceptance_criteria はテスト可能な条件として列挙せよ。
cmd を書いたら Karo に inbox_write して、手を止めよ。
```

Shogun が自分で `shogun_to_karo.yaml` を書き、Karo に inbox_write する。
**kiro-loop は queue ファイルを書かない**。

### 7.2 将来：GitLab Issue 経由（§10 で詳細）

`gitlab-obsidian-sync` / `gl.py` で取得した Issue 内容を **kiro-loop の prompt として shogun ペインに注入する**。
Shogun がその内容を読んで cmd に変換する。kiro-loop は queue を直接触らない。

### 7.3 やらないこと

- ntfy / スマホ入力経路は **作らない**
- `issue-mailbox` 経由の自動 queue 注入は採用しない（OSS queue 外部書き込み禁止のため）
- 複数 north_star の同時並走は **当面サポートしない**（1 北極星 × 1日）

## 8. コンテキストリセット戦略

**結論：kiro-loop はリセットを一切発火しない**。Shogun OSS の既存 Redo Protocol（`clear_command` 型 inbox メッセージ）に任せる。

| 契機 | 動作 | 担当 |
|---|---|---|
| cmd を 1 件 done にした直後 | （Shogun が必要なら自分で `/chat new` を打つ） | Shogun |
| redo 発令時 | 既存 Redo Protocol が `clear_command` を ashigaru へ送る | Karo（OSS 既存仕様） |
| Shogun ペインのコンテキスト膨張 | Shogun の判断で `/chat new` を打つ | Shogun |

**why kiro-loop が触らないか**:
- 不変原則4（セッション管理しない）に従う
- 時間ベースで強制リセットすると長時間 cmd の途中で履歴を失う事故が起きる
- Shogun は判断と発令だけなので会話量が少なく、コンテキスト膨張は遅い

ハートビートプロンプトに「会話履歴が重いと感じたら `/chat new` を自分で打ってよい」と書く程度は許容（テキスト注入の範囲内）。

## 9. 失敗モードとリカバリ

| 失敗 | 検知 | リカバリ |
|---|---|---|
| WSL アイドルシャットダウン | Windows タスクスケジューラが5分毎に起動ラッパを呼ぶ | ラッパが `shutsujin_departure.sh` と `kiro-loop` の両方を冪等に呼ぶ |
| Shogun ペイン死亡 | kiro-loop pane-watcher が10秒で検知 | **kiro-loop は再起動しない**。ログを出して送信をスキップ。OSS 側の再起動（人 or 起動ラッパ）を待つ |
| Karo / Ashigaru の沈黙 | （Shogun OSS 側のエスカレーションに委ねる） | OSS 既存仕様（escalation: Escape×2 → context reset） |
| inbox_watcher の inotify 不発（WSL2） | 30秒タイムアウト fallback（既存） | OSS 既存仕様 |
| Shogun が cmd 発令を忘れる | 次のハートビート（最大90分後）で再判断 | プロンプトが「未完 cmd がなく north_star 未達なら新 cmd 発令」を明示 |
| north_star が達成不能 | （人が介在しない以上、検知できない） | 翌日の人レビュー時に dashboard 🚨要対応 で発見 |

### 起動ラッパ（人介在なし24h運用のキー）

WSL 再起動 / shogun ペイン死亡を **shogun OSS の再起動責務を保ったまま** 自律復旧するため、
薄い起動ラッパを 1 本書く（OSS 外、ホストレイヤー）：

```bash
# 例: ~/bin/autonomous-day-bootstrap.sh （本設計の成果物）
#!/usr/bin/env bash
set -euo pipefail

# 1. shogun ペインが既に存在するか確認
if ! tmux has-session -t multiagent 2>/dev/null; then
    # 存在しなければ OSS 公式起動を呼ぶ（kiro-loop は関与しない）
    bash /path/to/multi-agent-shogun-kiro/shutsujin_departure.sh
fi

# 2. kiro-loop daemon を起動（PID ロックで冪等）
python /path/to/kiro-loop/kiro-loop.py --config ~/kiro-loop-autonomous.yaml
```

Windows タスクスケジューラはこのラッパを 5 分間隔で呼ぶ。
**kiro-loop は OSS の起動を直接コードしない**。ラッパが両者を別々に呼ぶ。

最悪 24 時間進捗ゼロも許容する設計。ループが回り続けていれば dashboard に記録は残るので、
翌日の人レビューで状況把握できる。

## 10. 将来拡張：GitLab Issue 入力

### 10.1 ゴール

人が GitLab Issue を立てると、その内容が自動的に Shogun の判断材料として流れ込み、新しい cmd が生まれる。

### 10.2 設計

**実装方針: kiro-loop の prompt を介してテキスト注入のみで実現する**（queue を外から触らない）。

```yaml
prompts:
  - name: "GitLab Issue 取り込み"
    target: shogun
    interval_minutes: 60
    fresh_context: false
    prompt_command: |
      python ~/sandbox/tools/gitlab-obsidian-sync/sync.py \
        --list-open-issues --label autonomous-task --format=prompt
    # prompt_command の標準出力を prompt 本文として注入する（要 kiro-loop 改良）
    # 出力例:
    #   「以下の Issue が新規にオープンされている:
    #    - #123: foo を作る (label: autonomous-task)
    #    - #124: bar を修正
    #    north_star に整合するなら新しい cmd として取り込んで Karo に発令せよ。
    #    整合しないものは無視してよい。手を止めよ。」
```

**ポイント**:
- kiro-loop の `prompt_command` 機能（要追加）で動的テキスト生成
- Shogun は **テキストとしてだけ** Issue を受け取り、cmd 化の判断と書き込みは自分で行う
- GitLab 側への状態書き戻し（Issue クローズ等）は Shogun が ashigaru に指示するか、Issue 側で人が行う

### 10.3 スコープ外（将来も含めて）

- ntfy / スマホ入力は将来も実装しない
- `issue-mailbox` の自動 queue 注入は採用しない

## 11. kiro-loop 改良要件（本設計の実装本体）

OSS を一切触らないため、本設計の実装は **kiro-loop 側の機能追加**に集約される。

### 11.1 追加する機能

| ID | 機能 | 概要 |
|---|---|---|
| K1 | `external_panes` 設定セクション | 既存ペインを参照のみ。kiro-cli 起動しない |
| K2 | prompts の `target` フィールド | 既存の `workspace` と排他で、`external_panes.name` を参照 |
| K3 | external pane 用の dispatch パス | ensure_session / kiro-cli launch をスキップ。send-keys のみ |
| K4 | pane-watcher（観測のみモード） | 既存 session-monitor を分岐。external pane は「死んだらログ + スキップ」のみ |
| K5 | external pane の busy 観測 | `capture-pane` でプロンプト表示有無を確認、busy 中なら送信スキップ |
| K6 | `prompt_command` フィールド（将来） | 標準出力を prompt 本文として動的注入（§10 用） |

### 11.2 設定スキーマ（改良後）

```yaml
# kiro-loop.yaml （改良後）

external_panes:
  - name: shogun
    tmux_target: "multiagent:shogun.0"
    # オプション: 死亡時にログだけ出すかエラー終了するか
    on_dead: log_and_skip   # default

# 既存の workspaces はそのまま残す（後方互換）
workspaces: []

prompts:
  - name: "ハートビート"
    target: shogun                # K2: external_panes 参照
    # workspace: project-a         # 既存。target と排他
    interval_minutes: 90
    fresh_context: false           # external_panes では強制 false（リセット禁止）
    prompt: |
      ...
```

### 11.3 実装方針

- `tools/kiro-loop/kiro-loop.py` の `PeriodicScheduler` に分岐を入れる
  - prompt エントリの `target` が `external_panes` を指す場合 → 既存 `ensure_session` を呼ばず、
    `tmux_target` 直接の send-keys 経路へ
- `SessionManager._dispatch_prompt()` を流用するが、kiro-cli 起動と session 作成パスは通らない
- `SlotMonitor` は流用可能（pane_id ベースで busy 観測しているため）
- `session-monitor` は external pane に対して **再起動を試みない** 分岐を追加
  - 検知時: ログ + `prompts` のスキップカウンタ + dashboard 通知（オプション）
- `fresh_context: true` を external_panes に対して設定したら、ロード時にエラー
- 既存の workspaces ベース機能は完全互換を保つ

### 11.4 実装サブ PR の提案

本設計とは別 PR で実装：
1. **K1-K3**: `external_panes` 最小実装（target 参照 + send-keys）
2. **K4-K5**: pane-watcher の分岐 + busy 観測
3. **K6**: `prompt_command`（GitLab Issue 連携前に）

K1-K5 だけで本設計の本流（90分ハートビート + 240分レビュー）は動く。K6 は将来。

## 12. 最小実装ステップ

1. **kiro-loop 改良 PR**（別ブランチ）で §11.1 の K1-K5 を実装
2. **本ブランチ**には設計書のみコミット（kiro-loop コード変更は別 PR）
3. **起動ラッパ** `autonomous-day-bootstrap.sh` のスケルトンを `docs/examples/` 配下に置く（任意）
4. **dry-run**: 小さな north_star（例: 「README のリンク切れをすべて見つけて修正する」）で 6 時間走らせる
   - dashboard.md と reports/ の蓄積を観察
   - §6.2 のプロンプト文面をチューニング
5. **24h 走行**: 中規模の north_star で本番投入

`tools/multi-agent-shogun-kiro/` 配下には **何も追加しない**。

## 13. オープン論点

- **shogun ペインの正確な tmux target 名**: `shutsujin_departure.sh` の実装を読んで確定する必要がある
  （`multiagent:shogun.0` か `multiagent:0.X` か別 session か）。
  本設計では仮に `multiagent:shogun.0` としているが、実装前に確認。
- **shogun OSS が `/exit` で抜けたときの自動復帰**: OSS 側の責務。kiro-loop はログするだけ
- **north_star を満たした後の挙動**: Shogun が「完了」と判断したらどう振る舞うか
  - 案: ハートビートに「north_star 達成済みならアイドル状態を保て」を明記
- **コスト上限**: 1日あたり kiro-cli トークン消費。Shogun の判断頻度（90分ハートビート + 240分深いレビュー）で実効的に抑制されるはず。実走後にメトリクス見て調整
- **GitLab Issue 取り込みの優先度判定**: Shogun のプロンプト設計に依存。Issue の label でフィルタするのが現実的

---

## 付録A: 既存部材リファレンス

- kiro-loop: `tools/kiro-loop/README.md`, `tools/kiro-loop/DESIGN.md`
- Shogun 階層（OSS、不可侵）: `tools/multi-agent-shogun-kiro/instructions/generated/kiro-shogun.md`,
  `tools/multi-agent-shogun-kiro/shutsujin_departure.sh`（公式起動スクリプト）
- ralph ループ系統の元祖議論: `docs/plans/2026-05-11-kiro-loop-oneshot-design.md`

## 付録B: 用語

- **north_star**: 1日のループ全体のゴール。最初に人が1回だけ Shogun に渡す
- **ハートビート**: kiro-loop が定期的に shogun ペインへ送る確認プロンプト（90分間隔）
- **深いレビュー**: kiro-loop が 240 分間隔で送る、要約と方針見直しを促すプロンプト
- **OSS 不可侵**: `tools/multi-agent-shogun-kiro/` 配下のファイル変更・新規追加・起動コード呼び出しをすべて禁ずる方針
- **外部ペイン参照モード**: kiro-loop の改良機能（§11）。既存ペインに対し send-keys のみ行い、kiro-cli 起動・セッション管理をしない
- **起動ラッパ**: `shutsujin_departure.sh` と `kiro-loop` を冪等に並べて呼ぶ薄いシェルスクリプト。WSL タスクスケジューラから呼ばれる
