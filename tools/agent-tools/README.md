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
echo '要件を3行で要約して' | agent-ollama qwen3                      # 単発（ツールなし）
echo 'この JSON 契約で答えて' | agent-ollama --format json qwen3      # 文法から JSON を強制
echo 'README の誤字を直して' | agent-ollama qwen3 --tools             # 実行ループ（bash 1 つ）
echo 'この repo の構成を調べて' | agent-ollama qwen3 --tools read      # 読み取り専用の探索ループ
agent-ollama --tui qwen3            # デバッグ用の対話ビュー（tmux から操作できる）
agent-ollama --follow               # 走っている実行のログへ後からアタッチする
agent-ollama --status               # いまの進捗を 1 行 JSON で返す（外部監視向け）
agent-ollama --context qwen3        # 文脈の上限だけを調べる（LLM を呼ばない）
```

| モード | 契約上の位置 | 何ができるか |
|---|---|---|
| 既定 | `readonly: enforced` | text → text のみ。ファイルもコマンドも触れない |
| `--tools`（= `--tools bash`） | `write_args` | bash 1 つを道具にした最小ループ。制限なし |
| `--tools read` | `write_args` | 読み取り専用コマンドだけの探索ループ（下記） |
| `--tui` | `interactive` | 進捗を見ながら手で叩く（agent-dashboard の対話診断・agent-loop から） |

ツールとループが `--tools`（書き込みモード）でだけ生えるのが要点。読み取り専用モードには
道具が 1 つも無いので、`readonly: enforced` の宣言が嘘にならない。

### ツールセット — 「道具ゼロ」と「無制限のシェル」の間の段

`--tools read` は探索はできるが何も壊せない段である。**強制はモデルの自己申告ではなく
実行の手前のゲート**で行う（`ollama_loop.check_command`）:

- 語彙は読み取り系コマンドと git の読み取り部分コマンドだけ。`sed -i` / `tee` / `python`
  のように自前の書き込み手段を持つものは入れない
- 引用の外のシェル記号（`|` `>` `$` `` ` `` `*` 等）は拒否する。実行も `bash -lc` を
  介さず argv を直接渡すので、**メタ文字はそもそも解釈されない**（二段構え）
- 拒否は実行せずに理由をモデルへ返し、続けて 3 回目で `tool_denied` として止める
  （権限の探りだけでラウンド予算を焼き切らせない）
- **同梱スクリプトを叩く前提のスキル**（本文に `{skill_dir}` を持つもの）は read セットで
  動かないので、黙って続けず env 分類で落とす

役割別の割り当ては定義ファイルで行う（エンジン改修は不要）:

| 定義 | 使いどころ |
|---|---|
| `ollama` | 汎用。単発 text→text（readonly）/ bash ループ（write） |
| `ollama-json` | JSON 契約の役割（planner / evaluator / plan など）。`--format json` で文法から強制 |
| `ollama-read` | 探索が要る読み取り役割。write 経路に read セットを載せ、権限はゲートが絞る |

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

### 文脈使用量 — 黙った切り捨てを起こさせない

ローカル推論の「たまに指示を無視する」の正体の 1 つが**文脈長の黙った切り捨て**である。
会話が `num_ctx` を超えると ollama はエラーを返さず古い側を落とすので、システム
プロンプトが消えた状態の答えが returns される。stall と同じで、**見えないから直せない**。

そこで使用量を常に持ち、近づいたら警告し、足せるものを削り、それでも入らなければ
**こちらから明示的に止める**（サーバに黙って捨てさせない）。

```
R2/12 decode 経過 4m12s  out=210tk  ctx 4.2k/8.2k (51%)      ← TUI のステータス行
@agent-context used=4531 limit=8192 pct=55.3 source=measured  ← ヘッドレスの stderr
```

- **上限の解決**は「効く順」に見る: `--context-limit` → 送っている `num_ctx`
  （`AGENT_OLLAMA_OPTIONS`）→ `/api/ps`（サーバが実際に確保した値）→ `/api/show`
  （モデルの宣言）。どれも取れなければ上限不明として**使用量だけ**を出す
  （知らない上限を根拠に警告も自衛もしない）。`--context <model>` で調べるだけもできる
- **使用量の実測**は直前の応答の `prompt_eval_count` + 出力トークン。プレフィックス
  キャッシュが効いて「新規評価分だけ」返す版でも、**会話は伸びる一方**という性質で
  補正する（減って見えたら積み上げに切り替え、`context_source` が `estimated` になる）
- **`--context-warn-pct`**（既定 90）を超えたら 1 回だけ警告する（毎ラウンド出さない）
- **ツール出力は残り容量に合わせて詰める**。残りが足りなければ `context_exhausted` で
  止め、`@agent-note` で「途中で打ち切った」ことを呼び出し側にも見せる（成果は返す）
- 使用量は `llm_end` イベント・`--status` の JSON・`@agent-context` 行の 3 か所に出る。
  TUI では `/ctx` でいつでも確認できる

`@agent-usage`（その実行で使った累計トークン = 台帳向け）と `@agent-context`
（いま文脈がどれだけ埋まっているか）は**意味が違う**ので行を分けてある。

### think・スキル・rich

- **`--think on|off`** は CLI オプション（API の `think` フィールドへ直結）。既定は
  `AGENT_OLLAMA_THINK` → モデル既定。プロンプトへ `/no_think` を混ぜる方式は採らない
  （モデル依存で、成果物本文へ漏れる事故がある）。`agents/ollama.json` は `--think on` を
  焼き込んであるので、エンジン側は何も知らなくてよい。**思考は API の `thinking`
  フィールドで本文と分離済み**なので、有効でも成果物は汚れない。decode 時間は伸びる
  ——ここは「品質を時間で買う」側に倒した既定である（think 非対応モデルで困ったら
  定義ファイルで `off` へ戻せる）。
- **`--format json`** は**デコード時の文法制約**で、プロンプトを 1 トークンも増やさない。
  JSON 契約の役割で「妥当な JSON でない出力」という故障モードが消える。全出力が JSON に
  なるので、人が読む本文が成果の役割には使わない。
- **スキルは明示したものだけを遅延で読む**。`--skill <名前>`（プログラム経路）と、
  プロンプト**先頭ブロックのスラッシュ行** `/<名前> [引数]`（人手経路）の 2 形態だけに
  反応する。カタログは LLM へ見せないので、**使わないときの追加コストは 0**。
  読む先は `install.py` の配布先（`~/.agents/skills` / `~/.claude/skills`）そのまま。
- **rich は任意**。`install.sh --with-rich` で zipapp へ同梱すると TUI に色が付く。
  無くても素の ANSI で同じ情報を出す（既定はネットワーク不要のまま）。

### TUI の行編集（矢印キー・ショートカット）

`--tui` の入力行には標準ライブラリの `readline` が噛んでいる。素の 1 行読みでは矢印キーが
`^[[A` のまま本文へ混ざり、打ち間違いを Backspace でしか直せなかった。

| キー | 動き | キー | 動き |
|---|---|---|---|
| `←` `→` | カーソル移動 | `Ctrl-A` / `Ctrl-E` | 行頭 / 行末へ |
| `↑` `↓` | 履歴を辿る | `Ctrl-R` | 履歴を遡って検索 |
| `Tab` | 補完（ローカルコマンド・スキル名・`on\|off`） | `Ctrl-W` | 直前の語を削除 |
| `Ctrl-U` / `Ctrl-K` | カーソルより前 / 後ろを削除 | `Ctrl-L` | 画面をクリア |
| `Ctrl-C` | 入力中の行を捨てる（**終了しない**） | `Ctrl-D` | 終了（空行のとき） |

一覧は TUI 内の `/keys` でも出る。割り当ては `~/.inputrc` がそのまま効く。履歴は
セッションをまたいで `~/.agents/ollama/tui-history` に残る（`AGENT_OLLAMA_HISTORY` で移せる）。

効かせるのは**本物の端末で対話しているときだけ**で、パイプ入力・非 tty では素の 1 行読みへ
落ちる——編集用のエスケープを非 tty へ吐くと、出力を読む側（`capture-pane`）が壊れるため。
`AGENT_OLLAMA_NO_READLINE=1` で明示的に切れる。tmux の `send-keys` / `capture-pane` から見た
画面は従来と同じ（`ready_pattern` の `> ` も含めて変わらない）。

環境変数: `OLLAMA_HOST` / `AGENT_OLLAMA_THINK` / `AGENT_OLLAMA_OPTIONS`（JSON・`num_ctx` 等を
リクエスト単位で足す）/ `AGENT_OLLAMA_KEEP_ALIVE` / `AGENT_OLLAMA_LOG_DIR` /
`AGENT_OLLAMA_SKILLS_DIR` / `AGENT_OLLAMA_STALL_TIMEOUT` / `AGENT_OLLAMA_FIRST_TOKEN_TIMEOUT` /
`AGENT_OLLAMA_CONNECT_TIMEOUT`（接続の上限秒・既定 120）/
`AGENT_OLLAMA_META_TIMEOUT`（文脈上限の問い合わせに許す秒数・既定 3）/
`AGENT_OLLAMA_HISTORY` / `AGENT_OLLAMA_NO_READLINE`（TUI の行編集を切る）。

`OLLAMA_HOST` が未設定のときは `~/.profile` を評価して `OLLAMA_*` / `AGENT_OLLAMA_*` を
補完する。エンジンは agent-ollama を**非ログインシェル**の subprocess として起動するため、
`~/.profile` の `export OLLAMA_HOST=...` はそのままでは届かない——設定はあるのに既定の
127.0.0.1 へ向かって env 落ちする、を防ぐための救済で、環境に既にある変数が常に勝つ。

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
