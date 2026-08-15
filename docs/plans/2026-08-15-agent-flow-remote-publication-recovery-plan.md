# agent-flow リモート公開保証と緊急復旧 実装計画

> 設計: `docs/plans/2026-08-15-agent-flow-remote-publication-recovery-design.md`
>
> 実装は TDD で、公開先契約 → 成果保全 → 強制復旧 → Dashboard 表示の順に進める。各段階で失敗テストを確認してから最小実装を加える。

## 完了条件

- Dashboard の Git workspace が `{url: remote URL, local: Git top-level}` を agent-flow へ渡す。
- agent-flow は remote push 成功または変更なしの場合だけ通常完了する。
- remote push 失敗時、期待 commit と `refs/agent-flow/recovery/<run-id>` がローカルに残る。
- `agent-flow force-complete` は手動 push 済みの commit を remote で検証し、publication failure だけを修復する。
- 後続 node が残る run は再開され、未実行処理を飛ばして `done` にならない。
- Dashboard は実データに基づく保存・公開状態を控えめに表示し、失敗時だけ復旧操作を出す。

## Phase 0: 回帰基準を固定

対象:

- `tools/agent-flow/tests/test_workspace.py`
- `tools/agent-flow/tests/test_bus.py`
- `tools/agent-dashboard/test/adhoc-flow.test.js`

作業:

1. 既存の workspace、bus、adhoc-flow テストを個別実行し、変更前の基準を記録する。
2. テスト用 Git は一時ディレクトリ内の working repository と bare remote だけで構成し、ネットワークへ依存させない。
3. 新規 public helper は正常、入力不正、Git 失敗、境界状態の branch をすべてテスト対象にする。

確認:

```bash
python3 -m unittest tools/agent-flow/tests/test_workspace.py tools/agent-flow/tests/test_bus.py
node tools/agent-dashboard/test/adhoc-flow.test.js
```

## Phase 1: Dashboard の workspace 契約を修正

対象:

- `tools/agent-dashboard/test/adhoc-flow.test.js`
- `tools/agent-dashboard/src/features/adhoc-flow/main/adhoc.js`

RED:

1. `gitWorkspace` が Git top-level、現在 base、`remote.origin.url` を解決し、`{url, local, base, path, desc}` を返すテストを追加する。
2. remote 未設定・空 URL・Git 外の cwd を fail-close するテストを追加する。
3. Windows 入力でも `local` は worker が使える WSL 側 top-level になることを固定する。

GREEN:

1. 既存の単一 shell 呼び出しで `git rev-parse`、base、`git remote get-url origin` を取得する。
2. `url` に remote URL、`local` に top-level を設定する。
3. `submit` の戻り値で branch を推測して主要結果に使わず、run result の publication を待つ。

## Phase 2: publication と recovery ref を実装

対象:

- `tools/agent-flow/tests/test_workspace.py`
- `tools/agent-flow/agent_flow/workspace.py`
- `tools/agent-flow/agent_flow/work.py`

RED:

1. commit 後、remote push 前に `refs/agent-flow/recovery/<run-id>` が `workspace.local` に作られるテストを追加する。
2. push 成功時、delivery に `publication.state=published`、URL、branch、commit、時刻が入るテストを追加する。
3. push 失敗時、例外の `data.publication` に expected commit、エラー、recovery repository/ref が残るテストを追加する。
4. push 成功時は recovery ref が消え、push 失敗時は残ることを固定する。
5. recovery ref を作れない場合、remote push を試行せず失敗することを固定する。

GREEN:

1. `WorkspacePublishError` を追加し、worker の既存 structured exception data 経路へ publication failure を渡す。
2. run-id を hidden ref に安全変換する helper と、local repository へ HEAD を転送する helper を追加する。
3. stale push の rebase 後にも recovery ref を最新 HEAD へ更新する。
4. 既存 delivery フィールドを保ち、その内側へ publication を追加して互換性を維持する。
5. Git エラー出力に資格情報を含めない既存 transport/hardening を再利用する。

## Phase 3: publication failure の run 状態を固定

対象:

- `tools/agent-flow/tests/test_e2e.py`
- `tools/agent-flow/tests/test_bus.py`
- `tools/agent-flow/agent_flow/work.py`
- `tools/agent-flow/agent_flow/orchestrate.py`

RED:

1. publication failure の node result が `failed` となり、run が `done` にならない結合テストを追加する。
2. result の `error_class` と `data.publication` が文字列解析なしで取得できることを固定する。
3. 変更なしの run は `not-required` 相当として正常終了し、不要な branch/ref を作らないことを固定する。

GREEN:

1. publication failure を固有 error class として分類する。
2. finalization は publication failure を通常の失敗経路へ流し、構造化 data を保存する。
3. status/result 読み手が publication を集約できる最小 helper を追加する。

## Phase 4: remote 検証付き `force-complete`

対象:

- `tools/agent-flow/tests/test_recovery.py`（新規）
- `tools/agent-flow/tests/test_bus.py`
- `tools/agent-flow/agent_flow/recovery.py`（新規）
- `tools/agent-flow/agent_flow/bus.py`
- `tools/agent-flow/agent_flow/__init__.py`
- `tools/agent-flow/agent_flow/cli.py`

RED:

1. run 不在、非 failed、空理由、通常失敗、publication 情報欠落を拒否するテストを追加する。
2. remote branch 不在、fetch 失敗、expected commit 不一致を拒否するテストを追加する。
3. remote tip が expected commit と同一、またはその子孫の場合だけ受理するテストを追加する。
4. publication failure と別の failed node が混在する場合は拒否する。
5. 受理時に node result、publication state、監査 event、meta が一貫して更新されることを固定する。
6. 未実行の後続 node があれば `running` に戻り、全 node が終端済みの場合だけ `done` になることを固定する。
7. 同じコマンドを再送しても二重監査や破壊的更新を起こさないことを固定する。

GREEN:

1. `Bus` に publication failure 専用の検査・修復メソッドを追加する。汎用 `set_status` の終端保護は緩めない。
2. recovery helper で remote branch を fetch し、`merge-base --is-ancestor expected FETCH_HEAD` を検証する。
3. `cmd_force_complete` は reason 必須、構造化 JSON を内部結果として返し、CLI では短い復旧結果を表示する。
4. CLI parser に `force-complete <run-id> --reason` を追加し、`recovery.py` を fragment 順へ登録する。
5. 後続 node がある場合は既存 run 再開経路へ接続し、無い場合だけ final result を再生成する。

## Phase 5: Dashboard の復旧 IPC

対象:

- `tools/agent-dashboard/test/adhoc-flow.test.js`
- `tools/agent-dashboard/test/feature-split.test.js`
- `tools/agent-dashboard/src/features/adhoc-flow/main/adhoc.js`
- `tools/agent-dashboard/src/features/adhoc-flow/main/ipc.js`
- `tools/agent-dashboard/src/features/adhoc-flow/preload.js`

RED:

1. Dashboard が agent-flow CLI の `force-complete` を呼び、bus、run-id、reason を安全に引用するテストを追加する。
2. 空理由と publication failure 以外の run では IPC を拒否するテストを追加する。
3. CLI の remote 検証エラーを短い利用者向けエラーへ変換し、詳細を失わないテストを追加する。

GREEN:

1. `buildForceCompleteLine` と `forceComplete` を main 側へ追加する。
2. `adhocFlow:forceComplete` IPC と preload API を追加する。
3. Dashboard 側で Git 状態を書き換えず、agent-flow CLI を唯一の復旧所有者にする。

## Phase 6: 保存・公開状態を控えめに表示

対象:

- `tools/agent-dashboard/test/adhoc-flow.test.js`
- `tools/agent-dashboard/test/workflow-publication-ui.test.js`（新規）
- `tools/agent-dashboard/src/renderer/features/adhoc-flow.js`
- `tools/agent-dashboard/src/renderer/styles.css`

RED:

1. `published`、`published-manually`、`failed`、`not-required`、`unknown` の view model と表示文言を固定する。
2. branch と commit が publication/delivery 由来であり、`af/<run-id>` を組み立てないことを固定する。
3. 通常時はメタ情報行と閉じた details だけ、失敗時だけ復旧説明と緊急操作が出ることを固定する。
4. 強制復旧で理由入力と確認を必須にし、成功後に run detail を再取得することを固定する。

GREEN:

1. publication を集約する純粋 view-model helper を renderer に追加する。
2. 結果見出し直下へ `保存: ... · 公開: ...` の subdued 行を追加する。
3. 「保存と公開の詳細」に URL、local path、完全 SHA、recovery ref、手動 push コマンドを置く。
4. `failed` の場合だけ確認ダイアログ経由の force-complete ボタンを表示する。
5. warning は色だけに依存せず文言と `aria-live` で伝える。

## Phase 7: GC・文書・全体検証

対象:

- `tools/agent-flow/agent_flow/cleanup.py`
- `tools/agent-flow/tests/test_recovery.py`
- `tools/agent-flow/README.md`
- `tools/agent-dashboard/README.md`

作業:

1. run GC 時に対応する recovery ref を削除し、別 run の ref を触らないテストを追加する。
2. CLI の通常公開、手動 push、force-complete、拒否条件を README に追記する。
3. 対象テスト、agent-flow 全テスト、Dashboard 全テスト、lint を順に実行する。
4. 一時 bare remote を使う結合テストで、Dashboard workspace 契約から remote branch 作成までを確認する。
5. 実在 GitHub への push は自動テストで行わず、最終手動確認では専用の disposable branch だけを使う。

最終確認:

```bash
python3 -m unittest discover -s tools/agent-flow/tests
npm test --prefix tools/agent-dashboard
npm run lint --prefix tools/agent-dashboard
```

## 実装順の停止条件

- recovery ref を push より前に作れない場合は Phase 2 で停止し、成果保全方式を再設計する。
- `force-complete` が publication failure 以外を成功へ変え得る場合は Phase 4 で停止する。
- Dashboard が result なしで公開済みを推測する場合は Phase 6 を完了扱いにしない。
- 全テスト中に既存の汎用 `set_status` の終端保護を弱める必要が生じた場合は、その変更を採用せず専用遷移へ戻す。
