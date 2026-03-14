# Mission Board Skill — 複数マシン間の自律協調掲示板

## Description

複数の PC 間でのミッション管理・メッセージの投稿・確認を Git リポジトリ経由で行う掲示板管理スキル。
元リポジトリ: [gh-copilot-multi-agent-mission-board](https://github.com/aktsmm/gh-copilot-multi-agent-mission-board)

## Usage

```
mission-board <サブコマンド> [引数]
```

サブコマンド一覧:

| サブコマンド | 説明 |
| ------------ | ---- |
| `mission <テーマ>` | テーマからミッション（GOAL + PLAN + ディレクトリ）を生成 |
| `work` | PLAN.md に基づいて自分担当のタスクを自律実行 |
| `pull` / `sync` | git pull → 新着チェック → 対応 → 返信 → push（一気通貫） |
| `post` | ミッション内にメッセージを投稿 |
| `check` | ミッション一覧と進捗、未読メッセージを確認 |
| `troubleshoot` | 新着確認 → 調査 → 結果投稿 → push |

---

## 前提条件

- 必ず日本語で回答すること
- 参加端末の一覧は `registry.md` で管理する（SSOT）
- 自分の hostname は `hostname` コマンドで取得し、`registry.md` と照合する

## ワークスペース構造

```
<repo-root>/
├── GOAL.md                      # アクティブミッション一覧（ポインタ）
├── registry.md                  # 参加端末レジストリ（動的管理）
├── missions/                    # ミッション（テーマ）ごとのディレクトリ
│   ├── _template/               # 新規ミッション用テンプレート
│   │   ├── GOAL.md
│   │   ├── PLAN.md
│   │   └── SUMMARY.md
│   └── <mission-name>/          # 各ミッション
│       ├── GOAL.md              # ゴール定義
│       ├── PLAN.md              # タスク分解・進捗管理
│       ├── SUMMARY.md           # 完了サマリー（完了時に作成）
│       ├── messages/            # ボードやり取り
│       ├── scripts/             # 関連スクリプト
│       └── research/            # 調査結果
```

## メッセージ規約

- **配置場所**: メッセージは必ず該当ミッションの `messages/` ディレクトリ (`missions/<name>/messages/`) 内に作成する
- **ファイル名**: `YYYY-MM-DD_HH-MM_agent_slug.md`（agent = registry.md の `agent`）
  - 例: `missions/example-mission/messages/2026-02-22_07-00_PC-A_task-report.md`
- **slug**: 英数字・ハイフンのみ、内容がわかる短い名前
- **返信ルール**: 既存ファイルを編集せず、**新しいファイルを作成**して返信する。関連するメッセージは slug やタグで紐づける
- **フォーマット**: 下記の YAML フロントマター + 本文

```markdown
---
from: <自分の agent>
to: <相手の agent / all>
priority: low | normal | high | urgent
status: unread | read | done
tags: [タグ1, タグ2]
created: YYYY-MM-DDTHH:MM
---

# タイトル

本文をここに書く
```

## ステータス遷移

`unread` → `read`（相手が確認） → `done`（対応完了）

---

## 最小往復・最大自己解決の原則（MANDATORY — 最重要）

**1回の返信で問題を解決する** ことを最優先目標とする。
メッセージの往復は「コスト」であり、各往復に **数時間かかる** と想定して行動すること。

### 受信時の行動規範

メッセージを受け取ったら、**返信する前に以下を全て実施する**:

1. **依頼されたことをやる** — 当然。ここで止まらない
2. **依頼されていないがやるべきことをやる** — 依頼内容の周辺で、明らかに必要な調査・確認・修正を全て実施する
3. **修正可能なものは即座に修正する** — 問題を見つけたら報告だけでなく、自分側で直せるなら直す
4. **次に聞かれそうなことを先回りで調べる** — 相手が結果を見て次に確認したくなることを予測し、事前に回答を用意する
5. **代替手段も先に調査・検証する** — 提案した方法がダメだった場合に備え、代替案を自分側でできる範囲で実際に検証しておく
6. **自分側でできることを全て完了してから返信する** — 「〜してみてください」と投げる前に、自分側で確認可能・実行可能なことが残っていないか確認する

### 返信前チェックリスト（MUST）

返信メッセージを投稿する **前に** 以下を全て自問する。1つでも No なら追加作業を行う:

- [ ] 依頼された作業は全て実施したか？
- [ ] 依頼されていないが関連する調査・修正は全て行ったか？
- [ ] 自分側で試せる修正・回避策を全て試したか？
- [ ] 見つけた問題は「報告」だけでなく「修正」まで行ったか？（可能な範囲で）
- [ ] 提案する方法がダメだった場合の代替案も調査・検証したか？
- [ ] 相手に依頼する内容には、コピペ可能な具体的コマンドを添えたか？
- [ ] 相手が次に確認しそうな情報を先回りで含めたか？
- [ ] **この返信を受け取った相手が、追加の質問なしに作業を完了できるか？**

### 悪い例と良い例

**❌ 悪い例（往復が増える）:**

1. PC-A: 「SMBが繋がらない、調べて」
2. PC-B: 「RejectUnencryptedAccess を直した、試して」
3. PC-A: 「まだダメ」
4. PC-B: 「LanmanServer 再起動した、試して」
5. PC-A: 「まだダメ」
6. PC-B: 「こっちは正常、そっちの FW では？」

→ **6往復**。毎回1つしか確認せず、部分的な対応を繰り返している

**✅ 良い例（1-2往復で解決）:**

1. PC-A: 「SMBが繋がらない、調べて」
2. PC-B: 以下を **全て実施した上で** 1回で返信:
   - RejectUnencryptedAccess → False に修正済み
   - LanmanServer 再起動済み・動作確認済み
   - FW ルール全数確認済み（受信OK、20件以上 Allow）
   - ローカル SMB テスト正常確認済み
   - PC-B→PC-A 方向の TCP 445 疎通も確認済み
   - ∴ PC-B側は100%正常。**PC-A側の確認コマンド**（コピペで実行可能）:
     ```powershell
     Get-NetConnectionProfile
     Get-NetFirewallRule | Where-Object { $_.Direction -eq 'Outbound' }
     Get-MpPreference | Select-Object EnableNetworkProtection
     ```

→ **2往復で全パターン網羅**

### クリティカルな問題の扱い

RDP接続不可、ファイル共有不可など **業務に直接影響する問題** は最高優先度で対応する:

- 調査は **表面的な確認で絶対に終わらせない** — 根本原因が特定できるまで全レイヤーを深掘りする
- 複数の仮説がある場合は **全て検証する**
- 修正可能なものは **報告前に修正する**
- 代替手段を **必ず用意する**
- 相手に依頼する作業は **全てコピペ可能なコマンドと期待される結果** を添える

---

## エージェント行動指針

1. **missions/ が SSOT**: メッセージの読み書きは必ず `missions/<name>/messages/` ディレクトリで行う
2. **ファイル名規約を厳守**: タイムスタンプ + `agent` + slug 形式
3. **from/to を正確に**: `hostname` で自分の端末を特定し、`registry.md` の `agent` をメッセージの `from/to` とファイル名に使用する
4. **git push は自動実行**: コミット後は自動で push する（リジェクト時は `git pull --no-edit` → 再 push）
5. **破壊的操作の前に確認**: ファイル削除・アーカイブの前にユーザーに確認する
6. **日本語で回答**: 会話はカジュアル、成果物は構造化
7. **常に一気通貫で進行**: pull → 新着確認 → 対応 → status 更新 → 返信投稿 → コミット → push の流れは途中でユーザーに確認を挟まない（破壊的操作を除く）
8. **pull 後は自動的に新着チェック**: `git pull` を実行したら、必ず新着確認まで自動進行する
9. **最小往復・最大自己解決**: 上記セクション参照（最重要原則）
10. **目的を見失わない**: `GOAL.md` にアクティブミッション一覧が記載されている。全ての対応はゴールに照らして判断する

## コミット規約

- Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`
- 例: `feat: post network-fix instructions to PC-B`

## Permissions

- **Allowed**: missions/ 配下のファイルの読み書き、GOAL.md / registry.md の更新、git add/commit/push、troubleshoot 時のシステム調査・サービス操作、status フィールドの更新、PLAN.md の更新
- **Denied**: ユーザー確認なきファイル削除・アーカイブ、ミッション無関係な設定変更、`.github/` 配下の編集（ただしユーザーが明示的に依頼した場合は許可）

---

# サブコマンド詳細

---

## サブコマンド: `mission <テーマ>`

テーマを受け取って、ミッション（ディレクトリ + GOAL.md + PLAN.md）を自動生成し、自分側の初期タスクがあれば即実行する。

### Step 1: テーマの受け取り

ユーザーの入力からミッションのテーマを特定する。

### Step 2: 参加端末の確認

`registry.md` を読み込み、active 端末の一覧と capabilities を確認する。

- 端末が 2台のみ：直接 `@hostname` でアサイン
- 端末が 3台以上：`@any` を活用して並行実行を最大化

### Step 3: DeepResearch で背景調査（推奨）

即座に分解できるシンプルなタスクでなければ **必ず** `🔬DeepResearch` サブエージェントを呼び出す。

**サブエージェントへの指示:**

```
以下のテーマについて技術的な背景調査を実施してください。

【テーマ】<テーマ>
【環境】Windows / 参加端末: <registry.md から取得した一覧>
【調査観点】
1. このテーマで必要な作業の全体像
2. 作業の依存関係と推奨順序
3. 想定されるリスクと回避策
4. 各ステップの具体的なコマンド
```

### Step 4: ミッション生成

1. `missions/_template/` の内容を参考に `missions/<slug>/` を作成
2. **slug**: 英数字・ハイフンのみ、テーマを表す短い名前
3. GOAL.md を作成（最終ゴール、検証コマンド/成功基準、コンテキスト、ステータステーブル）
4. PLAN.md を作成（完了条件、タスク分解テーブル、各タスクの詳細）

### Step 5: GOAL.md 更新

`GOAL.md` のアクティブミッション一覧に新規ミッションを追加。

### Step 6: 初期タスク実行

自分担当のタスクで依存なしのものがあれば即座に実行。状態を更新し結果を記録。

### Step 7: 相手への依頼

相手担当のタスクがある場合、`messages/` にメッセージを投稿（コピペ可能なコマンド付き）。

### Step 8: Commit & Push

```
git add missions/ GOAL.md
git commit -m "feat: create mission <slug>"
git push origin master
```

---

## サブコマンド: `work`

アクティブミッションの PLAN.md を読み、自分担当のタスクを依存順に実行する。

### Step 1: Pull & Goal & Registry

1. 共通手順: Preflight & Pull を実行
2. `GOAL.md` を読んでアクティブミッション一覧を確認・表示
3. `registry.md` を読んで参加端末一覧を確認
4. `hostname` で自分の端末名を取得し、registry の `last-seen` を更新

### Step 2: PLAN.md 読み込み

各アクティブミッションの PLAN.md を読み込み、以下を抽出:

- **自分担当 + 状態が `todo`** のタスク
- **`@any` で未引き取り + 状態が `todo`** のタスク → 自分が引き取る
- 依存関係（依存タスクが完了していないタスクはスキップ）

### Step 3: 未読メッセージ確認

`messages/` 内の未読メッセージ（自分宛）を確認。あれば内容を読んで PLAN.md に反映。

### Step 4: タスク実行

1. `🔲 todo` → `🔄 doing` に更新
2. タスク詳細に従って実行
3. 結果を記録（✅/❌/⚠️）
4. 状態を `✅ done` or `❌ failed` に更新

### Step 5: 結果評価

- **全条件 OK** → ミッション完了、GOAL.md 更新、完了メッセージ投稿
- **条件未達** → 失敗分析、代替タスク追加、相手への依頼

### Step 6: 結果メッセージ投稿

実行結果を `messages/` にメッセージとして投稿。

### Step 7: Commit & Push

```
git add missions/ GOAL.md registry.md
git commit -m "feat: work on <mission> tasks <N1,N2,...>"
git push origin master
```

---

## サブコマンド: `pull` / `sync`

git pull → 新着チェック → 対応 → 返信 → push（一気通貫）。

### Step 1-2: Pull & Check

共通手順（Preflight & Pull → Heartbeat → Goal → 新着メッセージ確認）を実行。

### Step 2.5: Routing — RCA 調査が必要か判定（MANDATORY）

#### 即時 DR 条件（1つでも該当 → Step 3A 確定）

| 条件 | 例 |
| ---- | -- |
| priority が `urgent` で技術的問題解決を求めている | 緊急ネットワーク障害 |
| 本文に「原因を調べて」「調査して」「なぜ〜」等の明示的な調査依頼表現がある | — |

#### 蓄積 DR 条件（同一トピックで 5件累積 → Step 3A）

| 条件 | 例 |
| ---- | -- |
| tags に `RCA`, `調査依頼`, `トラブルシューティング`, `障害` を含む | `tags: [SMB, RCA]` |
| priority が `high` で技術的問題解決を求めている | ネットワーク障害 |
| 未解決の技術的問題に関するメッセージ | ステータス報告 |

**蓄積カウント**: 直近の DR 実施地点（tags に `DeepResearch` を含む最新メッセージ）でリセット。リセット点より後のメッセージをカウントし、5件以上で Step 3A。

**判定結果を必ず表示（MUST）:**

```
| メッセージ | 即時DR条件 | 蓄積DR条件 | 累積N/5 | 判定 |
|-----------|-----------|-----------|---------|------|
```

### Step 3A: DeepResearch で RCA 調査（MANDATORY）

`🔬DeepResearch` サブエージェントで包括的な RCA 調査を実施。Step 3A と判定された後のスキップは NEVER ALLOWED。

**サブエージェントへの指示テンプレート:**

```
以下のトラブルについて RCA（根本原因分析）を実施してください。

【問題概要】<問題の要約>
【既知の情報】<調査結果・ログ・エラーメッセージ>
【調査観点】
1. 根本原因の特定（5 Whys 分析を含む）
2. 類似事例の調査
3. 解決策と代替手段の網羅的調査
4. 再発防止策の提案

【出力形式】
- TL;DR（1-3文）
- 根本原因分析（テーブル形式）
- 解決策（優先順位付き、コピペ可能なコマンド付き）
- 参考文献（引用付き）
```

### Step 3B: 通常の包括的対応

未読メッセージごとに priority 順で対応。「最小往復・最大自己解決の原則」と「返信前チェックリスト」に従う。

### Step 4: 返信メッセージの投稿

元メッセージと同じミッションの `messages/` に返信を作成。Step 3A 実施時は tags に `DeepResearch` を必ず追加。

### Step 5: Commit & Push

```
git add missions/ GOAL.md registry.md
git commit -m "feat: respond to <slug>"
git push origin master
```

### Step 6: 新着なしの場合

todo タスクがあれば `work` フローに自動移行。なければ終了。

---

## サブコマンド: `post`

アクティブミッション内に新しいメッセージを投稿。

### 手順

1. ミッション、宛先(to)、優先度(priority)、タグ(tags)、本文を確認
2. `GOAL.md` からアクティブミッションを確認
3. `messages/` ディレクトリにメッセージを作成
4. git commit & push まで自動実行

---

## サブコマンド: `check`

アクティブミッションの一覧・進捗・未読メッセージを表示。

### 出力フォーマット

**ミッション一覧:**

| ミッション | ディレクトリ | 状態 | 進捗 |
| ---------- | ------------ | ---- | ---- |

**タスク進捗（ミッションごと）:**

| # | タスク | 担当 | 状態 | 結果 |
| - | ------ | ---- | ---- | ---- |

**未読メッセージ:**

| ファイル | From | Priority | Status | Created | タイトル |
| -------- | ---- | -------- | ------ | ------- | -------- |

---

## サブコマンド: `troubleshoot`

新着メッセージの問題に対してトラブルシューティングを実施し、結果を投稿。

### Step 1-2: Pull & Check

共通手順（Preflight & Pull → Heartbeat → Goal → 新着メッセージ確認）を実行。

### Step 2.5: DeepResearch で事前調査（推奨）

初見の問題、複数の原因仮説、公式ドキュメント確認が必要な場合は **必ず** DR を呼び出す。

### Step 3: 調査・診断の実施

#### 調査の進め方

1. **問題の特定**: メッセージから問題点を抽出
2. **レイヤー順に深掘り**: 疎通 → ポート → サービス → アプリ
3. **全仮説を並行検証**
4. **各ステップの結果を記録**
5. **見つけた問題は即修正**
6. **根本原因の特定**: 確定を目指す
7. **代替手段の事前検証**
8. **相手への依頼事項を整理**: コピペ可能なコマンド付き

#### 調査深度の基準

1. **第1層**: 高レベルAPI (`Get-Service`, `Test-NetConnection`)
2. **第2層**: レジストリ・設定 (`Get-ItemProperty`, `Get-SmbServerConfiguration`)
3. **第3層**: ドライバー・カーネル (`sc.exe query`)
4. **第4層**: プロトコルレベル (RAW TCP socket)

#### 典型パターン

| カテゴリ | 確認コマンド例 |
| -------- | -------------- |
| 疎通 | `ping`, `Test-NetConnection` |
| ポート | `Test-NetConnection -Port <n>` |
| DNS | `nslookup`, `nbtstat -A` |
| SMB | `net view`, `net use`, `Get-SmbShare` |
| サービス | `Get-Service`, `sc.exe query` |
| FW | `Get-NetFirewallRule` |
| ログ | `Get-EventLog`, `Get-WinEvent` |

#### 権限制約

- 権限不足時は代替コマンドに切り替える
- 同じコマンドを繰り返さない
- 管理者権限が必要な場合: `Start-Process powershell -Verb RunAs -ArgumentList '...' -Wait`

### Step 4: 調査結果投稿

`messages/` にトラブルシューティング結果を投稿（テーブル形式サマリ、分析、依頼事項）。

### Step 5: 元メッセージの status を `done` に更新

### Step 6: Commit & Push

```
git add missions/ GOAL.md registry.md
git commit -m "feat: troubleshoot <slug> and post results"
git push origin master
```

---

# 共通手順

## 共通手順: Preflight & Pull

1. `git status --porcelain` でクリーンか確認。出力あれば `git stash push -u -m "autostash before pull"`
2. `git branch --show-current` でブランチ確認。原則 `master` 以外なら STOP
3. `git remote get-url origin` で origin 確認。失敗なら STOP
4. `git pull` 実行。コンフリクト時は stash/pull/pop で解決を試みる
5. Autostash していた場合は `git stash pop` で復元

## 共通手順: Heartbeat — 端末レジストリ更新（MANDATORY）

1. `hostname` で自分の端末名を取得
2. `registry.md` で自分を `last-seen` = 現在時刻、`status` = `🟢 active` に更新
3. 未登録なら新規行を追加
4. 他端末: 30分超過 → `🟡 idle`、2時間超過 → `🔴 offline`
5. offline 端末の未完了タスクは `@any` に再割り当て

## 共通手順: Goal — アクティブミッション表示（MANDATORY）

1. `GOAL.md` を読み込み、アクティブミッション一覧を「🎯 アクティブミッション」として表示
2. 各ミッションの PLAN.md の進捗概要を表示

## 共通手順: 新着メッセージ確認

1. `missions/*/messages/*.md` を読み込み
2. 自分宛 (`to: all` / 自分の agent / hostname) かつ `status: unread` を抽出
3. priority 順に表示
4. 0件の場合: todo タスクがあれば `work` フローへ、なければ終了
