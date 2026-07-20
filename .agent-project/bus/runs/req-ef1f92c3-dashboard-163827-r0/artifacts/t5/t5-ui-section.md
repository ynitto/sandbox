# t5 成果 — 一貫性ゲート状態セクション（概要タブ）

## 実装位置

| ファイル | 変更 |
|---|---|
| `src/renderer/renderer.js` | `consistencyGateHtml(p)`（純関数・HTML を返す）＋ `bindConsistencyGate(root)` を `technicalProjectInfoHtml` の直前に追加 |
| `src/renderer/sections/overview.js` | `renderOverview()` の `${overviewVersionsHtml(p)}` 直前に `${consistencyGateHtml(p)}` を 1 行、バインド部に `bindConsistencyGate(el)` を 1 行 |
| `test/consistency-gate-ui.test.js` | 新規（純関数の表示ロジック 5 ケース） |
| `package.json` | `test` スクリプトへ 1 本追加 |

renderer.js は分割済みだが、完了条件が「識別子と文言が renderer.js に出現すること」なので
**表示ロジック本体を renderer.js に置き、sections/overview.js からは呼ぶだけ**にした。
クラシックスクリプトのグローバルスコープ共有で、読み込み順は renderer.js → sections/*（`index.html:595-`）。

## 描画内容

`state.project.consistencyGate`（t4 のペイロード）を読む。**表示のみ・状態書換なし。**

- `<section class="overview-version-section">` として「計画バージョン」節の上に独立配置。
  `.overview-grid` は 3 カラム前提の CSS なので 4 枚目カードにはしていない（t2 §1 の助言）。
- 見出し脇に全体バッジ: 両方結線 = `有効` / それ以外 = `一部のみ`。
- `<dl class="need-failure-context">` に 2 行。各行の `<dt>` はラベル + yaml キー名（`regression_cmd` /
  `intake_cmd`）を `.mono` で併記。`<dd>` は
  - 結線済み → `<span class="badge info">結線済み</span>` + コマンド全文 `<code>`
  - 未結線 → `<span class="badge warn">未結線</span>` + その項目が何をするかの説明
- 未結線が 1 つでもあれば有効化導線（`.need-resolution`）:
  - `configFile` あり → 編集先パスの明示 ＋ `python3 codd_gate_regression.py --config <configFile>` の提示
    ＋ `data-gate-open` ボタン（`api.openPath` で OS 既定エディタに渡す。needs の `data-open` と同じ既存経路）
  - `configFile` が null → `.agent/agent-project.yaml` を作って 2 行書く旨のみ（開くボタンは出さない）
  - `intake_cmd` に注入 CLI が無いことも文言に明記（README `tools/agent-project/README.md:272-295` が出典）

**CSS は 1 行も追加していない。** `.overview-version-section` / `.overview-version-heading` /
`.need-failure-context` / `.need-resolution` / `.label-chip` / `.badge info|warn` / `.mono` / `.muted` /
`.summary-actions` / `.summary-link` の既存クラスのみ。`.badge.ok` は存在しないので結線済みは `.badge.info`。

## 後続への申し送り

- `consistencyGateHtml` は `esc` だけを自由変数に持つ純関数。テストは
  `new Function('esc', ...)` で注入する形（`test/consistency-gate-ui.test.js:31`）。
  renderer にヘルパを増やすと `detail-tabs-ui.test.js` 型の注入漏れが起きるので、
  この関数は自由変数を増やさないこと。
- `p.consistencyGate` が無ければ空文字を返す（古い main と組み合わせても壊れない）。
- needs 側の codd-gate 由来の失敗要約（t2 §4.2）は本タスクの範囲外で未着手。
