# ナレッジ wiki（Obsidian ボルトを知識ベースにする）

## 目次

- [考え方](#考え方)
- [置き場所と設定](#置き場所と設定)
- [wiki の構造](#wiki-の構造)
- [ノートの書き方](#ノートの書き方)
- [init — 骨組みを作る](#init--骨組みを作る)
- [ingest — ソースを取り込む](#ingest--ソースを取り込む)
- [query — wiki で答える](#query--wiki-で答える)
- [save — 答えを残す](#save--答えを残す)
- [lint — 点検する](#lint--点検する)
- [Obsidian CLI で直接引くもの](#obsidian-cli-で直接引くもの)

---

## 考え方

wiki-use（Karpathy LLM Wiki パターン）と同じく、エージェントがソースを読んで知識ノートを作り・育てる。
違いは **検索に関わる管理をすべて Obsidian に任せる** こと（[claude-obsidian](https://github.com/agricidaniel/claude-obsidian) の
「Obsidian CLI を読み取り・検索の経路にし、使えないときだけファイルを直接読む」に倣う）。

| 役目 | 誰がするか |
|---|---|
| 検索・タグ・別名の解決 | Obsidian（公式 CLI の `search` / `tags`） |
| 一覧・最近の更新・絞り込み | Obsidian の Bases（`index.base`。`index.md` や `hot.md` を手で更新しない） |
| リンク・バックリンク・リンクしていない言及・リンク切れ・孤立 | `vault.py links` / `lint`（その場でファイルを読む。Obsidian が閉じていても動く） |
| ノートの中身を書く・つなぐ | エージェント（`vault.py note` / `memo`、`[[題]]` のリンク） |
| 操作ログ | `vault.py log`（`meta/log.md` に追記） |

`vault.py` は索引・スコア・グラフを持たない。Obsidian が閉じているときだけ、`search` は素の文字列一致
（語がすべて含まれるノート。順位なし）に落ち、そのことを 1 行で知らせる。`[[…]]` は Obsidian と同じく
名前（大文字小文字は区別しない）でボルト全体から引く。

## 置き場所と設定

`<エージェントの置き場所>/skill-registry.json` の `skill_configs.obsidian-use`:

```json
{ "skill_configs": { "obsidian-use": {
  "vault": "~/Documents/Obsidian/Main", "vault_name": "Main", "wiki_dir": "llm-wiki" } } }
```

- `vault`: ボルトのフォルダ。`vault_name`: Obsidian に登録したボルト名（CLI の `vault=` に渡す。既定はフォルダ名）
- `wiki_dir`: ボルトの中の wiki のフォルダ（既定 `llm-wiki`）。ボルトの他のノートには触らない
- 確認: `python scripts/vault.py config`（プロジェクト名・環境名・Obsidian CLI が使えるかも出る）

## wiki の構造

```
<vault>/<wiki_dir>/
├── knowledge/             世界・ドメインの知識（type: concept|term|person|organization|product|topic）
├── projects/<プロジェクト>/ そのプロジェクトだけで通じるメモ（フックが集める。手で書いてもよい）
├── envs/<ホスト-OS>/       その PC・OS・ツールの版だけで起きること
├── meta/log.md            操作ログ（追記だけ）
├── index.base             一覧（最近 / 知識 / プロジェクト / 環境 のビュー）
└── SCHEMA.md              この wiki の決まり（利用者と育てる）
```

- プロジェクト名は git の origin のリポジトリ名（無ければ作業フォルダ名）、環境名は `ホスト名-OS`（WSL は `wsl`）。
- 知識（knowledge）は「何か・誰か・どう関係するか」。プロジェクト・環境のメモは「ここではどうするか」。

## ノートの書き方

- **題 = ファイル名**。リンクは `[[題]]`、見出しへは `[[題#見出し]]`。記法は [obsidian-markdown.md](obsidian-markdown.md)
- プロパティ（`vault.py` が書く）:

```yaml
title: 注意機構
type: concept            # knowledge は type 必須。メモは memo
scope: knowledge         # knowledge | project | env
aliases: [attention, self-attention, アテンション]   # 別名・訳語・略称（Obsidian の検索と補完が当たる）
tags: [deep-learning]
sources: ["https://arxiv.org/abs/1706.03762"]
created: 2026-10-31
updated: 2026-10-31
summary: 系列の各位置が他の位置へ重みを付けて情報を集める仕組み
```

- 日本語の題には英語表記を、英語の題には訳語・カナを `aliases` に入れる（表記ゆれは Obsidian の検索に任せる）。
- 関係するノートは本文の `## 関連` に `[[題]]` で並べる。外部ソースの出典は本文の末尾に `*出典: … / 発行: YYYY-MM-DD*`。
- 1 ノート 1 トピック。複数の知識を横断するまとめは `type: topic` にする。

## init — 骨組みを作る

```bash
python scripts/vault.py init --vault ~/Documents/Obsidian/Main [--wiki-dir llm-wiki] [--vault-name Main]
```

既にあるファイル（育てた `SCHEMA.md` など）は上書きしない。フックも入れるなら [memo-hooks.md](memo-hooks.md) の
インストーラが同じことをする。Obsidian CLI は Obsidian 1.12 以降の 設定 → 一般 → コマンドラインインターフェース で有効にする。

## ingest — ソースを取り込む

1. **取り込み済みか確かめる**: `python scripts/vault.py ingested "<URL かファイル>"`（`sources` で引く）
   - 当たった → そのノートを読み（`obsidian read path=…` か Read）、足りない分だけ追記する
2. **ソースを Markdown にする**: URL・HTML は defuddle、Office・PDF は markitdown（SKILL.md の表）
3. **知識を切り出す**: 1 ソースから 3〜10 件。それぞれ既存ノートを `search` で確かめ、あれば追記、無ければ作る

```bash
python scripts/vault.py note --scope knowledge --type concept --title "注意機構" \
  --aliases "attention,self-attention" --sources "https://…" --summary "1 文" --body-file draft.md
```

   同じ題のノートがあれば `## 追記 <日付>` として足し、`aliases` / `sources` を合わせる。
4. **つなぐ**: `python scripts/vault.py links "<題>"` の「リンクしていない言及」（題や別名を書いているのに
   リンクしていないノート）に `[[題]]` を足す。新しいノートの `## 関連` にも、つながる既存ノートを並べる
5. ログは `note` が自動で書く（`meta/log.md`。`--sources` を渡せば出典も残る）。一覧は Bases が勝手に追う
6. 区切りの終わりに `vault.py lint` を 1 回回し、出たリンク切れ・孤立だけは直してから次へ進む

大量のファイルは 5 件ずつ区切り、区切りごとに 1〜4 を回す（先読みしない）。

## query — wiki で答える

ユーザーの質問に答える前に、関係しそうなら wiki を先に引く:

```bash
python scripts/vault.py search "<語>"                 # wiki 全体（Obsidian の検索）
python scripts/vault.py search "<語>" --scope project # このプロジェクトのメモだけ
```

- 当たったノートを読み、`[[題#見出し]]` で出典を示して答える。wiki の根拠と自分の推測は分けて書く
- 手がかりが弱いときは、当たったノートを `vault.py links "<題>"` で見て、出ていくリンクと入ってくるリンクを
  1〜2 段たどる
- 語を変えても当たらない → wiki に無い。答えたあと、残す価値があれば ingest / save する
- moltbook-use が有効なら、共有の知見も `moltbook.py search --query …` で補う（出典は「Moltbook（共有）」）

## save — 答えを残す

複数のノートを横断した比較・まとめ・新しく見つけたつながりは、会話に埋もれる前に残す:

```bash
python scripts/vault.py note --scope knowledge --type topic --title "<まとめの題>" --body-file answer.md \
  --summary "1 文"
```

利用者が「残さなくていい」と言ったら残さない。

## lint — 点検する

```bash
python scripts/vault.py lint
```

1. **機械で分かること**（`vault.py lint`。Obsidian が閉じていても動く）
   - プロパティの欠け（`title` / `type` / `updated`、knowledge は `summary` も）、決まりにない `type`、題とファイル名の違い
   - 中身がほとんど無いノート
   - リンク切れ（wiki のノートから、ボルトに無い名前への `[[…]]`）
   - 孤立ノート（knowledge のうち、ボルトのどこからもリンクされていないもの。メモは対象外）
   - 同じ名前を持つノート（題や `aliases` が重なる knowledge。統合の候補）
2. **読んで分かること**（エージェント。ノートが多ければ最近更新したものから）
   - 同じ主題のノート同士で事実が食い違っていないか
   - 新しいソースが古い記述を覆していないか（覆していれば `> [!warning] YYYY-MM-DD 時点の情報` を付ける）
   - 重なりの候補は 1 つにまとめ、残す側の `aliases` に寄せて、消す側へのリンクを張り替える

直すのはリンクを足す・ノートを作る・統合するまで。ノートを消すときは利用者に確かめる。
直したら `vault.py log "lint の指摘を直した"`。

## Obsidian CLI で直接引くもの

`vault.py` が Obsidian に渡すのは `search` だけ。ほかは Obsidian CLI を直接使う（[obsidian-cli.md](obsidian-cli.md)）。
どれも先頭に `vault="<vault_name>"` を付ける。

```bash
obsidian vault="Main" read path="llm-wiki/knowledge/注意機構.md"
obsidian vault="Main" tags sort=count counts
obsidian vault="Main" search:context query="attention" path="llm-wiki"
```

CLI が使えないとき（`vault.py config` の `obsidian` が「使えない」）は、ファイルを直接読む。
