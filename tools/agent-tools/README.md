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

## agent-ollama — クラウド CLI が使えないときのバックアップ実行系

`install.sh` は 4 エンジンのほかに `agent-ollama`（zipapp・1 ファイル）も置く。**クラウドの
エージェント CLI がガバナンスや予算の事情で使えなくなったときに、agent-tools の作業を
止めないため**の実行系である（設計:
[2026-08-06 の対策案](../../docs/plans/2026-08-06-opencode-ollama-cpu-inference-proposals.md) §0.1・案 F-2）。
**速度と品質は犠牲にしてよい。その代わり「契約に完全適合すること」と「止まっていないことを
示せること」を要件にしている。**

```bash
echo '要件を3行で要約して' | agent-ollama --think off qwen3          # 単発（ツールなし）
echo 'README の誤字を直して' | agent-ollama --think off qwen3 --tools # 実行ループ（bash 1 つ）
agent-ollama --tui qwen3            # デバッグ用の対話ビュー（tmux から操作できる）
agent-ollama --follow               # 走っている実行のログへ後からアタッチする
agent-ollama --status               # いまの進捗を 1 行 JSON で返す（外部監視向け）
```

| モード | 契約上の位置 | 何ができるか |
|---|---|---|
| 既定 | `readonly: enforced` | text → text のみ。ファイルもコマンドも触れない |
| `--tools` | `write_args` | bash 1 つを道具にした最小ループ |
| `--tui` | `interactive` | 進捗を見ながら手で叩く（agent-dashboard の対話診断・agent-loop から） |

ツールとループが `--tools`（書き込みモード）でだけ生えるのが要点。読み取り専用モードには
道具が 1 つも無いので、`readonly: enforced` の宣言が嘘にならない。

### 「遅い」と「死んだ」を区別する

このバックアップ運転では **1 呼び出しが数十分になるのが正常**である。だから壁時計で
打ち切ると正常な実行を殺す。代わりに次の三層を持つ:

1. **進捗ログ（JSONL・追記のみ）**。`~/.agents/logs/ollama/` に、ラウンド遷移・
   トークン速度・ツール実行と、**沈黙中の heartbeat**（`phase=prefill` と待ち秒数）を書く。
   これが「その時刻に生きていた」ことの事後証明になる。
2. **`--status` / `--follow`**。プログラムは前者（`state` / `alive` /
   `since_last_progress_sec` / `tokens_per_sec`）、人は後者で同じものを見る。
   `state=running` かつ `alive=true` なら、長い沈黙は「遅い」であって「固まった」ではない。
3. **`--stall-timeout`（既定 180 秒）**。生成が始まった後の**無進捗**だけを打ち切り、
   `transient` 分類で返してエンジンのリトライ層に拾わせる。
   **最初のトークンまでの待ちは既定で無制限**（`--first-token-timeout 0`）——CPU 推論では
   prefill だけで 10 分かかるのが普通で、ここに上限を置くと正常な実行が死ぬ。

エンジン側は壁時計の上限を大きく取り（例 `agent_timeout: 3600`）、実質の検知器を
`--stall-timeout` に任せるとよい。壁時計は「無限ハング時の最後の砦」として残す。

### think・スキル・rich

- **`--think on|off`** は CLI オプション（API の `think` フィールドへ直結）。既定は
  `AGENT_OLLAMA_THINK` → モデル既定。プロンプトへ `/no_think` を混ぜる方式は採らない
  （モデル依存で、成果物本文へ漏れる事故がある）。`agents/ollama.json` は `--think off` を
  焼き込んであるので、エンジン側は何も知らなくてよい。
- **スキルは明示したものだけを遅延で読む**。`--skill <名前>`（プログラム経路）と、
  プロンプト**先頭ブロックのスラッシュ行** `/<名前> [引数]`（人手経路）の 2 形態だけに
  反応する。カタログは LLM へ見せないので、**使わないときの追加コストは 0**。
  読む先は `install.py` の配布先（`~/.agents/skills` / `~/.claude/skills`）そのまま。
- **rich は任意**。`install.sh --with-rich` で zipapp へ同梱すると TUI に色が付く。
  無くても素の ANSI で同じ情報を出す（既定はネットワーク不要のまま）。

環境変数: `OLLAMA_HOST` / `AGENT_OLLAMA_THINK` / `AGENT_OLLAMA_OPTIONS`（JSON・`num_ctx` 等を
リクエスト単位で足す）/ `AGENT_OLLAMA_KEEP_ALIVE` / `AGENT_OLLAMA_LOG_DIR` /
`AGENT_OLLAMA_SKILLS_DIR` / `AGENT_OLLAMA_STALL_TIMEOUT` / `AGENT_OLLAMA_FIRST_TOKEN_TIMEOUT`。

TUI は**全画面（alternate screen）にしない**。agent-loop / kiro-loop は tmux の
`send-keys` で入力を送り `capture-pane` で画面を読むので、全画面にすると向こうから
何も見えなくなる。行指向のまま、ステータス 1 行だけを更新する。

## 自己更新との関係

`agent-project` の自己更新は、リポジトリから**本体とこのディレクトリの両方**を
sparse-checkout してから `install.sh` を叩く（既定
`update_subdir: tools/agent-project tools/agent-tools`）。

cone mode の sparse-checkout は指定ディレクトリの兄弟を含まないので、**本体だけを指定すると
ここが取れず installer が必ず失敗する**（自己更新がサイレントに見送られ続ける）。
`update_subdir` を書き換えるときはここを外さないこと。
