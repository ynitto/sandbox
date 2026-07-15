# Agent Dashboard 計画レビュー・検収・バックログ補助設計

## 背景

計画レビュー（plan-review）ではタスク定義は見えるが、charter との整合・取りこぼし・
依存欠落を人が自力で見抜く必要がある。検収（delivery-review）では差分（何が変わったか）は
見やすくなった一方、変更意図（なぜ変えたか）の補助が無い。検収中にフォローアップ作業が
生まれることも多く、手動のタスク追加でも依存・優先度の調整が手探りになりやすい。

既存の読み取り専用 Doctor（失敗診断モード含む）と charter の JSON 下書き契約を拡張し、
**AI は下書き・助言のみ、確定は人のボタン**という護りを保ったまま補助する。

## 目的

1. plan-review で charter / 兄弟タスクと突き合わせた批評と推薦・差し戻し文面案を得る
2. 検収で差分の**変更意図**と acceptance 対応・承認推薦を得る
3. 検収中にフォローアップ backlog 案（title / verify / accept / after / priority）を得る
4. 手動タスク追加時に依存・優先度の提案と、既存 backlog への調整案を得る

## 採用設計

| 用途 | 入口 | モード | 出力 |
|------|------|--------|------|
| 計画批評 | 要対応カード「AIで計画を批評」 | `plan-critique`（Doctor） | Markdown（推薦・差し戻し文面案） |
| 変更理由 | 検収ダイアログ「変更理由を説明」 | `delivery-rationale`（Doctor） | Markdown（意図・acceptance・推薦） |
| フォローアップ案 | 検収ダイアログ「フォローアップ案」 | `followup-suggest`（構造化 Assist） | JSON → タスク追加フォームへ流し込み可 |
| 依存・優先度 | タスク追加「AIで依存・優先度を提案」 | `enqueue-assist`（構造化 Assist） | JSON → after/priority/note へ流し込み |

共通制約:

- CLI は Doctor と同じ読み取り専用起動（ファイル書き込み・状態遷移なし）
- 承認 / 差し戻し / 却下 / inbox 投入は既存の人操作ボタンのみ
- 差し戻し文面案は回答欄へコピーできるが、送信は人が押す

## コンテキスト

- **plan-critique**: 対象タスク、needs 本文、charter goal/acceptance、proposed 兄弟タスク
- **delivery-rationale**: タスク verify/accept、risk、差分要約（取得可能な範囲）、charter
- **followup-suggest**: 上記＋既存 backlog 要約（id/title/status/priority/after）
- **enqueue-assist**: 下書きフィールド＋既存 backlog 要約

## テスト

- プロンプト契約（見出し / JSON キー / 読み取り専用）
- UI 入口ボタンとモード起動
- JSON 正規化（after 配列・priority・suggestions）
- preload / IPC の Assist 経路
