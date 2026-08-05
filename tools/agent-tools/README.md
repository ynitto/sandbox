# agent-tools — 3 エンジンで共有するもの

> agent-* ファミリー全体（agent-dashboard を含む）が何のための道具かは
> [コンセプト正典](../../docs/designs/agent-tools-concept.md) が定める。
> このファミリーへ機能を足すときは、先にあちらの §7（作業ゲート）を通すこと。

`agent-project` / `agent-flow` / `agent-amigos` が**共通で使うもの**の置き場。
エンジン固有のものはここに置かない（各エンジンのディレクトリへ）。

```
tools/agent-tools/
  install.sh      # 3 エンジンをまとめて入れる唯一のインストーラ
  agentcore/      # 共通ライブラリ（transport / protocol / vocab / heartbeat）
```

## install.sh

```bash
bash tools/agent-tools/install.sh                       # 3 本すべて（推奨）
bash tools/agent-tools/install.sh --only agent-project  # 1 本だけ
bash tools/agent-tools/install.sh --prefix /usr/local/bin
bash tools/agent-tools/install.sh --service             # 常駐化（systemd user unit）も構成
```

**3 本を別々に入れない。** 同じ `agentcore` と契約バージョンを共有しているので、片方だけ
古いと状態の読み書きや仕事の受け渡しが噛み合わなくなる。更新もまとめて
（`git pull && bash tools/agent-tools/install.sh`）。

各エンジンの `install.sh` は、ここへ `--only <engine>` で委譲する薄いシム
（既存の手順書・`setup.sh`・自己更新の呼び出しパスを壊さないために残してある）。

導入の手順書は [`docs/guides/single-resident-setup.md`](../../docs/guides/single-resident-setup.md)。

## agentcore

転送（git の護り）・claim/lease・語彙・心拍を 1 実装に集約した共通ライブラリ（設計 P0）。
`promptcompose`（プロンプトキャッシュに適合する注入順の正規化・案 H）も agent-project /
agent-flow で共有する（設計: docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md §3）。

**独立配布しない内部モジュール**（設計 R10）。3 本はそれぞれ別の実行ファイルなので、
`install.sh` が**各 zipapp へ同梱する**——1 本だけ入れ直しても自己完結して動く。

開発木から直接実行するときは、各エンジンの `__init__.py` がこのディレクトリを `sys.path` へ
足して解決する（`tools/<engine>/<package>/__init__.py` から見て `../../agent-tools/agentcore`）。
zipapp では同梱物が先に解決されるので、その追加パスは存在しなくても無害に素通りする。

テストは `agentcore/tests/`:

```bash
cd tools/agent-tools/agentcore && python3 -m unittest discover -s tests
```

## 自己更新との関係

`agent-project` の自己更新は、リポジトリから**本体とこのディレクトリの両方**を
sparse-checkout してから `install.sh` を叩く（既定
`update_subdir: tools/agent-project tools/agent-tools`）。

cone mode の sparse-checkout は指定ディレクトリの兄弟を含まないので、**本体だけを指定すると
ここが取れず installer が必ず失敗する**（自己更新がサイレントに見送られ続ける）。
`update_subdir` を書き換えるときはここを外さないこと。
