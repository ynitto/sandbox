【差別化の切り口】検証 CLI を識別せず、既存の完了前フックだけを表示する。

# dashboard プロジェクト共通チェック表示設計

## TL;DR

dashboard は `regression_cmd` の設定有無と値を表示する。コマンド内の CLI は解釈せず、未設定なら state repo が持つ共通チェックを案内する。設定やタスク状態は変更しない。

## 前提と完了条件

agent-project の完了前チェックは既存の `regression_cmd` が正典である。dashboard はこの契約の読み取り側に留まり、検証 CLI ごとの設定、検出器、状態モデルを持たない。

完了条件は、任意の `regression_cmd` を同じように表示でき、回帰失敗が done を止めた事実を要対応で確認できること。新しい検証 CLI を追加しても dashboard の変更は不要である。

## 範囲

対象は、概要での設定表示、未設定時の案内、要対応での回帰失敗表示である。コマンドの実行、設定の更新、CLI の存在確認、`intake_cmd` の表示、done の確定は対象外とする。

## 表示項目

| 配置 | 表示 | 根拠 |
|---|---|---|
| 概要「プロジェクト共通チェック」 | `設定済み` または `未設定`、現在のコマンド | `projectCheck.configured`、`projectCheck.command` |
| 同上 | 未設定時の `regression_cmd: ./tools/check` | state repo が所有する共通チェックの標準形 |
| 要対応 | 完了前の共通チェックが失敗し、done を止めたこと | `failure-phase=regression` |

表示はコマンド文字列をエスケープする。`codd-gate`、`make`、将来の CLI のいずれでも扱いを変えない。

## 未設定時の導線

自動探索した設定ファイルがある場合は、それを OS のエディタで開くボタンだけを出す。画面から設定を生成・更新する機能や専用の配線 CLI は設けない。

```yaml
regression_cmd: ./tools/check
```

`./tools/check` は state repo が所有する。検証 CLI の追加や順序変更はそのファイルだけで行う。

## done 不変条件

`regression_cmd` はタスク verify が通った後、done 確定前に agent-project が実行する。失敗時は agent-project が needs を生成する。dashboard は結果を読むだけで、approve、complete、status 更新を送らない。

## 公式契約の境界

| 公式入力 | dashboard の読取経路 | 許される解釈 |
|---|---|---|
| `agent-project.{yaml,yml,json}` | `readToolConfig()` → `projectCheckStatus()` → `readProject().projectCheck` | `regression_cmd` の有無と値 |
| `needs/<id>.md` の `failure-*` | `parseNeeds()` → `needFailureViewModel()` → `renderNeedFacts()` | producer が記録した失敗工程と要約 |

dashboard は CLI の種類、実在、バージョン、実行成功を判定しない。agent-project が明示 `--config` で使うパスは現契約にないため、自動探索候補との一致も断定しない。

## 主要判断と却下案

CLI ごとの結線判定は、新しい検証機を追加するたびにコードと学習項目が増えるため採らない。共通チェックの manifest や plugin registry も、1本のコマンドで足りる間は設けない。既存負債の自動投入は完了前チェックとは責務が異なるため、この表示へ混ぜない。

## テスト観点

1. 任意の `regression_cmd` を設定済みとして値とともに表示する。
2. 未設定と設定ファイル未検出を安全に表示する。
3. コマンド文字列を HTML として解釈しない。
4. `failure-phase=regression` だけを共通チェック失敗として表示する。
5. 表示操作が `openPath` 以外の書き込みを行わない。

## 未解決と範囲外

- 実効 `--config` パスの表示は、agent-project が公式契約へ公開した場合に検討する。
- 実行時間や検査別の内訳が必要になった場合は、共通チェックの標準出力ではなく、計測要件を先に定める。
