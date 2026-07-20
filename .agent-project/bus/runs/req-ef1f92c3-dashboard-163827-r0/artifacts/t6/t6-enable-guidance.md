# t6 成果 — 未結線時の有効化導線を README と同一文言にする

## 出典（原文確認済み）

`tools/agent-project/README.md:272-295`「一貫性ゲート（codd-gate 連携・オプション）」。
画面に写した記述は以下の 4 点で、すべて README の原文どおり。

| README 原文 | 行 |
|---|---|
| `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json'` | 276 |
| `intake_cmd: 'codd-gate tasks --debt --repos <root>/repos.json'` | 277 |
| `python3 codd_gate_regression.py --config <設定ファイル>`（この 1 キーだけを冪等 upsert、`--dry-run` で書かずに結果だけ、codd-gate 未検出なら何も書かない） | 279-283 |
| 「`intake_cmd` に対応する注入 CLI は無いので、こちらは yaml を直接編集する」 | 282 |
| 「`--config` は**既存の設定ファイル**を指すこと——無ければ…エラーで止まる」 | 284-285 |

## t5 実装からの差分（＝直した乖離）

`consistencyGateHtml` の `enable` ブロックのみ差し替え。バッジ・行・セクション構造は t5 のまま。

1. **書くべき行を出していなかった** — 「不足している行を追加します」だけで、何を書くかが画面に無かった。
   README の 2 行を `<pre class="mono">` にそのまま出す。**未結線のキーの行だけ**を出す
   （結線済みの行まで書けと言わない）。`<root>/repos.json` は README 同様プレースホルダのまま
   （root は結線判定に使っておらず、勝手に埋めると嘘になる）。
2. **`intake_cmd` が未結線のときに注入 CLI を勧めていた** — `codd_gate_regression.py` は
   `regression_cmd` の 1 キーしか書かない。CLI の提示条件を
   `configFile あり && regression_cmd が未結線` に限定し、`intake_cmd` 未結線時は
   README の「対応する注入 CLI は無いので yaml を直接編集する」を出す。
3. **設定ファイル未検出時のパスが `.agent/` だった** — README は `.agents/agent-project.yaml`。
   `src/base/main/agent-home.js:15-16` でも `.agents` が現行・`.agent` は legacy。`.agents/` へ統一。
   なお実在の設定を検出できた場合はその実パス（`gate.configFile`）を出すので、新旧どちらでも正しい。
4. 設定ファイル未検出時は**注入 CLI を勧めない**（README:284「`--config` は既存の設定ファイルを指すこと」）。
   開くボタンも従来どおり出さない。

## 描画結果（実物）

設定ファイル未検出・両方未結線:

```html
<span class="label-chip">有効化</span>
agent-project の設定ファイルが見つかりません。ワークスペース直下に
<span class="mono">.agents/agent-project.yaml</span> を作り、次の行を書く:
<pre class="mono">regression_cmd: 'codd-gate verify --base &quot;$KIRO_BASE_REV&quot; --repos &lt;root&gt;/repos.json'
intake_cmd: 'codd-gate tasks --debt --repos &lt;root&gt;/repos.json'</pre>
<p><code>intake_cmd</code> に対応する注入 CLI は無いので、こちらは yaml を直接編集する。</p>
```

`regression_cmd` のみ未結線・設定ファイルあり: 上の 1 行目だけ ＋
`python3 codd_gate_regression.py --config /ws/.agents/agent-project.yaml` ＋ `--dry-run` の案内
＋ 既存の `data-gate-open` ボタン。

## 乖離の再発防止

`test/consistency-gate-ui.test.js` に README 突き合わせを追加。
`tools/agent-project/README.md` からバッククォート内の `regression_cmd: '…'` / `intake_cmd: '…'` を
正規表現で抜き、`esc()` を通した文字列が描画結果に含まれることを assert する。
README 側の文言が変われば **テストが落ちて気づける**。
単体配布（agent-dashboard だけ取り出した場合）では README が無いので `fs.existsSync` でスキップする。

## CSS

追加ゼロ。`pre.mono` は `styles.css:1019-1026` に既存（背景・枠・`overflow-x: auto`・`white-space: pre-wrap`）。

## 申し送り

- `consistencyGateHtml` の自由変数は `esc` のみのまま（t5 の申し送りを維持）。
- 状態書換の経路は追加していない。画面は「設定ファイルを開く」と「人が打つコマンドの提示」に留まる。
