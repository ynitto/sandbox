# 自律タスク処理システム 設計書（kiro-loop × multi-agent-shogun-kiro）

> 作成日: 2026-05-21
> 対象ブランチ: `claude/autonomous-task-system-design-TUNpD`
> 関連: `tools/kiro-loop/`, `tools/multi-agent-shogun-kiro/`

---

## 1. 目的とスコープ

ひとつの **north_star**（北極星目標）を渡したら、人の介在なしに丸1日（最大24時間）
kiro-cli 群が自律的に分解・並列実行・統合・報告を繰り返す系を、既存部材の結合のみで構築する。

### 不変原則（最重要）

1. **`multi-agent-shogun-kiro` は外部 OSS** として扱い、その中身は変更しない
   （`tools/multi-agent-shogun-kiro/` 配下のファイルを編集しない、設定ファイルテンプレも追加しない）
2. **人 / kiro-loop からの指示はすべて Shogun ペインへの「テキスト入力」として伝える**
   （`queue/*.yaml` を外部から直接書き換えない。`shogun_to_karo.yaml` は Shogun が書く）
3. **kiro-loop はセッション管理をしない**
   （`/chat new`, `/clear`, queue 操作などの内部状態リセットは Shogun 機構（Redo Protocol 等）に任せる。
   kiro-loop はペインの死活監視と再起動だけ行う）
4. **スマホ / ntfy 入力は使わない**
5. **人介在なしに自律動作を優先**する（人の介入チャネル＝Shogun ペインへの直接入力のみ、緊急時のみ）

### スコープ外

- 新しいエージェント枠組みの設計
- 新規ループスケジューラの実装
- Shogun OSS 側への機能追加・ファイル変更
- ntfy / 人による日中の能動介入

## 2. 結論（採用案）

**案A「kiro-loop は外殻、Shogun が艦隊」**を採用、ただし kiro-loop の責務は最小化する：

```
kiro-loop の責務 = (1) Shogun ペインを生かし続ける
                   (2) ハートビートのテキスト注入のみ
                   (3) 将来: 外部入力（GitLab Issue）をテキスト注入に変換
```

それ以外（cmd 発令、queue 操作、コンテキストリセット、ashigaru 制御）は **すべて Shogun OSS 側に閉じる**。

採用理由:
- Shogun 機構は既にイベント駆動（inbox + inotify、`F004` で sleep 禁止）で長時間運用を前提に設計済み
- kiro-loop の責務を「ペイン死活 + 鼓動の注入」に絞れば、OSS の中を一切触らずに統合できる
- 結合点が1か所（Shogun ペインへの send-keys）に閉じる

## 3. 全体構成

```
            ┌──────────────────────────────────────────────────┐
            │ ホスト (WSL)                                      │
            │                                                  │
            │  kiro-loop daemon (薄い外殻)                       │
            │   ├─ session-monitor    (10s)  shogun ペイン蘇生   │
            │   └─ periodic-scheduler ( 1s)  ハートビート注入    │
            │           │                                       │
            │           │ send-keys (テキストのみ)              │
            │           ▼                                       │
            │   tmux session: shogun:main                      │
            │     └─ kiro-cli chat --agent shogun              │
            │           │ ← ここから先は OSS 領域。kiro-loop は不可侵 │
            │           ▼                                       │
            │   tmux session: multiagent                       │
            │     ├─ 0.0  karo                                  │
            │     ├─ 0.1〜0.7  ashigaru1〜7                     │
            │     └─ 0.8  gunshi                                │
            │           │                                       │
            │           │ inbox_watcher.sh (inotify)           │
            │           ▼                                       │
            │   queue/  inbox/, tasks/, reports/, dashboard.md │
            │   （Shogun が自分で読み書き。外から触らない）       │
            └──────────────────────────────────────────────────┘
```

kiro-loop と Shogun 群の唯一の接点は **shogun ペインへの送信 (send-keys)**。逆向きはなし。

## 4. 部材と責務

| 部材 | 責務 | 触ってよい範囲 |
|---|---|---|
| kiro-loop daemon | Shogun ペインの死活監視・再起動、ハートビート注入 | shogun ペインへの send-keys だけ |
| Shogun (kiro-cli) | north_star に対する戦略判断、cmd 発令、状況把握 | OSS 内部の全機構（自前で完結） |
| Karo / Ashigaru / Gunshi | OSS 既存仕様どおり | （外部から触らない） |
| `queue/*.yaml`, `dashboard.md` | Shogun 階層の内部状態 | **外から read/write しない** |

### 守るべき不変条件

- kiro-loop は **shogun ペイン以外の tmux ペインを触らない**
- kiro-loop は **`queue/*.yaml` を直接 read/write しない**
- kiro-loop は **`/chat new` / `/clear` を送らない**（Shogun が自分で打つのは自由）
- 人間からの入力は **shogun ペインに直接打つ**か、kiro-loop の prompt 設定経由でテキスト注入する

## 5. 1日のタイムライン（自律モード）

人間がやるのは **起動時の north_star 投入1回だけ**。あとは kiro-loop と Shogun の自律ループ。

```
T=0       (人間) shogun ペインに north_star をテキストで入力（後述 §7）
T=0+      Shogun が cmd_001 を queue/shogun_to_karo.yaml に書き、Karo に inbox_write
          → 以降、Karo/Ashigaru/Gunshi の既存ループが回る
T=0+45m   (kiro-loop) ハートビート注入 #1
          「north_star に対する現状を確認し、停滞・完了があれば次の手を打て」
T=0+90m   (kiro-loop) ハートビート注入 #2  … 以下 90分おき
T=0+4h    (kiro-loop) 深いレビュー注入
          「これまでの成果を summarize し、north_star に対する残課題を整理せよ」
T=24h     最終レビュー注入 → daemon は走り続けるが新規 cmd は Shogun 判断で止まる
```

時刻ベースの「朝・昼・夕」のような人間サイクルは使わない（人介在なし前提のため）。
**経過時間ベースのハートビート + 深いレビュー** の2系列で十分。

## 6. ハートビート注入プロンプト

### 6.1 配置と原則

`<shogun-project>/.kiro/kiro-loop.yml` に prompts として記述。

設計原則:
- **「次に何をするか」を kiro-loop が決めない**。Shogun に判断を委ねる。kiro-loop は「考えろ」と促すだけ
- **「手を止めよ」を毎回明示**する（Shogun が暴走して ashigaru に直接 send-keys する F002 違反を防ぐ）
- **「動きがあるなら無動作で終わってよい」を許す**（idempotent。ナッジが動きを乱さない）
- **`fresh_context: false` を全エントリで強制**。kiro-loop はリセットを発火しない（§8）

### 6.2 設定例

```yaml
kiro_options:
  trust_all_tools: true
  agent: shogun           # OSS 既存の .kiro/agents/shogun.json をそのまま使用

max_concurrent: 0          # Shogun ペインは1本だけなので無制限

prompts:
  - name: "ハートビート"
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

### 6.3 やらないこと

- 「朝・夕・夜」のような時刻ベース注入は **行わない**（人間サイクルを暗黙に仮定するため）
- 「沈黙監視」のようなメタ監視も **行わない**（ハートビートが兼ねる）
- `/chat new` / `/clear` の注入は **行わない**（§8）

## 7. 大目標（north_star）の投入方法

### 7.1 起動時（人による初回投入）

人が shogun ペインに **テキストで直接入力**する。kiro-loop は介在しない。

例:
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

具体的には kiro-loop の prompt にコマンド埋め込み機能を活用し、注入時点で Issue を取得して prompt 本文に展開する。

### 7.3 やらないこと

- ntfy / スマホ入力経路は **作らない**
- `issue-mailbox` 経由の自動 queue 書き込みは **行わない**（OSS queue に外から書かない方針のため）
- 複数 north_star の同時並走は **当面サポートしない**（1 北極星 × 1日）

## 8. コンテキストリセット戦略

**結論：kiro-loop はリセットを一切発火しない**。Shogun OSS 側の既存 Redo Protocol（`clear_command` 型 inbox メッセージ）に任せる。

| 契機 | 動作 | 担当 |
|---|---|---|
| cmd を 1 件 done にした直後 | （Shogun が必要なら自分で `/chat new` を打つ） | Shogun |
| redo 発令時 | 既存 Redo Protocol が `clear_command` を ashigaru へ送る | Karo（OSS 既存仕様） |
| Shogun ペイン自身のコンテキスト膨張 | Shogun の判断で `/chat new` を打つ | Shogun |

**why kiro-loop が触らないか**:
- 不変原則3（セッション管理しない）に従う
- 時間ベースで強制リセットすると長時間 cmd の途中で履歴を失う事故が起きる
- Shogun は判断と発令だけなので会話量が少なく、コンテキスト膨張は遅い

ハートビートプロンプトに「会話履歴が重いと感じたら `/chat new` を自分で打ってよい」と書く程度は許容（テキスト注入の範囲内）。

## 9. 失敗モードとリカバリ

| 失敗 | 検知 | リカバリ |
|---|---|---|
| WSL アイドルシャットダウン | Windows タスクスケジューラが5分毎に kiro-loop を呼ぶ（既存） | kiro-loop が PID ロックで二重起動を防ぎつつ再起動 |
| Shogun ペイン死亡 | kiro-loop session-monitor が10秒で検知 | 同 pane で kiro-cli を再起動、`--resume` で履歴復元 |
| Karo / Ashigaru の沈黙 | （Shogun OSS 側のエスカレーションに委ねる） | OSS 既存仕様（escalation: Escape×2 → context reset） |
| inbox_watcher の inotify 不発（WSL2） | 30秒タイムアウト fallback（既存） | OSS 既存仕様 |
| Shogun が cmd 発令を忘れる | 次のハートビート（最大90分後）で再判断 | プロンプトが「未完 cmd がなく north_star 未達なら新 cmd 発令」を明示 |
| north_star が達成不能 | （人が介在しない以上、検知できない） | 翌日の人レビュー時に dashboard 🚨要対応 で発見 |

人が介在しないため、**「最悪 24 時間進捗ゼロ」も許容**する設計。
ループが回り続けていれば dashboard には何らかの記録が残るので、翌日の人レビューで状況把握できる。

## 10. 将来拡張：GitLab Issue 入力

### 10.1 ゴール

人が GitLab Issue を立てると、その内容が自動的に Shogun の判断材料として流れ込み、新しい cmd が生まれる。

### 10.2 設計

**実装方針: kiro-loop の prompt を介してテキスト注入のみで実現する**（queue を外から触らない）。

```yaml
# kiro-loop.yml の prompt エントリ（将来追加）
prompts:
  - name: "GitLab Issue 取り込み"
    interval_minutes: 60
    fresh_context: false
    prompt_command: |
      python ~/sandbox/tools/gitlab-obsidian-sync/sync.py \
        --list-open-issues --label autonomous-task --format=prompt
    # prompt_command の出力を prompt 本文として使う
    # 内容例（gl.py が生成する想定の文章）:
    #   「以下の Issue が新規にオープンされている:
    #    - #123: foo を作る (label: autonomous-task)
    #    - #124: bar を修正
    #    north_star に整合するなら新しい cmd として取り込んで Karo に発令せよ。
    #    整合しないものは無視してよい。手を止めよ。」
```

**ポイント**:
- kiro-loop の prompt_command 機能（要追加。`prompt` の代わりに subprocess の標準出力を本文に使う）でテキストを動的生成
- Shogun は **テキストとしてだけ** Issue を受け取り、cmd 化の判断と書き込みは自分で行う
- GitLab 側への状態書き戻し（Issue クローズ等）は Shogun が ashigaru に指示するか、Issue 側で人が行う

**kiro-loop への必要な機能追加**:
- `prompt_command` フィールドの追加（標準出力を prompt 本文として注入）
- タイムアウト・エラー時のフォールバック（空出力なら注入をスキップ）

これは kiro-loop 自身の機能追加であり、Shogun OSS は触らない。本設計とは別 PR で対応する。

### 10.3 スコープ外（将来も含めて）

- ntfy / スマホ入力は将来も実装しない
- `issue-mailbox` の自動 queue 注入は採用しない（OSS queue 外部書き込み禁止のため）

## 11. 最小実装ステップ

OSS を一切触らないため、追加成果物は kiro-loop 側設定とランブックのみ。

1. **`docs/guides/autonomous-day-runbook.md` を新設**（任意）
   - 起動手順: kiro-loop daemon 起動 → shogun ペインに north_star を打つ → 24h 放置
   - 終了手順: dashboard.md と reports/ をレビュー → daemon 停止
2. **kiro-loop 設定サンプル**を docs 配下に置く（`docs/examples/kiro-loop-autonomous.yml` 等）
   - §6.2 の prompts と §10.2 の Issue 取り込み（コメントアウト状態でテンプレ提供）
3. **dry-run**: 小さな north_star（例: 「README のリンク切れをすべて見つけて修正する」）で 6 時間走らせる
   - dashboard.md と reports/ の蓄積を観察
   - §6.2 のプロンプト文面をチューニング
4. **24h 走行**: 中規模の north_star で本番投入

`tools/multi-agent-shogun-kiro/` 配下には **何も追加しない**。

## 12. オープン論点

- **north_star を満たした後の挙動**: Shogun が「完了」と判断したらどう振る舞うか
  - 案: ハートビートに「north_star 達成済みならアイドル状態を保て」を明記
- **複数 north_star に拡張するか**: 当面 1 北極星 1 日でテストし、課題が出てから検討
- **GitLab Issue 取り込みの優先度判定**: Shogun のプロンプト設計に依存。Issue の label でフィルタするのが現実的
- **コスト上限**: 1日あたり kiro-cli トークン消費の上限。Shogun の判断頻度（90分ハートビート + 240分深いレビュー）で実効的に抑制されるはず。実走後にメトリクス見て調整

---

## 付録A: 既存部材リファレンス

- kiro-loop: `tools/kiro-loop/README.md`, `tools/kiro-loop/DESIGN.md`
- Shogun 階層（OSS、不可侵）: `tools/multi-agent-shogun-kiro/instructions/generated/kiro-shogun.md`
- ralph ループ系統の元祖議論: `docs/plans/2026-05-11-kiro-loop-oneshot-design.md`

## 付録B: 用語

- **north_star**: 1日のループ全体のゴール。最初に人が1回だけ Shogun に渡す
- **ハートビート**: kiro-loop が定期的に shogun ペインへ送る確認プロンプト（90分間隔）
- **深いレビュー**: kiro-loop が 240 分間隔で送る、要約と方針見直しを促すプロンプト
- **OSS 不可侵**: `tools/multi-agent-shogun-kiro/` 配下のファイル変更・新規追加をすべて禁ずる方針
