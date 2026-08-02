# agent-loop event hook ack 設計

## 結論

L3 のイベント欠落は、event hook の結果を webhook 用キューへ統合せず、hook の既読確定を
配送成功後まで遅延する ack 方式で修正する。

現在のキュー統合案は、`fresh_context` の消失、同名 entry 間の誤配送、enqueue 失敗後の
イベント欠落を生むため撤回する。ただし `_dispatch_prompt()` が送信成否を返す変更は、ack の
判定に必要なので維持する。

## 不変条件

1. event hook の更新は送信成功後だけ既読になる。
2. session 準備、slot 取得、`/clear`、prompt 送信のいずれかが失敗したら ack しない。
3. ack 前のプロセス停止は欠落ではなく再配送へ倒す（at-least-once）。
4. 通常 schedule と event hook の `fresh_context` 契約を維持する。
5. webhook の既存キュー、名前解決、配送挙動を変更しない。
6. 同時更新は最新の1件を配送・ack し、残りを後続 check へ残す。

## コンポーネント

### GitLab issue hook

- `check()` は選択した更新に対応する次状態をメモリ上へ保留し、prompt を返す。
- `ack()` は保留状態を状態ファイルへ保存して解除する。
- ack されずに再度 `check()` された場合は、状態ファイルが未更新なので同じイベントを再検出する。
- 更新なし、または fallback の場合は保留 ack を作らない。

### Scheduler

- event hook は従来どおり通常の schedule 配送経路を通す。
- `_dispatch_prompt()` の返値が成功の場合だけ、hook に `ack()` があれば呼ぶ。
- `ack()` を持たない既存 hook は従来互換としてそのまま動かす。
- event hook を webhook の外部キューへ enqueue する変更は戻す。

## 失敗時の動作

| 状況 | 配送 | ack | 次回 |
|---|---|---|---|
| session 準備失敗 | なし | なし | 同じ更新を再検出 |
| slot 取得失敗 | なし | なし | 同じ更新を再検出 |
| `/clear` 失敗 | なし | なし | 同じ更新を再検出 |
| prompt 送信失敗 | 失敗 | なし | 同じ更新を再検出 |
| prompt 送信成功 | 成功 | あり | 次の未処理更新へ進む |
| 送信成功後・ack 前に停止 | 成功済み | なし | 重複配送し得るが欠落しない |

## テスト

1. 同時更新2件を、成功した2回の check / dispatch / ack で順に処理する。
2. slot 拒否後は ack せず、次回に同じイベントを再検出する。
3. prompt 送信失敗後は ack せず、成功後だけ既読になる。
4. `fresh_context: true` の event hook が `/clear` を維持する。
5. 同名 entry があっても event hook の prompt を別 entry へ送らない。
6. ack 非対応の既存 hook が引き続き動く。
7. agent-loop のツール単位テストを実行する。

## 監査文書

L3 は ack 方式のテスト成功後に修正済みとする。FL3 で解消済みの m6 / m7 にも修正済み表示を付け、
§7 の FL3 行へ対応関係を追記する。

## 実装計画

1. 現在の event hook → webhook キュー統合を戻す。
2. GitLab issue hook に保留状態と `ack()` を追加する。
3. scheduler で送信成功後だけ optional `ack()` を呼ぶ。
4. 上記の失敗・互換ケースを回帰テストへ置き換える。
5. 監査文書の L3 / m6 / m7 と優先リストを同期する。
6. agent-loop 全テストと `git diff --check` を実行する。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-08-02 |
| 決定者 | nitto |
| 採用案 | 配送成功後 ack 方式 |
| 却下案 | webhook キュー拡張（配送ポリシーと名前境界を混同）、L3 全戻し（イベント欠落が再発） |
| 主な理由 | 既存の event hook 配送契約を維持しながら、失敗を欠落ではなく再配送へ倒せるため |
| トレードオフ | 送信成功後・ack 前の停止では重複配送し得る |
| 再評価条件 | hook を複数 scheduler thread から並行実行する、または exactly-once 配送が必要になったとき |
