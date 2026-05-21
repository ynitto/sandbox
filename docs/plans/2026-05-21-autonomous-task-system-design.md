# 自律タスク処理システム 設計書（kiro-loop × multi-agent-shogun-kiro）

> 作成日: 2026-05-21
> 対象ブランチ: `claude/autonomous-task-system-design-TUNpD`
> 関連: `tools/kiro-loop/`, `tools/multi-agent-shogun-kiro/`, `tools/issue-mailbox/`

---

## 1. 目的とスコープ

夜寝る前に「大きな1タスク（または複数の大目標）」を投入しておけば、翌日の業務時間（最大24時間）を通じて
kiro-cli 群が自律的に分解・並列実行・統合・報告を繰り返す系を、**既存部材の結合のみ**で構築する。

非目的:
- 新しいエージェント枠組みの設計（既存 Shogun→Karo→Ashigaru/Gunshi をそのまま使う）
- 新規ループスケジューラの実装（kiro-loop をそのまま使う）

## 2. 結論（採用案）

**案A「kiro-loop は外殻、Shogun が艦隊」**を採用する。
将来 **案C「外部入力ルータ層」**へ段階的に伸ばす（§10）。

採用理由:
- Shogun 機構は既にイベント駆動（inbox + inotify、`F004` で sleep 禁止）で長時間運用を前提に設計済み
- kiro-loop の責務（死活監視・再起動・定期注入・同時実行セマフォ）は Shogun が苦手な層を補完する
- 結合点が「Lord 役プロンプトの注入」1か所に閉じるので、リバート容易

## 3. 全体構成

```
            ┌──────────────────────────────────────────────────┐
            │ ホスト (WSL)                                      │
            │                                                  │
            │  kiro-loop daemon                                │
            │   ├─ session-monitor    (10s)  ペイン蘇生          │
            │   ├─ periodic-scheduler ( 1s)  Lord 注入プロンプト │
            │   └─ slot-monitor       ( 2s)  同時実行制御        │
            │           │                                       │
            │           │ send-keys                            │
            │           ▼                                       │
            │   tmux session: shogun:main                      │
            │     └─ kiro-cli chat --agent shogun  ← Lord代役  │
            │                                                  │
            │   tmux session: multiagent                       │
            │     ├─ 0.0  karo                                  │
            │     ├─ 0.1〜0.7  ashigaru1〜7                     │
            │     └─ 0.8  gunshi                                │
            │           │                                       │
            │           │ inbox_watcher.sh (inotify)           │
            │           ▼                                       │
            │   queue/  inbox/, tasks/, reports/, dashboard.md │
            └──────────────────────────────────────────────────┘
```

kiro-loop が「心臓の鼓動」、Shogun 階層が「軍」、両者の接点は **shogun ペインへの定期プロンプト** のみ。
agent 間通信は既存の inbox YAML + inotify をそのまま使う（kiro-loop は介在しない）。

## 4. 部材と責務

| 部材 | 責務 | 触ってよい層 |
|---|---|---|
| kiro-loop daemon | ペイン死活監視・再起動、Lord 注入、ホスト面 SIGHUP 回避 | shogun ペインのみ |
| Shogun (kiro-cli) | 大目標の戦略判断、cmd 発令、ダッシュボード読み | `queue/shogun_to_karo.yaml` 書き込み |
| Karo | cmd 分解、ashigaru/gunshi 割当、合否判定、dashboard 更新 | `queue/tasks/*.yaml`, `queue/reports/*.yaml`, `dashboard.md` |
| Ashigaru ×7 | 実行（コード・調査・push・done_keywords） | 自分の task/report YAML のみ |
| Gunshi | 品質確認、設計分析、ダッシュボード補助 | gunshi の task/report |
| inbox_watcher.sh | inbox 変更検知 → tmux nudge（最大限ミニマル） | tmux send-keys だけ |
| issue-mailbox / gitlab-obsidian-sync | 外部チャネル正規化（将来=案C） | （現段階では未使用） |

**境界線（守るべき不変条件）**:
- kiro-loop は **shogun 以外のペインを触らない**。ashigaru の同時並列は Shogun 配下のセマフォと watcher に任せる
- Shogun → Karo は `shogun_to_karo.yaml` の cmd 経由のみ。kiro-loop の Lord 注入は「**新しい cmd を書くかどうかは Shogun が判断**」する形に留め、kiro-loop は cmd を直書きしない

## 5. 1日のタイムライン

```
時刻        kiro-loop が注入する Lord 役プロンプト                            状態の典型
──────────────────────────────────────────────────────────────────────────
前夜 23:00  (人間) shogun_to_karo.yaml に north_star と cmd_NNN を書く        cmd: pending
07:00       「north_star に対する今日の進捗を把握し、必要なら次の cmd を発令」    Shogun 起床
09:00       「未完 cmd のダッシュボードを確認、ブロッカーがあれば Karo に再指示」   日中ループ
13:00       同上                                                            日中ループ
17:00       「半日の戦果を要約し、追い込み計画を策定」                          中間レビュー
21:00       「今日の戦果をレトロし、残タスクを明日の cmd 候補として整理」          終業レビュー
随時        cmd 完了境界で Karo の dashboard 更新 → 次サイクル                 イベント駆動
```

日中の **9:00 / 13:00 ナッジ**は cron 風（kiro-loop の `cron` フィールド）で、それ以外は inbox 駆動が支配的。
kiro-loop の役目は「**沈黙が長引いたときに Shogun に呼吸させる**」こと。Shogun が能動的に動いている限り、ナッジは no-op で済む。

## 6. 接点設計：Lord 注入プロンプト

### 6.1 配置

`~/.kiro/kiro-loop.yaml` ではなく、プロジェクトワークスペース `<shogun-project>/.kiro/kiro-loop.yml` 側に書く。
理由: ワークスペース固有の north_star / dashboard パスに依存するため。

### 6.2 設定例

```yaml
kiro_options:
  trust_all_tools: true
  agent: shogun           # 既存 .kiro/agents/shogun.json を指定

max_concurrent: 0          # Shogun ペインは1本だけなので無制限でよい

prompts:
  - name: "朝の戦況確認"
    cron: "0 7 * * *"
    fresh_context: false   # /chat new はしない（後述 §8）
    prompt: |
      おはよう。dashboard.md を読み、queue/shogun_to_karo.yaml の未完 cmd を確認せよ。
      north_star に対して今日進めるべき次の一手を判断し、必要なら新しい cmd を発令してから手を止めよ。
      （手を止める = Karo に inbox_write した時点。執行は Karo に任せる）

  - name: "昼の進捗確認"
    cron: "0 13 * * *"
    fresh_context: false
    prompt: |
      dashboard.md と queue/reports/ を確認。停滞している cmd があれば原因を特定し、
      必要なら方針を見直して Karo に再指示せよ。

  - name: "夕の中間レビュー"
    cron: "0 17 * * *"
    fresh_context: false
    prompt: |
      半日の戦果を要約せよ。残り時間で達成可能なゴールに絞り、追い込み計画を Karo に発令せよ。

  - name: "夜のレトロ"
    cron: "0 21 * * *"
    fresh_context: false
    prompt: |
      本日の戦果をレトロし、残タスクを明日の cmd 候補としてダッシュボードに整理せよ。
      未確定の方針があれば 🚨要対応 セクションに必ず記載せよ。

  - name: "沈黙監視ナッジ"
    interval_minutes: 90
    fresh_context: false
    prompt: |
      最後の dashboard 更新と最後の inbox 受信時刻を確認せよ。
      90分以上 Karo から音沙汰がなければ、停滞要因を調査して必要なら cmd を再発令せよ。
      動きがあれば何もせず手を止めてよい。
```

### 6.3 設計原則（プロンプト本文）

- **「次に何をするか」を kiro-loop が決めない**。Shogun に判断を委ねる。kiro-loop は「考えろ」と促すだけ
- **「手を止めよ」を毎回明示**する。Shogun が暴走して ashigaru に直接 send-keys する事故（F002 違反）を防ぐ
- **「無動作で終わってよい」を許す**。動きがあるときにナッジが上書き判断を引き起こさないよう、Idempotent に設計

## 7. 大目標の投入方法

**結論**: `queue/shogun_to_karo.yaml` への正式 cmd 投入を一次経路、`queue/ntfy_inbox.yaml` を二次経路とする。

| 経路 | 用途 | 投入者 | 形式 |
|---|---|---|---|
| `shogun_to_karo.yaml` | 計画済みの大目標（前夜投入） | 人間 / kiro-loop 側スクリプト | 正規 cmd（north_star + purpose + acceptance_criteria 必須） |
| `ntfy_inbox.yaml` | スマホからの追加指示（日中） | 人間（ntfy 経由） | 短文。Shogun が判断して cmd 化 |
| `issue-mailbox` / GitLab Issue | 将来：外部システム連携 | 外部 | 案C で正規化層を挟む |

cmd の最低限テンプレ（前夜の人間用）:

```yaml
- id: cmd_NNN
  timestamp: "2026-05-21T23:00:00"
  north_star: "（1-2文。なぜ重要か。具体的に）"
  purpose: "（1文。"done" の姿）"
  acceptance_criteria:
    - "（テスト可能な条件1）"
    - "（テスト可能な条件2）"
  command: |
    （Karo 向けの自然文指示。how は書かない）
  project: <project-id>
  priority: high
  status: pending
```

**why この設計**: Shogun の Forbidden Actions に「Lord を装って ntfy_inbox を勝手に書く」がないため、二系統を並列に運用しても破綻しない。`shogun_to_karo` は計画タスクの正式入口で監査しやすい。

## 8. /chat new（コンテキストリセット）戦略

**結論**: **時間境界ではなく、cmd 完了境界 + 安全網（6時間）でリセット**する。

| 契機 | 動作 | 担当 |
|---|---|---|
| cmd を 1 件 done にした直後 | `/chat new` を Shogun ペインに送る | Karo → Shogun への dashboard 更新通知後、Shogun 自身が判断 |
| 最後の `/chat new` から 6 時間経過 | kiro-loop が `clear_command` 型を擬似的に発火（または専用ナッジ） | kiro-loop（安全網） |
| `redo_of` を含む再発令時 | 既存 Redo Protocol が `clear_command` を送る | Karo（既存仕様） |

**why cmd 境界**: 時間ベースだと長時間の cmd 実行中にリセットされてコンテキストを失う事故が起きる。cmd 単位なら「task 1つ完了 = 安全に忘れてよい」自然な境界になる。

**why 6時間の安全網**: cmd が長期化して 1 日リセットゼロだと累積でコンテキスト窓を食う。Shogun pane は会話量が少ない（判断と発令のみ）ので 6 時間で十分安全。

注意: kiro-loop の `fresh_context` を Shogun ペインに対して `true` にしてはいけない。判断履歴を失うとループの連続性が崩れる。Ashigaru ペインの /chat new は既存 Redo Protocol に任せる。

## 9. 失敗モードとリカバリ

| 失敗 | 検知 | リカバリ |
|---|---|---|
| WSL アイドルシャットダウン | Windows タスクスケジューラが5分毎に kiro-loop を呼ぶ（既存仕様） | kiro-loop 自身が PID ロックで二重起動を防ぎつつ再起動 |
| Shogun ペイン死亡 | kiro-loop の session-monitor が10秒で検知 | 同 pane で kiro-cli を再起動。会話履歴は `--resume` で復元 |
| Karo 死亡（沈黙） | 「沈黙監視ナッジ」が90分で発火 | Shogun が状況確認 → 必要なら cmd 再発令（Redo Protocol） |
| Ashigaru 全員 busy | kiro-loop の global semaphore が制御（既存） | キューに積み、cooldown 経過後に再試行 |
| inbox_watcher の inotify 不発（WSL2） | 30秒タイムアウト fallback（既存仕様） | inbox_watcher 自体の再起動は外部から（systemd か kiro-loop に追加） |
| cmd の acceptance_criteria が不明瞭で Karo が判定不能 | Karo が dashboard 🚨要対応 に記載 | 翌朝の人間レビューで修正 cmd 発令 |

**最悪ケース**: kiro-loop daemon ごと落ちて再起動もしない。→ Windows タスクスケジューラの5分間隔が安全網。

## 10. 将来拡張（案C への伸ばし方）

段階的に以下を足す。**全部やる必要はない**。動き始めてから不足が見えてからで十分。

| 拡張 | 追加コンポーネント | 効果 |
|---|---|---|
| 外部 Issue 連携 | `issue-mailbox` の watcher を kiro-loop daemon の一部として常駐 | GitLab Issue を `shogun_to_karo.yaml` に自動正規化 |
| ntfy 受信の前さばき | `scripts/ntfy_listener.sh` を kiro-loop が systemd 的に起動 | スマホ指示が落ちないことを保証 |
| skill ベース判断強化 | Karo 起動時の resources に `scrum-master`, `sprint-reviewer`, `agent-reviewer` を追加 | 分解品質・統合品質が安定 |
| メトリクス収集 | `kiro-log-exporter` で daemon ログを集約・Obsidian へ吐く | 翌日のレトロが定量化 |
| 複数プロジェクト並走 | kiro-loop を **プロジェクトごとに別インスタンス**で立てる（既存設計通り） | プロジェクト間でセマフォ共有なので暴走しない |

## 11. 最小実装ステップ（このブランチで残す成果物の想定）

設計のみで終わるか、最小構成までやるかは別判断。やる場合の手順:

1. **`tools/multi-agent-shogun-kiro` 配下に `examples/autonomous-day/` を新設**
   - `kiro-loop.yml` テンプレ（§6.2 を雛形に）
   - `shogun_to_karo.yaml` の前夜投入テンプレ（§7）
   - 起動 README（4行）
2. **既存 `kiro-loop` 側に手を入れない**ことを確認（外殻として既に十分）
3. **Shogun の `.kiro/agents/shogun.json` resources に dashboard.md のパスを追記**して、Lord 注入時に必ず読みに行くようにする
4. **dry-run**: cmd_001 として「小さな調査タスク」を投入し、24h 走らせて挙動確認
5. dashboard.md と reports/ の蓄積をレビュー → 6章のプロンプト文面をチューニング

## 12. オープン論点（決めなくても動くが、後で必要）

- **複数大目標を同時に走らせるとき**の優先度競合：当面は cmd の `priority` 任せ、Karo が judgement
- **人間が日中に介入したいとき**の流儀：`/chat new` をせず ntfy で短文投入する方針で運用しテスト
- **コスト上限**：1日あたり kiro-cli トークン消費の上限を設けるか。当面は「Shogun の判断頻度（cron で4回 + 90分ナッジ）」で実効的に抑制
- **失敗 cmd の自動エスカレーション基準**：3回連続 failed で Lord 通知、など。Karo 側ルールに追記要

---

## 付録A: 既存部材リファレンス

- kiro-loop: `tools/kiro-loop/README.md`, `tools/kiro-loop/DESIGN.md`
- Shogun 階層: `tools/multi-agent-shogun-kiro/instructions/generated/kiro-shogun.md`（Lord 注入時に参照される）
- Karo 規約: `tools/multi-agent-shogun-kiro/instructions/generated/kiro-karo.md`
- inbox 機構: `tools/multi-agent-shogun-kiro/scripts/inbox_watcher.sh`
- ralph ループ系統の元祖議論: `docs/plans/2026-05-11-kiro-loop-oneshot-design.md`
