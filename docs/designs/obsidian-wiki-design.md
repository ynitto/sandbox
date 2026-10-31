# obsidian-use ナレッジ wiki とメモ収集フック 設計書

> 対象: `.github/skills/obsidian-use/`（`scripts/vault.py`・`scripts/obsidian_hook.py`・`scripts/hook_install.py`）
> 作成: 2026-10-31
> 参考: [agricidaniel/claude-obsidian](https://github.com/agricidaniel/claude-obsidian)（Obsidian CLI を読み取り・検索の経路にする作り）、
> wiki-use（Karpathy LLM Wiki パターン）、[moltbook-retro-design.md](./moltbook-retro-design.md)（セッションのフックで振り返らせる作り）

## TL;DR

- obsidian-use に、ボルトを知識 wiki として育てる操作（init / ingest / query / save / lint）を足す。中身の作り方は wiki-use と同じ。
- **検索に関わる管理はすべて Obsidian に任せる。** スキル側は索引・スコア・グラフ・`index.md`・`hot.md` を持たない。
  検索は Obsidian CLI の `search`、一覧と最近の更新は Bases（`index.base`）、リンク切れ・孤立は `unresolved` / `orphans`。
- moltbook-use と同じ作りのフックで、作業中の出来事を控え、ターンの終わりにエージェント自身に振り返らせて `vault.py memo` で書かせる。
  moltbook と違い、書き先は自分のボルトで、**プロジェクト・環境に固有のメモも残す**。

## 1. 目的と範囲

| 要件 | 満たし方 |
|---|---|
| wiki-use 相当の知識管理を Obsidian の中で | ボルトの中の 1 フォルダ（既定 `llm-wiki`）に、knowledge / projects / envs を置く |
| 検索の管理を Obsidian に寄せる | `vault.py` は `search` を CLI に渡すだけ。順位付け・別名の解決・リンクの追跡は Obsidian |
| ナレッジを自動で集めて溜める | フックが出来事を決定的に控え、出来事があったときだけ振り返りの 1 ターンを足す |
| プロジェクト・環境のメモを残す | `projects/<リポジトリ名>/`・`envs/<ホスト-OS>/` に分けて書く。開始時に題を数行だけ見せる |

範囲外: 埋め込みによる意味検索、索引の自前管理、会話ログの読み直し、ボルトの Git 管理、wiki-use の廃止（並存する）。

## 2. 決定表

| 論点 | 決定 |
|---|---|
| 置き場所 | `skill_configs.obsidian-use` に `vault` / `vault_name` / `wiki_dir`。ボルトの他のノートには触らない |
| 検索 | `obsidian vault=<名> search query=… path=<wiki_dir> limit=… format=json`。受けない CLI なら素の `search` にしてフォルダで絞る。CLI が使えなければ文字列一致（順位なし）に落ち、そう明示する |
| 一覧・最近 | `index.base`（Bases）。ビューは 最近 / 知識 / プロジェクト / 環境。手で更新する目録を持たない |
| 書き込み | ファイルへ直接（Obsidian が閉じていても書ける。Obsidian は変更を拾う）。題 = ファイル名、同じ題なら `## 追記` |
| メモの重複 | 同じフォルダでプロパティ `memo` が同じなら「起きた回」を足す（名前だけで判定。検索は使わない） |
| 秘密情報 | トークン・鍵・パスワードの形を見つけたら書かない（moltbook の privacy gate の秘密の型だけを持つ）。パス・リポジトリ名・ホスト名は書いてよい |
| フックの対象 | Copilot（アプリ・CLI）、kiro-cli v2 / v3、Claude Code |
| フックが呼ぶもの | ボルトのフォルダの一覧だけ。Obsidian CLI もネットワークも呼ばない（10 秒の上限と、Obsidian が閉じている場合のため） |

## 3. ボルトの構造

```
<vault>/<wiki_dir>/
├── knowledge/              type: concept|term|person|organization|product|topic
├── projects/<project>/     scope: project（project = git origin のリポジトリ名、無ければフォルダ名）
├── envs/<host-os>/         scope: env（ホスト名と OS。WSL は wsl）
├── meta/log.md
├── index.base
└── SCHEMA.md
```

wiki-use の `atoms/` と `topics/` の区別は、フォルダではなくプロパティ `type` で持つ（Bases がまとめる）。

## 4. wiki の操作

| 操作 | wiki-use | obsidian-use |
|---|---|---|
| 検索 | `wiki_query.py search`（自前のトークン化と重み付け、graphify） | `vault.py search`（Obsidian の検索） |
| つながり | graphify のグラフ | `obsidian backlinks` / `links` |
| 目録 | `index.md` を `update-index` で更新 | `index.base`（更新しない） |
| 最近 | `hot.md` を `update-hot` で更新 | `index.base` の「最近」ビュー（`file.mtime` 順） |
| 点検 | `wiki_lint.py`（自前のリンク解決） | `vault.py lint`（プロパティだけ自前、リンクは Obsidian） |

## 5. フック

moltbook-retro の §4〜§5 と同じ流れ（控える → 終わりに振り返らせる → 決まった引数で書かせる）。

| 出来事 | 拾い方 |
|---|---|
| ツールの失敗 | Copilot `postToolUseFailure` / Claude Code `PostToolUseFailure`（`--failed`）、kiro は `tool_response` の失敗 |
| 失敗からの回復 | 失敗した同じツールがあとで成功 |
| 言い直し・メモの依頼 | プロンプトの語（`memo_rules.json`） |
| まとまった作業 | ツール `work_tools` 回（既定 15）。1 セッション 1 回。失敗が無くても、そのプロジェクトで分かったことを残す機会にする |

| クライアント | 振り返りの出し方 |
|---|---|
| Copilot / Claude Code / kiro v3 | 終わりのフックで `{"decision": "block", "reason": …}`。`stop_hook_active` なら出さない |
| kiro v2 | 終わりで印を付け、次のプロンプトのフックで前置きする |

開始のフックは、このプロジェクトのメモ 5 件・この環境のメモ 2 件の題と要点を会話に足す
（Copilot は `additionalContext`、ほかは標準出力）。プロジェクトの文脈を次のセッションへ渡すのが目的で、検索ではない。

上限は moltbook と同じ考え方（指示 3 回 / セッション、メモ 5 件 / セッション、控え 20 件、7 日で消す）。
`vault.py memo --session` がセッションの控えに書いた件数を数え、上限に達したら指示を出さない。

moltbook のフックと並べて入れたときは、両方の指示が出うる。OBSIDIAN NOTE の手順書（`references/memo-hooks.md`）で、
一般的なことは Moltbook、プロジェクト・PC 固有のことは Obsidian と書き分け、同じことを両方に書かないと定める。

## 6. 導入

`hook_install.py install --client copilot|kiro|claude [--vault …]`。moltbook の `retro_install.py` と同じ手順で、

1. スキルを `<エージェントの置き場所>/skills/obsidian-use` に写す（フックはこの写しを指す）
2. `--vault` があれば `vault.py init` で骨組みと設定を作る
3. フックを足す
   - Copilot: `obsidian-memo` プラグインとマーケットプレイスを作る（アプリへの追加は利用者が行う）
   - kiro: 既定のエージェントに印付きのフックを追記。組み込みの既定なら `obsidian-memo` エージェントを作って既定にする。
     moltbook のエージェントが既定ならそこへ足す
   - Claude Code: `~/.claude/settings.json` の `hooks` に追記（最初の 1 回だけ控えを取る）
4. 点検（スキル・ボルト・Obsidian CLI・フックの有無）

印はコマンドに `obsidian_hook.py` を含むこと。外すときは印の付いたものだけを外し、作ったエージェントに他のフックが
残っていればエージェントは消さない。

## 7. 記憶レイヤとの関係

`.github/instructions/common.instructions.md` の 3 レイヤの振り分けは変えない。obsidian-use のフックを入れた環境では、
プロジェクト・環境に固有の運用知（ltm-use の典型例の一部）は、フックの指示に従って Obsidian に書き、ltm-use へは重複して保存しない。
wiki-use と obsidian-use の wiki は同じ役目なので、どちらか一方を使う。
