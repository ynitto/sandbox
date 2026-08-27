# 用途コマンドの宣言（種別 C）

`/verify` `/edit` のような**用途の 1 語 → 起動形**の束縛を 1 枚の md で宣言します。
設計: [2026-08-27 クラウド CLI を正とした入口の再構成](../docs/plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md) §3.3。
規約の正典: [agent-cli 仕様書](../docs/specs/agent-cli-spec.md) §8。

## 置き場と探索順

先勝ちです（上位に置けば同梱の宣言を上書きできます）。

1. `$AGENT_COMMANDS_DIR`（`:` 区切りで複数）
2. `<プロジェクト>/.agents/commands/`
3. `~/.agents/commands/`
4. 同梱（このディレクトリ。`tools/agent-tools/install.sh` が 3 へ配ります）

**名前空間はスキル・スラッシュコマンドと 1 つ**です。同じ名前の宣言とスキルを両方
置かないでください（先に見つかった方が勝ち、もう片方は黙って効かなくなります）。

## 書き方

frontmatter は**平らな `key: value` だけ**です（agentcore は標準ライブラリだけで動く
必要があるので、YAML の全文法は受けません）。本文はそのままシステムプロンプトになります。

| キー | 意味 |
|---|---|
| `description` | `/help` と補完に出る 1 行 |
| `agent` | 起動形（旧 `variants.<用途>` の宛先）。宣言した定義の `variants` はさらに引かれる |
| `model` | 用途専用の既定。**人の明示と用途別順位表（実測）には負ける** |
| `tools` | ツールセットを 1 つだけ。`[]`＝道具なし / `[read]` / `[bash]` |
| `output` | 出力契約（`json` 等） |
| `argument-hint` | `/help` の左列に出る引数の型 |

```markdown
---
description: 受入条件を読み取り専用で判定する
agent: ollama
model: gemma4:12b
tools: []
output: json
argument-hint: "[基準ファイル]"
---
あなたは判定役です。作業した本人ではありません。
観測できたものだけで判定し、確かめられないものは fail としてください。
```

## 同梱しているもの

`edit.md` の 1 枚だけです。**aider の名前が出るのはここだけ**で、編集適用の実装を
差し替える変更は将来この 1 行で済みます（設計 §3.6）。

上の `verify.md` は**例であって同梱していません**。同梱すると `agent: ollama` が
base の CLI（claude / kiro …）を問わず効いてしまい、いまは定義ごとの `variants` が
決めている振り替え先を、置いただけで全員ぶん奪うことになります。使いたい人が
`~/.agents/commands/verify.md` へ置いてください。
