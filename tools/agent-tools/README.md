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
bash tools/agent-tools/install.sh                       # 4 エンジン + agent-herd（推奨）
bash tools/agent-tools/install.sh --only agent-project  # 1 本だけ
bash tools/agent-tools/install.sh --only agent-herd     # 実行系の入口だけ（推論担当の PC）
bash tools/agent-tools/install.sh --prefix /usr/local/bin
bash tools/agent-tools/install.sh --service             # 常駐化（systemd user unit）も構成
```

エンジンは 4 本（`agent-project` / `agent-flow` / `agent-amigos` / `agent-audit`）で、
ほかに `agent-herd`（と別名の `agent-aider` / `agent-ollama` / `agent-opencode` = 同一
ファイルへのハードリンク）も置く。**別々に入れない。** 同じ `agentcore` と契約バージョンを
共有しているので、片方だけ古いと状態の読み書きや仕事の受け渡しが噛み合わなくなる。更新もまとめて
（`git pull && bash tools/agent-tools/install.sh`）。

各エンジンの `install.sh` は、ここへ `--only <engine>` で委譲する薄いシム
（既存の手順書・`setup.sh`・自己更新の呼び出しパスを壊さないために残してある）。

導入の手順書は [`docs/guides/single-resident-setup.md`](../../docs/guides/single-resident-setup.md)。

## agentcore

転送（git の護り）・claim/lease・語彙・心拍を 1 実装に集約した共通ライブラリ（設計 P0）。
`promptcompose`（プロンプトキャッシュに適合する注入順の正規化・案 H）も agent-project /
agent-flow で共有する（設計: docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md §3）。

**独立配布しない内部モジュール**（設計 R10）。各ツールはそれぞれ別の実行ファイルなので、
`install.sh` が**各 zipapp へ同梱する**——1 本だけ入れ直しても自己完結して動く。同梱先は
このインストーラが作る 5 本（4 エンジン + `agent-herd`）に、自前の installer を持つ
`agent-loop` を加えた 6 本。実行系の 3 名（`agent-aider` / `agent-ollama` /
`agent-opencode`）は `agent-herd` への別名なので、zipapp の数は増えない。

開発木から直接実行するときは、各エンジンの `__init__.py` がこのディレクトリを `sys.path` へ
足して解決する（`tools/<engine>/<package>/__init__.py` から見て `../../agent-tools/agentcore`）。
zipapp では同梱物が先に解決されるので、その追加パスは存在しなくても無害に素通りする。

テストルートは **2 つある**（`agentcore/tests/` と パッケージ内の `agentcore/agentcore/tests/`）。
片方だけ discover すると残りが黙ってスキップされるので、両方を回す（CI も両方を明示している）:

```bash
cd tools/agent-tools/agentcore && python3 -m unittest discover -s tests && python3 -m unittest discover -s agentcore/tests
```

設計判断は [`docs/designs/agentcore-design.md`](../../docs/designs/agentcore-design.md)、
モジュール一覧・公開 API・写しを縛るテストは
[`docs/specs/agentcore-spec.md`](../../docs/specs/agentcore-spec.md)。

## agent-herd — コスト 0 のローカル実行系

`install.sh` は 4 エンジンのほかに `agent-herd`（zipapp・1 ファイル）と、その別名として
`agent-aider` / `agent-ollama` / `agent-opencode`（同一ファイルへのハードリンク）を置く。
別名は互換シムではなく本体そのものなので、打ち方も出力も従来どおりである。出発点は
「クラウドの CLI がガバナンスや予算の事情で使えなくなったときに作業を止めないため」の
バックアップだったが（[2026-08-06 の対策案](../../docs/plans/2026-08-06-opencode-ollama-cpu-inference-proposals.md) §0.1・案 F-2）、
現在は**品質が成立する役割を恒常的に引き受けるコスト 0 の常備戦力**として位置づけている。
**犠牲にするのは壁時計時間だけで、「契約に完全適合すること」と「止まっていないことを
示せること」は要件のまま。**

設計判断は [`docs/designs/agent-herd-design.md`](../../docs/designs/agent-herd-design.md)、
サブコマンドの綴り・profile の割当・フラグ・環境変数・上限・終了状態は
[`docs/specs/agent-herd-spec.md`](../../docs/specs/agent-herd-spec.md)。

分岐は `basename(argv[0])` の 1 回だけで、あとはサブコマンドが決める:

```bash
agent-herd aider …        # = agent-aider …     （Aider をヘッドレスで回す）
agent-herd ollama …       # = agent-ollama …    （ollama を回す。--tools / --tui も）
agent-herd opencode …     # = agent-opencode …  （opencode を回す）

agent-herd chat [<cli>]   # 定義の interactive で対話起動する（既定は ollama の内蔵 TUI）
agent-herd defs [<名前>]   # 定義の一覧と実効 argv（エンジンが組むのと同じもの）
agent-herd exec <cli>     # 定義どおりにヘッドレス実行する（人のデバッグ用。本文は stdin）
agent-herd harness …      # statemachine / run を tmux もデーモンも無しに回す
agent-herd status|follow|replay   # 観測と測定（ollama の同名フラグの別名）
```

**サブコマンドは adapter の名前であって定義の名前ではない。** `ollama-json` のような定義を
指して回すときは `agent-herd exec ollama-json` を使う（打ち間違えたら黙って別解釈せず、
`exec` を案内して止まる）。

ollama を直に叩く例（`agent-ollama …` の旧綴りも同じコードパスに落ちるので、既存の手順書は
書き換え不要）:

```bash
echo '要件を3行で要約して' | agent-herd ollama gemma4:e4b                 # 単発（ツールなし）
echo 'この JSON 契約で答えて' | agent-herd ollama --format json gemma4:e4b # 文法から JSON を強制
echo 'README の誤字を直して' | agent-herd ollama gemma4:e4b --tools        # 実行ループ（bash 1 つ）
echo 'この repo の構成を調べて' | agent-herd ollama gemma4:e4b --tools read # 読み取り専用の探索ループ
agent-herd chat                          # 対話（= agent-herd ollama --tui。人の入口）
agent-herd follow                        # 走っている実行のログへ後からアタッチする
agent-herd status                        # いまの進捗を 1 行 JSON で返す（外部監視向け）
agent-herd ollama --context gemma4:e4b   # 文脈の上限だけを調べる（LLM を呼ばない）
agent-herd replay --arm model=gemma4:e4b,think=off,format=json \
                  --arm model=gemma4:e4b,think=on   # 再生して品質を測る
```

| モード | 契約上の位置 | 何ができるか |
|---|---|---|
| 既定 | `readonly: enforced` | text → text のみ。ファイルもコマンドも触れない |
| `--tools`（= `--tools bash`） | `write_args` | bash 1 つを道具にした最小ループ。制限なし |
| `--tools read` | `write_args` | 読み取り専用コマンドだけの探索ループ（下記） |
| `--tui` | `interactive` | 進捗を見ながら手で叩く（agent-dashboard の対話診断・agent-loop から。人が直接入るなら `agent-herd chat`） |
| `--replay` | 観測（測定） | 記録済みプロンプトを再生する。**道具は持たない**（下記） |

ツールとループが `--tools`（書き込みモード）でだけ生えるのが要点。読み取り専用モードには
道具が 1 つも無いので、`readonly: enforced` の宣言が嘘にならない。

`agent-aider` は `--agent-policy gemma4-e4b-reliability-v1` を wrapper option として受け取り、
`ollama_chat/gemma4:e4b` の Aider system prompt 先頭へ固定 reliability policy を注入する。
適用時は stderr の `@agent-policy id=... sha256=...` で実効 policy を観測できる。未知の ID、
対象外 model、外部 `--model-settings-file` との競合は黙って無効化せず、起動前に失敗する。

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

役割別の割り当ては定義ファイルで行う（エンジン改修は不要）。**用途別の起動差は別ファイルでは
なく `agents/ollama.json` の `profiles` にある**——分けると `agent_cli` が用途ごとに増え、
1 実行系の実測が偽の候補へ割れるため:

| profile | 従来の綴り | 使いどころ |
|---|---|---|
| （base） | `ollama` | 汎用。単発 text→text（readonly）/ bash ループ（write） |
| `json` | `ollama-json` | JSON 契約の役割（planner / evaluator / plan など）。`--format json` で文法から強制 |
| `list` | `ollama-list` | 配列契約の役割（split）。`--format json` はトップレベルをオブジェクトに固定して配列を表せないので、スキーマを渡す `--format array` で受ける |
| `list-thinking` | `ollama-list-thinking` | Aider/Gemma 4 の split。文法制約を外して Thinking を使い、`temperature=0` で意味的な完全被覆を安定させる |
| `read` | `ollama-read` | 探索が要る読み取り役割。write 経路に read セットを載せ、権限はゲートが絞る |
| `verify` | `ollama-verify` | 受入条件の判定層。既定モデルだけ `gemma4:12b` で、`--stall-timeout 180` を持つ |

従来の綴りはそのまま解決でき（`ollama-list` → base=`ollama` / profile=`list`）、`variants` の
指す先もこの綴りのままでよい。**台帳と格付けに残る `agent_cli` は正典名の `ollama`** に揃う
（`agentcli.canonical_name()`）。実効 argv は `agent-herd defs ollama-list` で確認できる。

### 「遅い」と「死んだ」を区別する

このバックアップ運転では **1 呼び出しが数十分になるのが正常**である。だから壁時計で
打ち切ると正常な実行を殺す。代わりに次の三層を持つ:

1. **進捗ログ（JSONL・追記のみ）**。`~/.agents/logs/ollama/` に、ラウンド遷移・
   トークン速度・ツール実行と、**沈黙中の heartbeat**（`phase=prefill` と待ち秒数）を書く。
   これが「その時刻に生きていた」ことの事後証明になる。会話の本文も同じログへ
   `kind="message"`（`role` / `content`）として残るので、`agents/ollama.json` の
   `session_log` 宣言だけで agent-audit がセッションとして読める——「あの工程で何を
   指示して何が返ったか」を後から読める CLI と読めない CLI を混ぜないため
   （`agent-audit sessions --cli ollama`、agent-dashboard の工程詳細「会話を見る」）。
2. **`--status` / `--follow`**。プログラムは前者（`state` / `alive` /
   `since_last_progress_sec` / `tokens_per_sec`）、人は後者で同じものを見る。
   `state=running` かつ `alive=true` なら、長い沈黙は「遅い」であって「固まった」ではない。
3. **`--stall-timeout`（既定 180 秒）**。生成が始まった後の**無進捗**だけを打ち切り、
   `transient` 分類で返してエンジンのリトライ層に拾わせる。
   **最初のトークンまでの待ちは既定で無制限**（`--first-token-timeout 0`）——CPU 推論では
   prefill だけで 10 分かかるのが普通で、ここに上限を置くと正常な実行が死ぬ。

4. **ラウンド粒度の無進捗（`no_progress`）**。1〜3 が見ているのは「トークンが出るか」
   だけなので、**トークンは出続けているのに仕事が進まない**形——同じコマンドを同じ結果で
   叩き続ける空回り——は素通りしていた。同じ `(コマンド, 終了コード, 出力)` が 3 回続いたら
   `no_progress` で止める。判定は完全一致だけ（出力が 1 バイトでも変われば「進んでいる」
   と見る）なので、テスト再実行のような繰り返す意味のある仕事は殺さない。
   分類は `env`——同じ入力を再試行しても同じ空回りに同じ時間を焼くだけで、解けない。

エンジン側は壁時計の上限を大きく取り（例 `agent_timeout: 3600`）、実質の検知器を
`--stall-timeout` と `no_progress` に任せるとよい。壁時計は「無限ハング時の最後の砦」。

write の `--max-rounds` は 12 に絞ってある（read セットを載せる `read` profile は 30 のまま）。
実測の空回り run に「もう少し回れば畳めた」形跡が無く、30 まで回せること自体が
ターンの食いつぶしだったため。読取は 1 ラウンドが安く、打ち切りが成果の欠落に直結する。

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
  止める（成果は返す。打ち切りの申告は下記）
- 使用量は `llm_end` イベント・`--status` の JSON・`@agent-context` 行の 3 か所に出る。
  TUI では `/ctx` でいつでも確認できる

`@agent-usage`（その実行で使った累計トークン = 台帳向け）と `@agent-context`
（いま文脈がどれだけ埋まっているか）は**意味が違う**ので行を分けてある。

### 完走しなかったときの申告

`done` 以外（`no_command` / `max_rounds` / `no_progress` / `context_exhausted` /
`tool_denied`）で終わったら、**成果本文の末尾へ機械可読な封筒**を足す
（`--format json` のときだけ足さない）:

```
…最後の応答までの成果…

{"ok": false, "issues": ["agent-ollama: 最大ラウンドに達して打ち切りました（status=max_rounds）"]}
```

同じことを `@agent-note` で stderr にも出すが、**判定に使うのは封筒のほう**。人向けの
注記は呼び出し側が読まないので、これが無いと途中経過が rc=0 の完了として扱われる
（規約から外れたまま打ち切る `no_command` が一番起きる）。封筒の形はエンジン側の
worker 契約（agent-flow の `{"ok": …}` 判定）と同一。

**圧縮して続行するループは持たない**（非目標）。圧縮 1 回は会話全体の再 prefill 1 回で、
繰り返した時点で停滞が確定し、要約のたびに情報欠落だけが積み上がる。文脈が尽きたら
途中成果と `@agent-note` を返して止まり、**続きはタスクを割って新しい会話でやる**。
`context_exhausted` は定義の `errors` で `env` に分類してある——`transient` にすると
エンジンが同じ入力で再試行し、同じ壁に同じ時間を掛けてぶつかり続ける。

運用側で先に余裕を確保しておく（サーバ既定に任せない）:

```bash
export AGENT_OLLAMA_OPTIONS='{"num_ctx": 32768}'   # 呼び出し単位で明示する
export OLLAMA_FLASH_ATTENTION=1                    # KV キャッシュ量子化の前提
export OLLAMA_KV_CACHE_TYPE=q8_0                   # 同上（RAM を稼ぐ）
export OLLAMA_KEEP_ALIVE=1h                        # 冷起動の除去
export OLLAMA_NUM_PARALLEL=1                       # 先頭キャッシュが効く前提
```

上限は「KV キャッシュ込みで物理 RAM に収まること」。**スワップに落ちた瞬間、遅いではなく
停滞になる**——モデル選定でも `num_ctx` でも、ここだけは越えない。

### 品質を測る — 記録済みプロンプトのオフライン再生

設定を変えたときに**品質が上がったのか下がったのか**は、ライブ運用の台帳からは実質
測れない（1 件あたり数十分の実行を焼くので、結局測られないまま設定だけが変わる）。
そこで、既に走った実行の JSONL から入力を取り出して再生する:

```bash
# 直近 20 件を 2 つの設定へ当てて比べる（think の効きを見る）
agent-herd replay --replay-limit 20 \
  --arm model=gemma4:e4b,think=off,format=json \
  --arm model=gemma4:e4b,think=on,format=json

# モデルを跨いで比べる（verify profile の既定を上げる根拠が要るとき）
agent-herd replay --replay-limit 20 \
  --arm model=gemma4:e4b,think=off,format=json \
  --arm model=gemma4:12b,think=off,format=json

# 同じ設定を 3 回引いてばらつきを見る（自己一貫性）
agent-herd replay --arm model=gemma4:e4b,think=off,repeat=3
```

- 入力はログの**最初の user メッセージ**。道具ありの実行ログも入力源にできる
  （後続の user メッセージはツール結果の差し戻しなので使わない）
- **再生は常に道具なしで行う。記録されたコマンドは再実行しない**——再生は測定であって
  副作用の再現ではない。ここは腕（`--arm`）の指定でも緩められない
- 同じプロンプトはまとめて数える（`occurrences`）。同一の planner プロンプトが何十本も
  残っている置き場を素直に舐めると、同じ測定を繰り返すだけで再生予算が溶ける
- stdout は集計 1 行 JSON（腕ごとの空応答率・失敗率・所要秒の中央値と、**腕をまたいだ
  一致率**）。1 件ごとの記録は JSONL で `--replay-out` へ落ち、場所は `@agent-log` に出る
- 一致判定は JSON として読めればキー順まで揃えてから比べる（JSON 契約の役割で、
  キーの順や空白の差を不一致に数えない）

正解ラベルとの一致率はここでは出さない——ラベルは人が付けるものであり、この口は
「同じ入力に対する出力」を再現可能な形で並べるところまでを引き受ける。

### think・スキル・rich

- **`--think on|off`** は CLI オプション（API の `think` フィールドへ直結）。既定は
  `AGENT_OLLAMA_THINK` → モデル既定。プロンプトへ `/no_think` を混ぜる方式は採らない
  （モデル依存で、成果物本文へ漏れる事故がある）。**思考は API の `thinking`
  フィールドで本文と分離済み**なので、有効でも成果物は汚れない。

  `agents/*.json` は think を**ヘッドレスの全役割で off** に焼き込んである。残すのは TUI
  だけ（人が待てる場のみ）。実測（2026-08-10・ログ 236 本）で on の 3 経路が全滅した:

  | 経路 | 実測 |
  |---|---|
  | `write_args`（道具を持つ側・最大 30 ラウンド） | 1 ラウンドの思考だけで 7700 トークン・12 分。p90 942 秒に対し呼び出し側の `agent_timeout` は 600 秒 |
  | `readonly_args`（planner / evaluator） | 中央値 1000 秒。同じく 600 秒を超える |
  | `--format json` との併用 | **本文が空**（39/39 件）。文法制約が `thinking` の 1 トークン目から掛かり、答えの JSON が思考側に入る |

  3 つ目は `--format` を渡した時点で `think off` を強制するようにしたので、定義ファイルで
  復活させられない。思考が品質に変換された証拠は 1 件も取れていないので、on へ戻すなら
  記録済みプロンプトのオフライン再生で先に示すこと。

  **この実測は qwen3.5:9b のものである**（台帳 `ledger-2026-08-10-qwen35-9b.jsonl`。
  gemma4:e4b がこのリポジトリへ入るのは翌日）。全役割 off の焼き込みは、そこからの
  一般化であって gemma4 で測り直したものではない——同じリポジトリ内に反証もある
  （`ollama-list-thinking` は gemma4 の split で Thinking を使う）。測り直す口は下記。
- **`--think prompt`** は system prompt 先頭へ `<|think|>` を置く方式（Gemma 4 系の作法）。
  API の `think` フィールドとは**経路が違う**ので、`--format` の強制 off に巻き込まれない
  ——「JSON 契約の役割では Thinking を使えない」という上の制約が、この経路では
  当てはまらない可能性がある。**どちらなのかは未実測**で、測り方は
  [`eval/README.md`](eval/README.md) の「推論条件の腕」にある（`--replay` の腕でも
  `think=prompt` を指定できる）。プロンプトを汚す `/no_think` 方式とは目的が逆である点に
  注意——あれは思考を止めるための細工だが、こちらは**モデル側の作法に従う**ための口である。
- **`--format json`** は**デコード時の文法制約**で、プロンプトを 1 トークンも増やさない。
  JSON 契約の役割で「妥当な JSON でない出力」という故障モードが消える。全出力が JSON に
  なるので、人が読む本文が成果の役割には使わない。
- **スキルは明示したものだけを遅延で読む**。`--skill <名前>`（プログラム経路）と、
  プロンプト**先頭ブロックのスラッシュ行** `/<名前> [引数]`（人手経路）の 2 形態だけに
  反応する。カタログは LLM へ見せないので、**使わないときの追加コストは 0**。
  最初に `~/.agents/skills` を読み、次に `AGENT_OLLAMA_SKILLS_DIR` の追加先、
  最後に `~/.claude/skills` を読む。
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

環境変数: `OLLAMA_HOST` / `AGENT_OLLAMA_THINK`（on|off|prompt） / `AGENT_OLLAMA_OPTIONS`（JSON・`num_ctx` 等を
リクエスト単位で足す）/ `AGENT_OLLAMA_KEEP_ALIVE` / `AGENT_OLLAMA_LOG_DIR` /
`AGENT_OLLAMA_SYSTEM_PROMPT`（追加の system instruction。未指定なら送らない）/
`AGENT_OLLAMA_SKILLS_DIR` / `AGENT_OLLAMA_STALL_TIMEOUT` / `AGENT_OLLAMA_FIRST_TOKEN_TIMEOUT` /
`AGENT_OLLAMA_CONNECT_TIMEOUT`（接続の上限秒・既定 120。到達時に生存確認が通れば
順番待ちとして待ち続け、サーバに届かないときだけ打ち切る）/
`AGENT_OLLAMA_META_TIMEOUT`（文脈上限の問い合わせに許す秒数・既定 3）/
`AGENT_OLLAMA_HISTORY` / `AGENT_OLLAMA_NO_READLINE`（TUI の行編集を切る）。

`OLLAMA_HOST` が未設定のときは `~/.profile` を評価して `OLLAMA_*` / `AGENT_OLLAMA_*` を
補完する。エンジンは agent-ollama を**非ログインシェル**の subprocess として起動するため、
`~/.profile` の `export OLLAMA_HOST=...` はそのままでは届かない——設定はあるのに既定の
127.0.0.1 へ向かって env 落ちする、を防ぐための救済で、環境に既にある変数が常に勝つ。

TUI は**全画面（alternate screen）にしない**。agent-loop / agent-loop は tmux の
`send-keys` で入力を送り `capture-pane` で画面を読むので、全画面にすると向こうから
何も見えなくなる。行指向のまま、ステータス 1 行だけを更新する。

## 自己更新との関係

`agent-project` の自己更新は、リポジトリから**本体とこのディレクトリの両方**を
sparse-checkout してから `install.sh` を叩く（既定
`update_subdir: tools/agent-project tools/agent-tools`）。

cone mode の sparse-checkout は指定ディレクトリの兄弟を含まないので、**本体だけを指定すると
ここが取れず installer が必ず失敗する**（自己更新がサイレントに見送られ続ける）。
`update_subdir` を書き換えるときはここを外さないこと。
