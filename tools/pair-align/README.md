# pair-align

**実装のリポジトリと設計書のリポジトリを、交互に揃えていくためのステートマシン。**
片方を変更したら、もう片方を整合させるための依頼文（そのままエージェントに渡せるプロンプト）を作る。

> 設計: [`docs/designs/pair-align-design.md`](../../docs/designs/pair-align-design.md)

- 同じ定義を**両方のリポジトリに置く**。どちらの側か（実装 / 設計書）と相手のパスだけを設定で変える
- 実行は statemachine-use スキル（「pair_align のステートマシンを実行して」）。依存は python3 と git
- 相手のリポジトリを探すとき、[graphify](https://pypi.org/project/graphifyy/) の知識グラフがあれば使う。
  無ければ文字列検索だけで動く
- 相手のリポジトリには**書き込まない**。読むだけ

## 置き方

```bash
# 実装のリポジトリへ（相手 = 設計書）
python3 tools/pair-align/install.py ~/work/my-app --side impl --pair ../my-app-docs
# 設計書のリポジトリへ（相手 = 実装）
python3 tools/pair-align/install.py ~/work/my-app-docs --side design --pair ../my-app
```

`<リポジトリ>/.statemachine/pair_align/` に定義が入り、`pair.json` に設定が書かれる。
手で置くなら `machine/` の中身をそのフォルダへ写し、`pair.json` を直せばよい。

```json
{ "side": "impl", "pair_path": "../my-app-docs", "graphify": "auto" }
```

| 項目 | 値 |
|---|---|
| `side` | このリポジトリの側。`impl`（実装）か `design`（設計書） |
| `pair_path` | 相手のリポジトリのパス。相対パスはこのリポジトリのルートから。**置き場所が変わったらここを手で直す** |
| `graphify` | `auto`（あれば使う）か `off` |

作業ファイル・送り箱・状態は `<リポジトリ>/.pair-align/` に置く（インストーラーが `.gitignore` に足す）。
マシン自身が graphify の索引に入らないよう、`.graphifyignore` にも 1 行足す。
もう一度 `install.py` を実行すると定義とスクリプトだけが新しくなり、`pair.json` はそのまま残る。

## 使い方

1. 自分のリポジトリを変更してコミットする
2. エージェントに「pair_align のステートマシンを実行して」と頼む
3. 相手を直す必要があれば、依頼文が `.pair-align/outbox/<ID>.md` にでき、全文が表示される
4. 依頼文を相手のリポジトリのエージェントに渡す。相手の側でこのステートマシンを実行しても、
   未処理の依頼として同じ依頼文が示される
5. 相手の側で反映をコミットするとき、依頼文の末尾にある `Pair-Align: <ID>` の 1 行をコミットメッセージに付ける

5 の行が付いたコミットは、相手の側から見て「自分の変更」に数えない。反映がまた依頼になって
戻ってくることはない。反映が不要と判断したら、相手の側で `pair_align.py ack <ID>` を実行する。
反映と、それとは別の変更は**コミットを分ける**（行の付いたコミットは丸ごと数えないため）。

### ステートマシンの流れ

```
collect ─┬─ 相手からの依頼が残っている ─→ inbound_pending（先にそれを反映する）
         ├─ 伝える変更が無い ────────────→ no_changes
         └─ 変更がある ─→ assess ─┬─ 反映不要 ─→ record_skip ─→ skipped
                                   └─ 反映が要る ─→ locate ─→ compose ─→ record ─→ done
```

- **相手からの依頼が残っているうちは、自分の変更を送らない。** 両側が同時に食い違いを抱えないよう、
  依頼は交互に片付ける（反映せずに先へ進めるなら `ack`）
- 見るのは**前回からのコミット**。初回は直前の 1 コミットだけを見る。範囲を変えたいときは
  入力「この版からの変更を見る」にリビジョンを入れる。コミットしていない変更は含めない
- 依頼文の形（見出し・ID・完了の合図）は、書いたあとスクリプトが検査する。通らなければ書き直す

### 手で使うコマンド

ステートマシンを使わずに、スクリプトだけを呼んでもよい（リポジトリのルートで実行する）。

```bash
python3 .statemachine/pair_align/pair_align.py status          # 設定・基準点・未処理の依頼
python3 .statemachine/pair_align/pair_align.py collect         # 変更を集める（--since REV で範囲指定）
python3 .statemachine/pair_align/pair_align.py locate --term 語 # 相手側を探す（--refresh でグラフ更新）
python3 .statemachine/pair_align/pair_align.py ack <ID>        # 相手の依頼を反映不要として受け取る
```

## graphify を使う

相手のリポジトリで一度 graphify を実行して `graphify-out/graph.json` を作っておくと、
`locate` が語ごとに `graphify query` で関係するノード（見出し・関数・呼び出し元）をたどり、
候補のファイルを絞る。文字列検索も並べて行うので、グラフに入っていない文書も拾う。

- コードのグラフは `locate --refresh`（`graphify update`、LLM 不要）で更新できる
- 設計書の意味的な抽出（Markdown・図など）は graphify のスキルか `graphify extract` で作る
- graphify の導入はリポジトリの `install.py`（外部ツールのセットアップ）でも入る

## テスト

```bash
python -m unittest discover -s tools/pair-align/tests
```
