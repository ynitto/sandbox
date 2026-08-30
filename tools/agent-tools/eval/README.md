# agent-tools 評価ハーネス

Ollama のモデルを交換するときに、**モデルの実力**と**エージェント・ハーネスの影響**を
混同せず比較するための測定群。処理単位ごとに直接実行でき、まとめて同条件で回すこともできる。

| 単位 | スクリプト | 測るもの | 主な指標 |
|---|---|---|---|
| worker | `worker_eval.py` | ファイル編集・修正・複数成果物 | 決定的チェッカーの受入率、壁時計、失敗様式 |
| judge | `judge_eval.py` | split/filter/judge/reduce/evaluator | 正解一致率、自己一貫性、形式違反 |
| retrieval | `retrieval_eval.py` | 記憶検索（生成モデルとは独立） | hit@k、MRR、検索時間 |
| planner | `planner_eval.py` | 要求 → タスクグラフ（flow-planner の `plan.py` を本番引数で呼ぶ） | 構造チェッカーの正解率、契約違反、過分解 |
| candidate | `candidate_eval.py` | 候補生成 + 決定的検算（grep パターン・パス・テスト名） | 機械が落とした後に正解が残るか |
| project-verify（履歴） | `project_verify_eval.py` | 廃止した charter 自然文 verifier の反証を保存 | 捏造 pass 率（道具なし）・判定の正しさ（道具あり） |
| moe-ram | `moe_ram_probe.py` | P11: MoE 候補が物理 RAM に収まるか（対象マシンで数分） | num_ctx ごとの常駐・load・prefill/decode・成立判定 |
| doctor | `doctor_eval.py` | agent-dashboard の Doctor 4 モード（本番 `doctorPrompt` を node で呼ぶ） | 見出し契約 + 構成的な言及 |
| observation | `log_stats.py` | 既存の agent-ollama ログ | prompt/output 寸法、TTFT、decode 速度 |
| coverage | `coverage_eval.py` | flow/project/dashboard/amigos の呼び出し面 | direct/indirect/missing の棚卸し |

### 網羅性（重要）

**現時点では網羅していない。** `judge_eval.py` が直接測る agent-flow の処理は
`split / filter / judge / reduce / evaluator`、`worker_eval.py` が直接測るのは `work`、
`planner_eval.py` が直接測るのは `planner`、
`doctor_eval.py` が agent-dashboard の `doctor/*` 4 モード（いずれも 2026-08-23 追加）である。
`generate` は work と同じ実装系契約を間接的に通すだけで、専用セルではない。残りの flow 役割、
agent-project の 9 面・agent-dashboard の他 8 面・agent-amigos 全面はまだ能力測定が無い。この状態を「suite に名前が無いから存在しない」ように見せないため、
`coverage.json` に全呼び出し面と `direct / indirect / missing / deterministic` を明記した。

```bash
python3 tools/agent-tools/eval/coverage_eval.py
```

agent-flow の一覧は正典 `agentcore.nodecontract.VALID_KINDS` と実行時に突き合わせる。kind が増えて
manifest の更新を忘れると coverage 測定とテストが失敗する。`human` は LLM を呼ばないので
`deterministic` とする。project/dashboard/amigos は flow を単に呼ぶだけではなく、それぞれ
独自プロンプト・出力契約・CLI 解決を持つため、flow の点数で代用しない。現状の `missing` は
今後、各表面の正典プロンプトビルダーを直接呼ぶ決定的ケースを追加してから `direct` に変える。

## モデル交換時の標準測定

`run_suite.py` は各単位をサブプロセスとして直列実行する。Ollama の取り合いを避け、同じ
`model / repeat / wall / cli` を manifest に固定する。最初は dry-run で条件を確認する。

```bash
python3 tools/agent-tools/eval/run_suite.py --model qwen3.5:9b --dry-run
python3 tools/agent-tools/eval/run_suite.py --model qwen3.5:9b --cli agent-ollama
python3 tools/agent-tools/eval/run_suite.py --model qwen3.5:9b --cli aider --label aider
# worker の腕（引き直し）を標準測定の経路で回す。条件は manifest の worker_arm に残る
python3 tools/agent-tools/eval/run_suite.py --model gemma4:e4b --cli aider --units worker \
  --tasks T1gate,T2gate,T3gate --agent-policy off \
  --temperature 1.0 --top-p 0.95 --top-k 64 --resample 3 --label resample3
```

worker の腕を変える軸（`--tasks` / `--agent-policy` / `--num-ctx` / `--num-predict` /
`--temperature` / `--top-p` / `--top-k` / `--resample`）は `run_suite` を素通りして
`worker_eval` へ届き、**指定したものだけ**が manifest の `worker_arm` に flag 名のまま残る。
未指定は 1 バイトも渡さない——`worker_eval` が区別している「未宣言」と「既定値を明示」を
run_suite が潰さないため（契約テスト `test_run_suite.py`）。

結果は `results/<UTC時刻>-<model>-<label>/` の下へ保存する。run 直下の `manifest.json` が
比較条件、単位ごとの `command.txt` が再現コマンド、`console.log` が生ログ、`ledger.jsonl`
または `metrics.json` が機械可読な結果である。通常の run は Git 管理外で、採用判断の根拠に
残すスナップショットだけを `results/archive/` へ移す。過去の台帳も同所へ整理した。

### 実行方針の腕（`harness` 軸）

呼び出しの形やプロンプトを変えた測定は、変える前の数字と同じ表に並べられない。
`worker_eval --harness ARM` がその条件を宣言し、台帳の `harness` 列に残る。

| 腕 | 何が変わるか |
|---|---|
| `default`（既定） | 現行の実行方針。何も上書きしない |
| `templates:<宣言ディレクトリ>` | ツールループのプロンプトを宣言から差し替える（agent-herd 設計 §3.5・段 13）。`AGENT_COMMANDS_DIR` として子へ渡る。agent-ollama 経路のみ |

**腕は名前だけにしない。** 名前が実際の設定を指していないと、条件の違う数字が同じ
`harness` 値で並び、軸を足した意味が消える——知らない腕名と、効かない組み合わせ
（aider に `templates:`）は起動時に断る。

### 呼び出し回数上限の腕（`max_rounds` 軸）

`worker_eval --max-rounds N`（**agent-ollama 経路のみ**）。制限付き実行案 §6 の
「回数制限のみ」を引く口で、台帳の `max_rounds` に残る（未指定は null ＝定義のまま）。

**環境変数（`AGENT_MAX_TOOL_ROUNDS` / `..._WRITE`）では引けない。** 上限の優先順は
宣言 ＞ 環境変数 ＞ 層の既定（`agentcore.limits`）で、`agents/ollama.json` の `write_args`
が `--max-rounds 12` を宣言しているため、本番 argv は環境変数では締まらない——測定条件が
運用の宣言を黙って上書きしないための順序である。環境変数が効くのは宣言を持たない経路
（statemachine の `max_tool_rounds` 未宣言ステートなど）。

aider 経路では断る。aider は自分の直し直し（`max_reflections`）を持つ単発 worker で、
ハーネスは周を数えていない——効かない宣言を受けると腕の名前だけが台帳に残る。

#### 実測（2026-08-29・T2・agent-ollama・gemma4:e4b・n=3）

台帳: `results/archive/worker/ledger-2026-08-29-maxrounds-{12,3,2}-t2-agent-ollama-gemma4-e4b.jsonl`

| 腕 | 受入 | 中央値 | 終わり方 |
|---|---|---|---|
| 12（定義のまま） | 0/3 | 600s | timeout 3/3 |
| 3 | 0/3 | 600s | timeout 2/3・自己申告の未完了 1/3 |
| **2** | 0/3 | **51s** | 自己申告の未完了 3/3 |

**受入は動かない。上限は品質のレバーではない。** 3 腕とも 0/3 で、n=3 の読み方
（全滅 ⇔ 全通だけを差にする）に照らすと**受入率の差は主張できない**。state-harness の
外部実測（naive turn cap で +35pp）は、この課題 × この層では再現しなかった。

**効いたのは失敗の値段である。** 中央値 600s → 51s（−92%・区間が重ならない）。同じ壁時計
予算で 11 回多く試せる・上位へ回せる。層を替えたときの利得（600s timeout → 76〜130s
returned・§「T2 は同じモデルのまま 1/3 → 3/3」）と同じ形で、**失敗を安くするレバー**である。

**上限 12 と 3 は到達しない。** どちらも壁時計（600s）が先に切る——1 周がおよそ 200〜300s
かかるので、3 周でも予算を超える。**この経路で上限が実際に効いたのは 2 だけ**だった。
「回数上限」は、1 周が壁時計予算に対して十分安いときにしか意味を持たない。

**偽の完了は増えていない。** 上限で切れた腕の終わり方は `self_reported_incomplete`
＝モデルが自分で未完了と申告して返る形で、無言で切られる timeout より上位への振替が
正しく起きる（制限付き実行案 §6 の合格条件 4）。

---

## policy 文面のチューニング — F2 は書き方では直らない（2026-08-29）

`AGENT_OLLAMA_SYSTEM_PROMPT` を seam に、reliability policy の文面だけを差し替えて
判定セルを回した（model / harness / cases / repeat は同一、順を反転した 2 ブロック）。
台帳は `results/archive/judge/ledger-2026-08-29-policytune-*.jsonl`、
候補文面は `results/archive/judge/policy-2026-08-29-v2a-candidate.txt`。

**狙い**: F2（filter）の失敗は「指示に無い基準を自分で足す」型である。課題の基準は
「追加依存が要らないこと」1 つだけなのに、モデルは「依存なし **かつ** テスト pass」で
絞って `c3` だけを返す（正解は `c2,c3,c5,c6`）。policy 3 条がまさにこれを狙った条文なので、
そこを**禁止文から手順へ**変えた 1 テーマだけの候補（v2a）を測った。

| ケース | v1（4 ブロック） | v2a（2 ブロック） |
|---|---|---|
| **F2** | 1/5・1/5・0/5・0/5 | **1/5・1/5** |
| J1 / J2 / R1 / F1 / S1 | 5/5 | 5/5（維持） |
| 合計 | 30〜31/40 | 30〜31/40 |

**動かない。** v2a の 1/5 は v1 の揺れ幅（0〜1/5）の中で、退行も無い代わりに改善も無い。

**F2 に対するプロンプト側の攻撃は 4 回連続で失敗している**——sampling（P10）・
`think=prompt`（P10）・policy v1・policy v2a。**書き方の問題ではない。**

### 直る経路は構造側にある

同じ日・同じ条件・同じ policy で、素材も正解も同じまま**構造だけ**を変えると通る:

| 経路 | v1 | v2a |
|---|---|---|
| F2（直接 filter させる） | **0/5** | **0/5** |
| F2P（決定化: モデルは事実抽出だけ・判定は機械） | **5/5** | **5/5** |
| J1（直接 judge させる） | 5/5 | 5/5 |
| J1P（決定化） | 5/5 | 5/5 |

`think: off` + `--format json` + 「採用した id だけを配列で出せ」という出力契約の下では、
policy が要求する「全項目を全基準に照らす」**内部手続きを実行する場所が無い**——
出力はいきなり `["c3"]` になる。列挙を出力契約の側へ移す（＝決定化）と 5/5 で通る。

**したがって多基準 filter / judge は policy で救わない。** 決定化へ回す（既定の方針どおり）。
policy の役割は、決定化できない仕事（コード編集）のほうにある（Gate 2 §8.0: T1 0/3 → 2/3）。

---

## 接頭辞キャッシュのプローブ — `prefix_cache_probe.py`（計画 2026-08-22 案 3 の前提）

案 3（同役割の直列バッチ化）は「役割が交互に来ると system 接頭辞が入れ替わり prefill を
払い直す」という前提に立つ。**スケジューラを触る前に前提だけを測る**道具。

```bash
python3 tools/agent-tools/eval/prefix_cache_probe.py --model gemma4:e4b --per-role 5
```

同じ長さ・同じ本数・同じ本文で消化順だけを変える（`A A A B B B` 対 `A B A B A B`）。
生成は `num_predict=1` で打ち切る——見たいのは prefill だけで、decode を混ぜると腕の差が
decode の揺れに埋もれる。全接頭辞を先に温めてから測る（初回 prefill は 40 倍以上高いので、
片方だけ温めると「腕の差」ではなく「初回コストの置き場所」を測ることになる）。

**判定は時間で行う。** `prompt_eval_count` はキャッシュに当たっても落ちない（実測: 全
リクエストで 2904 のまま）。落ちるのは `prompt_eval_duration` のほうである。

実測（gemma4:e4b・接頭辞 2904 tok・20 呼び出し・2026-08-29）:

| 腕 | prefill 合計 | 中央値 |
|---|---|---|
| batched | **23.5s** | **0.53s** |
| interleaved | 70.2s | 3.68s |

合計で約 3.0 倍（3 回引いて 3.02 / 3.22 / 2.98）。1 呼び出しおよそ 2.3 秒の節約。
台帳 `results/archive/worker/prefix-cache-2026-08-29-gemma4-e4b.json`。

**節約は接頭辞ぶんの秒数で固定**なので、1 周 200〜300 秒のコード仕事では誤差、
1 呼び出し 9〜13 秒の判定・抽出系では 2 割前後になる。

`qualification_seed.py` は**現行方針の行だけ**を格付けに使う（`harness` を持たない行は
軸を足す前の記録＝現行方針として受ける）。外した行があれば数と腕を stderr に言う——
黙って間引くと、少ない `samples` が「測っていない」のか「別方針を外した」のか読めない。

### 評価archiveから初期適格性を生成する

`qualification_seed.py` は、保存済み台帳を候補×処理種別へ明示的に対応付け、
`agent-candidate-qualifications` v1 を生成する。同じモデルでも agent CLI が違えば別候補として扱い、
コード側の12b候補は既知の停止性問題があるため `blocked` のままにする。出力のwriterはこの初期変換だけで、
運用開始後の更新は agent-audit が担う。

```bash
python3 tools/agent-tools/eval/qualification_seed.py \
  --revision 1 \
  --generated-at 2026-08-15T00:00:00Z \
  --output /path/to/agent-control/qualifications.json
```

出力には根拠を区別する `source: eval-archive` と、evaluation profileの
`valid_for_days`から計算した `valid_until` が入る。入力archive、revision、生成時刻が同じなら
出力も同じになるため、レビューや再生成では`--generated-at`を固定する。

### 評価archiveからおすすめ構成を生成する

`recommend.py` は、同じ archive から **`agent-recommendation`**（読み取り専用のおすすめ構成）を
出す。適格性の生成は `qualification_seed.build_seed()` の 1 実装をそのまま使い、そこへ
「実行レベルの構成・実行方針・同時実行数・必要なモデル・1 行ずつの根拠」を添えるだけである。

```bash
# 生成（配布物として置く。制御面ではないのでインストーラが配ってよい）
python3 tools/agent-tools/eval/recommend.py \
  --generated-at 2026-08-26T00:00:00Z --output ~/.agents/recommendation.json

# 現在の制御面との差分を見る（書き込みはしない）
python3 tools/agent-tools/eval/recommend.py --print-diff --control-dir ~/.agents/control
```

**実行レベルのローカル候補は `herd` の 1 語である。** `aider` / `ollama` の
どれをどのモデルで使うかは用途ごとに違い（抽出は e4b・レビューは 12b・コード編集は aider の
e4b）、それを知っているのは実測なので、推奨も具体名を書かない。一族は
`agents/<name>.json` の `command[0] == "agent-herd"` で機械的に決まる。クラウドは実測できない
ので `slots`（枠）として宣言し、値は適用時に人が選ぶ。

出力は決定的で、同じ archive・revision・生成時刻なら同じ JSON になる。正典は
`schemas/agent-recommendation.schema.json`。

生成モデルだけを比べる基準線は `--cli agent-ollama` のまま `--model` だけ変える。
ハーネスを調整する実験ではモデルを固定し、`--cli`、`--wall`、個別スクリプトの
`--methods` / `--num-predict` を **1 度に 1 つだけ**変える。worker の aider 経路は比較用に
残すが、モデル交換の必須条件ではない。retrieval の埋め込みモデルは生成モデルと別軸なので、
`--embedding-model` で明示する。

Gemma 4 の thinking を診断するときは、JSON 文法制約が thinking を強制 off にするため、
`judge_eval.py --drop-format --think on` のように両方を明示する。これは本番基準線ではなく
診断セルであり、各台帳行の `think_override` / `format_dropped` に実効条件を残す。
追加の system instruction は `AGENT_OLLAMA_SYSTEM_PROMPT` で渡せる（未指定なら送らない）。

## worker 受入率ハーネス — ローカルモデルを 1 時間で判定する

`agent-ollama` に載せたモデルを **worker として使えるか**だけを測る。合否は決定的な
チェッカーが出し、判定役（LLM）は 1 度も呼ばない。

```bash
python3 tools/agent-tools/eval/worker_eval.py --model qwen3.5:9b --repeat 3
```

同じ問いを本番ラインの観察で答えようとして 3 日かけて何も出なかった。ハーネスに移して
1 時間で決着した。別モデルを試すときも `--model` を変えるだけでよい。

## なぜ本番ラインで測らないか

agent-project → agent-flow → bus → ollama を通して観察すると、1 データ点に 20〜30 分かかり、
しかも次の 5 つが同時に効いて交絡する。

- heal が同じ失敗を 10 回繰り返し、台帳の失敗数を水増しする
- タスクが毎回違うので、run 同士を比べられない
- ollama サーバを他セッションと共有すると `stall: connect` が混ざる
- インフラ起因の失敗がモデルの品質を覆い隠す
- `agent-ollama` は zipapp。ソースを直しても `install.sh` を回すまで反映されない

ハーネスはこの 5 つを全部外す。使い捨て worktree・直列実行・同一タスクの反復・
決定的な合否・実行前の再インストール確認。

## 何を本番と同じに保つか

測っているものが本番と別物にならないよう、3 点だけ固定する。ここを変えたら本番を
測っていない。

| 項目 | 正典 |
|---|---|
| argv | `agents/ollama.json` の `write_args`（起動時に**読む**。写さない） |
| プロンプト | flow-worker スキルの `scripts/prompt.py`（agent-flow が実際に呼ぶビルダー） |
| 上限 | agent-flow の `agent_timeout` 既定 600 秒。超過は fail |

worktree はリポジトリの外（`$TMPDIR/agent-worker-eval`、`WORKER_EVAL_DIR` で変更可）に作る。
中に作ると評価の残骸が作業ツリーへ漏れる。

argv は初版で `WRITE_ARGS` へ literal を写していたが、**定義側が予算を 30 → 12 へ絞った
当日にずれた**。写しは人の注意力に頼る不変条件で、ずれても静かに測定が別物になるだけ
なので、いまは起動時に `agents/ollama.json` を読む（読めた出所を起動行に必ず表示する）。

## エンジンへ触る口は 1 つ（`engine.py`）

ハーネスは本番の実装を**呼ぶ**。写しは定義側の変更に静かに置いていかれるからで、argv を
写して予算の変更に取り残された前科がある。いっぽう直接呼ぶと、**まだ着地していない
シンボル**へ触れた瞬間に `AttributeError` で全 run が起動前に死ぬ——`LIST_CONTRACT_ROLES`
と `unwrap_list` で 2 度踏んだ。

だから解決を `engine.py` へ寄せて、3 つを守る。

- **欠けても走る。** 使えない機能はその機能だけを落とし、残りの測定は続ける。
- **黙って落とさない。** 欠けた機能は起動行に出し、台帳の各行へ `engine_missing` として
  残す。**欠けた木で取った数字を、揃った木の数字として読ませない**（0/6 が「モデルの
  不合格」なのか「振り替えが無い木」なのかは、後から台帳だけで区別できないと困る）。
- **写さない。** 揃っている限り本番の実装をそのまま使う。`engine.py` に再実装は書かない。

不変条件は `test_engine.py` が持つ——落ちないことと、**欠けたと言うこと**の両方を見る。
片方だけでは足りない（黙って倒れる実装は、事故そのものより読み違いを増やす）。

## 判定役を使わない理由

qwen3.5:9b の品質劣化の真因は偽 done だった。自分の仕事を自分で「できた」と申告し、
同じモデルの gate がそれを承認していた。**自己申告を合否に混ぜると、測ろうとしている
故障そのもので測定が壊れる。** だからチェッカーは成果物だけを見る。

- T1 — 実装した関数の振る舞いを境界 6 ケースで突き合わせる（実装は読まない）
- T1min — T1 からテスト追加の契約を外した最小セル。粒度で救えるかの切り分け用
- T2 — 落ちるテストを渡して直させる。**テストファイルのハッシュを前後で照合し、
  書き換えて通したらズルとして落とす**
- T3 — 本番で receipt 4/4 fail だった実タスク。C1（schema が妥当）と C3（契約テストが通る）を機械判定

**多段セル**（`steps` を持つタスク）は、1 課題を複数の呼び出しへ割り、手順の境目に
決定的なゲートを置く。ゲートが落ちたら、測った不一致を課題文へ足して同じ手順をやり直す。
判定は一発版と同じチェッカーなので直接比較できる。1 回の呼び出しの argv は本番の
`engine.headless_cmd` そのままで、手順制御だけがハーネス側の模擬——測るのは分解と
ゲートの効果であって、agent-loop の statemachine 実装ではない。

- T1seq — T1 を 2 手順（実装 / テスト追加）へ割っただけ。ゲートは `max_retries=0` で
  **作用せず**、どの手順で壊れたかを台帳へ残すだけ。分解そのものの効果を見る
- T1gate — 同じ 2 手順にゲートと再試行を効かせる。機械検証と再投入の効果を見る
- T1impl_diag / T1impl_blind — 実装ステップだけの対照。再試行の回数は同じで、
  渡す材料が「測った不一致」か「落ちたという事実だけ」かが違う

チェッカー自体も検証してから使うこと。仕込み直後に落ち、正解を置くと通り、ズルを弾く——
この 3 点を確認しないと、ハーネスの不具合をモデルの不合格として報告してしまう。

## 結果の読み方

台帳は 1 run 1 行の JSONL（`ledger.jsonl`）。`ok` が合否、`mode` が失敗様式。

| mode | 意味 |
|---|---|
| `timeout` | 上限内に終わらなかった |
| `returned` | 完走したが成果物が基準を満たさない |
| `self_reported_incomplete` | `{"ok": false}` を自分で付けて未完了を申告した |
| `cli_error` | CLI が非 0 で終了 |
| `empty` | 本文が空 |

`timeout` が多いなら予算か暴走、`returned` が多いなら能力。この 2 つを取り違えると
打ち手を間違える。

### n の読み方（2026-08-23・計画 2026-08-22 §4.2 C6）

本書の結論はほぼ **n = 3** で、これは率ではなく**存在の証明**である。線引きを 3 つ置く。

- **`3/3` は「起きる」、`0/3` は「起きない」の証拠としてだけ読む。** 100% / 0% とは書かない。
  n = 3 の 95% 区間は 3/3 でも下端 44%、0/3 でも上端 56% である（Wilson）。
- **n = 3 同士の差は「全滅 ⇔ 全通」だけを差として読む。** 1/3 と 2/3 の差は雑音で説明できる。
  P10 の「T1 0/3 → 1/3」は「揺れた」の証拠であって改善率ではない——本文もそう書いてある。
- **率として比較したいなら同じ腕で n ≥ 10 を引く**（区間幅が ±30% を切る）。12b の暴走 2/27 は
  区間 2〜23% で、「発生率の推定」としてはまだ粗い（存在の証明 + 上限の目安）。

台帳は常に `k/n` の形で残し、`n` を落とした要約を作らない。

## 2026-08-10 の実測 — qwen3.5:9b

**受入 2/21 で不合格。** 独立な 3 本のレバーを引いて全部空振りしたので、原因は
予算でも粒度でも暴走でもなく能力と判断した。

| 引いたレバー | 受入 |
|---|---|
| 基準（600 秒） | 1/9 |
| 予算 3 倍（1800 秒） | 0/3 |
| 粒度を極小（1 ファイル 1 関数・テスト契約なし） | 1/3 |
| 暴走止め（`num_predict=4096`） | 0/6 |

様式は timeout 12 / returned 4 / cli_error 3 / 自己申告未完了 2。decode は 11〜12 tok/s で、
600 秒の予算は約 7000 トークンに相当する。台帳は `ledger-2026-08-10-qwen35-9b.jsonl`。

失敗の中身を見ると、**21 本中 9 本は成果物のファイルが 1 つも無い**（`eval/humansize.py が無い`）。
書いたが誤っていたのは構文 3・振る舞い 1 で、「間違ったコードを書いた」より
「**書き終える前に予算が尽きた**」が主である。7000 トークンという decode 予算は、
読んで・考えて・ファイルを書いて・テストを回す往復には足りない。

**この台帳は `--max-rounds 30` 時点の測定である**（現行の定義は 12）。予算 3 倍でも
0/3 だったので結論は動かないが、再測するときは条件が違うことを踏まえること。

副産物としてエンジンの穴が 2 つ出た。どちらもモデルを替えても残るので先に直してある——
`format` と `think` の併用（本文が空になる）と、1 ラウンドの生成に上限が無いこと
（10 tok/s で 19771 トークンまで書き続けた例がある）。詳細は
[適用拡大設計 §4.2・§10.2](../../../docs/plans/2026-08-08-agent-ollama-expansion-design.md)。

## 2026-08-11 の実測 — worker の不合格は**エージェント層の問題**でもあった

2026-08-10 の「受入 2/21・原因は能力」は、**エージェント層を agent-ollama に固定したまま
出した結論だった**。層を aider に替えて同じ課題・同じチェッカーで測り直すと、結論の一部が
崩れる。

```bash
python3 tools/agent-tools/eval/worker_eval.py --cli aider --model gemma4:e4b \
  --tasks T1min,T1,T2,T3 --repeat 3
```

### Gemma 4 reliability policy A/B

本番の `agents/aider.json` は `gemma4-e4b-reliability-v1` を有効にする。比較評価では、同じ task、
model、wall limit、checker を保ったまま `--agent-policy off` と policy arm を分ける。

```bash
python3 tools/agent-tools/eval/worker_eval.py --cli aider --model gemma4:e4b \
  --agent-policy off --tasks T2,T1min --repeat 3
python3 tools/agent-tools/eval/worker_eval.py --cli aider --model gemma4:e4b \
  --agent-policy gemma4-e4b-reliability-v1 --tasks T2,T1min --repeat 3
```

台帳には `policy_id` と adapter の `@agent-policy` marker から取得した `policy_sha256` に加え、
Aider version、実効 context / 生成上限、token usage、checker 診断、wall limit、retry 回数が残る。
`--agent-policy` 未指定時は本番定義を継承する。sampling は policy と同時に変えず、独立した
`--agent-policy off` arm として測定する。

| 課題 | qwen + agent-ollama | qwen + aider | gemma4:e4b + aider |
|---|---|---|---|
| T1min（1 ファイル 1 関数） | 1/6・中央値 600s | 0/3・中央値 **76s** | 1/3・中央値 **130s** |
| T1（実装 + テスト追加） | 0/9・中央値 600s | — | 0/3・中央値 600s |
| T2（落ちるテストを直す） | 1/3・中央値 598s | **3/3**・中央値 173s | **3/3**・中央値 97s |
| T3（実タスク・複数成果物） | 0/3・中央値 364s | — | 0/3・中央値 446s |

**T2 は同じモデルのまま 1/3 → 3/3 になった。** 変えたのは層だけである。つまり
agent-ollama の tools ループが不合格の一因で、**モデルの能力だけが原因ではなかった**。
aider が効くのは 3 点——編集対象をチャットに固定するので文脈が探索で溶けない、
~~diff 形式で返させるので全文再生成の decode を払わない~~、
`--test-cmd` の直し直しがシェル往復を挟まない。
7000 トークンの予算を「読んで・探して・書く」で使い切っていたのが agent-ollama 経路だった。

**2026-08-29 訂正: 2 点目は成り立っていない。** 実 aider の起動を確認したところ、
`ollama_chat/gemma4:e4b` は **`whole` 編集形式**（ファイル全文を返させる）で走っていた
——`Model: ollama_chat/gemma4:e4b with whole edit format`。本番定義（`agents/aider.json`）も
現行の eval も `--edit-format` を宣言しておらず、aider のモデル別既定に従っている
（`edit_format: diff` を書くのは `_aider_argv_legacy` だけで、これは比較用に残した旧経路）。
したがって「全文再生成の decode を払わない」は現行構成の説明になっていない。
残る 2 点（材料の固定・`--test-cmd` の往復削減）は変わらない。

**編集形式を腕として測るのは別の話**である。ここは「なぜ効いたか」の説明が 1 つ
間違っていたという訂正で、`diff` にすれば良くなるという主張ではない——
未決 5 の対照実装（`editblock.py`）は SEARCH/REPLACE 側だったが aider を上回らなかった。

**残るのはモデルの中身の誤り。** T1min は qwen 0/3・gemma 1/3 で、どちらも単位の繰り上げを
1 段ずらす（`1024.0 MiB` / `units[unit_index-1]`）。層を替えても直らない。ただし
**失敗が安くなった**——600 秒の timeout から 76〜130 秒の returned へ変わり、同じ予算で
何度も試せる。

**複数成果物は層を替えても落ちる。** T1（実装 + テスト）は 0/3 で全部 600 秒に張り付く
（`--auto-test` のループが収束しない）。T3 は schema までは作るが契約テストが出ない。
2026-08-10 の「書き終わる前に予算が尽きる」とは形が違い、**速く書いて速く直し続けて
収束しない**。

### 測る前に踏んだ落とし穴 2 つ（どちらもハーネス側）

- **aider にファイルを渡していなかった。** aider はチャットに入っているファイルしか編集
  しない。渡さないと本文で「追加してくれ」と要求して終わる——`--message` は一発なので
  答える人がいない。**課題に着手すらしていない run を 6 本、モデルの不合格として数えかけた。**
  いまは課題ごとに編集対象（`files`）とテスト等の読み取り専用（`--read`）を渡す。
- **aider は文脈長を自分で決める。** 何も指定しないと ollama は `CONTEXT 9640` でモデルを
  載せ、モデル設定ファイルで `num_ctx: 32768` を渡すと 32768 で載る（`ollama ps` で見える）。
  T2 はどちらでも 3/3 なので**小さい課題では効かない**が、材料が増える課題では効く。
  必要な課題だけ `--model-settings-file` で上げる。リポジトリマップは既定で切る
  （1,777 ファイルのマップだけで文脈が尽きる）——探索が要る課題（T3）だけ予算を与える。

### 収束しない課題を安く切る — 上限は「回数」ではなく「1 ターンの生成」

T1 は 3 本とも 600 秒に張り付いた。**原因は直し直しの無限ループではない**——aider は
`max_reflections = 3` で止まる（CLI フラグは無く、実測でも LLM 呼び出しは 3〜4 回）。
焼いていたのは **1 ターンの生成の長さ**で、最後のターンが受信 3.7k トークン、
26.5 tok/s で約 140 秒である。

そこで `--num-predict`（aider のモデル設定 `extra_params.num_predict`）で上限を引いた。
**収束する課題を対照群に置く**——上限は失敗を安くすると同時に、合格を壊しうる。

| 1 ターンの上限 | T1（収束しない） | T2（収束する） |
|---|---|---|
| なし | 0/3・**600s**（timeout） | 3/3・**97s** |
| 2048 | 0/3・**218s** | 3/3・195s |
| 1024 | 0/3・278s | **0/3**・45s（編集が切れて壊れる） |

1024 は効きすぎる。2048 は T1 を 2.75 倍安くするが、**T2 の壁時計も倍**にする——切られた
編集を直すために往復が増えるからで、合格する課題ほど損をする。

**既定にはしない。効かせるなら壁時計のほうを絞る。** T2 は上限なしで 97〜120 秒に収まる
ので、`--wall 240` なら収束する課題に触れずに T1 の 600 秒だけを切れる。本番では
agent-flow の `agent_timeout` がその口で、ローカルモデル + aider の組み合わせでは
600 秒は緩すぎる。`--num-predict` は残してあるが、**対照群を置かずに引くレバーではない**。

### 定義ファイル化（2026-08-11）

T2 型が確実に取れるので、aider を正典の CLI 定義にした（`agents/aider.json`）。ハーネスは
argv を写さず**この定義を読む**——写しは定義側の変更に静かに置いていかれる（agent-ollama
経路で実際に起きた）。同じ定義で測り直して **T2 3/3・中央値 105 秒**。

ファイルの受け渡しは CLI 定義の契約に足した（`file_flag` / `read_flag`）。宣言した CLI に
だけ載り、宣言しない CLI の argv は 1 トークンも変わらない（`test_agentcli_files.py`）。
「aider はチャットに入っているファイルしか編集しない」を**呼び出し側の注意力に頼らせない**
ための口で、渡し忘れは定義の `errors` が env 起因として拾う（`add … to the chat`）。

課題ごとに違うもの（`--test-cmd` + `--auto-test`、探索が要る課題の `--map-tokens`）だけを
ハーネスが足す。定義は `--map-tokens 0` 固定なので、上書きするときは**フラグを消してから
足す**——同じフラグを 2 回並べて後勝ちに賭けると、定義側が並び順を変えた日に静かに 0 へ戻る。

台帳は `ledger-2026-08-11-worker-aider.jsonl`（`cli` と `model` 列で腕を分ける）。

gemma4:e4b の素の速度は decode 26.5 tok/s・warm TTFT 0.67 秒（qwen は 11.7 tok/s・約 7 秒）、
常駐 3.3 GB。**同じ 600 秒の予算が 7000 → 16000 トークン相当になる。**

## 2026-08-11 の実測 — 本番プロンプトの実寸と時間の内訳（LLM 呼び出しなし）

[次の計画 §4](../../../docs/plans/2026-08-11-agent-ollama-next-eval-plan.md) の先行確認。
既存ログ 265 本を読むだけで出る。

```bash
python3 tools/agent-tools/eval/log_stats.py
```

**「1 発判断は中央値 9.3 秒」は本番を代表していない。** その 6 本のプロンプトは中央値
14 文字（入力 16〜21 トークン）で、本番の判定系は 2,500〜5,100 文字（1,300〜2,300
トークン）である。ただし**外した理由は計画の想定と違った**。

| 計画 §4 の想定 | 実測 |
|---|---|
| 9.3 秒の大半は prefill。本番のプロンプト長では桁が変わる | prefill はほぼ効かない。TTFT は入力 1k でも 16k でも中央値 6〜13 秒で**横ばい** |
| 判定・分類の出力は 10〜50 トークン | 本番の evaluator 312・planner 437・analyst 281 トークン。10〜50 は adjudicate（16）だけ |

つまり時間を決めているのは入力長ではなく**出力長**である。机上計算の係数は 2 つ。

```
所要秒 ≒ TTFT(中央 7 秒・p90 25 秒) + 出力トークン / 11.7
```

decode は文脈 1k〜16k+ で 11.4〜11.9 tok/s と平坦（p10 でも 8 tok/s）。実測との突き合わせ
——`plain・think on・format json`（本番の判定系の形）は入力 2,265・出力 312 で
7 + 27 = 34 秒、実測の中央値 35.1 秒と合う。

役割別の実寸（`run sec` は run 全体の壁時計）。

| 役割 | n | chars p50 | in p50 | out p50 | run 秒 p50 / p90 |
|---|---:|---:|---:|---:|---:|
| flow:filter | 2 | 14,798 | 6,166 | 2,303 | — / 507 |
| flow:evaluator | 39 | 5,118 | 2,265 | 312 | 36 / 264 |
| flow:analyst | 6 | 4,914 | 2,279 | 281 | 45 / 120 |
| flow:planner | 19 | 4,353 | 1,981 | 437 | 19 / 83 |
| flow:generate | 69 | 2,639 | 1,489 | 1,489 | 120 / 492 |
| flow:worker | 49 | 2,558 | 1,457 | 3,583 | 250 / 1,180 |
| project:verifier | 10 | 2,545 | 1,304 | 5,187 | 267 / 966 |
| flow:adjudicate | 3 | 69 | 35 | 16 | 2 / 5 |

判定系のハーネスに効くのは 3 点。

- **入力長は本番に合わせるが、そこは高くつかない。** 小さい入力で測る落とし穴は
  「速いと誤認する」ことだったが、実際に誤認の元は出力長のほうだった。
- **1 件あたり 35 秒前後を見込む。** 100 件 × 3 反復で約 3 時間（計画 §6 段 3 の見積 1〜2
  時間より長い）。縮めるなら反復か件数を削る。出力を短く縛るのは本番と別物を測ることになる。
- **直列で回す。** TTFT の p90 は p50 の 3〜4 倍まで伸びるが、これは入力長ではなく
  他プロセスとの取り合いで動く。worker_eval と同じく ollama を占有しないと数字がぶれる。

`flow:filter` の入力 6,166 トークンが本番の最大で、それでも TTFT は数秒。**入力長を理由に
落とせる候補は無い**——候補の足切りは出力長で行う。

## 2026-08-11 の実測 — 判定・分類の基準線（qwen3.5:9b）

[次の計画 §3](../../../docs/plans/2026-08-11-agent-ollama-next-eval-plan.md) のハーネス。
worker_eval の型を継ぎ、正解は**入力を作る規則から従う**（構成的ラベル・人の確認ゼロ）。

```bash
python3 tools/agent-tools/eval/judge_eval.py --selfcheck   # チェッカーの自己診断（LLM 無し）
python3 tools/agent-tools/eval/judge_eval.py --repeat 3    # 基準線
```

**合計 14/30。役割で割れた。**

| 役割 | 受入 | 中身 |
|---|---:|---|
| evaluator | 4/6 | done / replan の判断そのものは正しい。落ちた 2 件は JSON が壊れた 1 件と、要求外の「スケジュール設定が無い」を理由にした過剰 replan 1 件 |
| reduce | 4/6 | 単純な畳み込みは 3/3。**重複除去を伴うと 1/3**（重複を消すついでに別要素まで落として 7 件にする） |
| judge | 3/6 | 基準が 2 段（テスト通過 → 依存の有無）なら 2/3。基準を 1 つに絞った J2 のほうが 1/3 と悪い——**指示に無い基準を自分で足す** |
| filter | 3/6 | 「テストが通るもの」は 3/3。「依存を増やさないもの」は 0/3 で、前段の基準を勝手に重ねて 1 件しか残さない |
| split | 0/6 | **モデルの不合格ではない**（下記。エンジン修正後は 4/6） |

**split の 0/6 はエンジン側の契約不整合である。** ollama の JSON モード
（`--format json`）は**トップレベルを必ずオブジェクトにする**。同じプロンプトで確かめた:

```
--format json あり → {"data":["1-25","26-50","51-75","76-100"]}
--format json なし → ["1-25", "26-50", "51-75", "76-100"]
```

器の形は 1 つに定まらない。ハーネスの S1/S2（上の例より長いプロンプト）では
`{"1-250": "251-500", ...}`（要素をキーへ）・`{"0": "1-250", ...}`（添字をキーへ）・
`{"group_1": "ingest.py,normalize.py", ...}` が混在した。**受け側を寛容にするだけでは
「キーと値のどちらが答えか」を決められない**——ここを推測で剥がすと、黙って別の分解で
fan-out することになる。だから修正は器の推測ではなく、配列を表現できる起動形の追加になる。

いっぽう `agent-flow` は split の成果を `isinstance(data, list)` で受け、配列でなければ
形式修復リトライ（`format_retries=1`）を回す。split は `JSON_CONTRACT_ROLES` に入っている
ので `ollama-json` へ振られる——つまり**契約が原理的に満たせない役割へ、満たせない修復を
1 回余分に払っている**。診断用に `--format json` だけ外すと 0/6 → 3/6 になる。

```bash
python3 tools/agent-tools/eval/judge_eval.py --cases S1,S2 --repeat 3 --drop-format
```

`--drop-format` は本番の argv から外れるので**基準線には使わない**（この行だけ測定条件が違う）。

**修正済み（同 2026-08-11）。** 配列契約の役割（`LIST_CONTRACT_ROLES` = split）を、
structured outputs のスキーマ `{"type":"array","items":{"type":"string"}}` を渡す
`agents/ollama-list.json`（`--format array`）へ振り替えるようにした。CLI 定義側は
`list_variant` で申告する（無ければ `json_variant` へ落ちる）。同じハーネスで再測すると
**0/6 → 4/6**、中央値 12 秒 → 2 秒（空振りする修復リトライを払わなくなった分）。
残る 2 件は器ではなく中身の取りこぼし（8 ファイル中 1 件を落とす）で、モデル側の課題。

```
S1 (split): 3/3   S2 (split): 1/3   合計 4/6   空・形式違反 0/6
```

gemma4:e4b でも同じ 4/6・中央値 2 秒。**再測の前に `bash tools/agent-tools/install.sh` を
回すこと**——`agent-ollama` は zipapp なので、`--format array` を足しても再ビルドするまで
古い実行ファイルが使われ、6 run 全部が `cli_error` になる（実行前のチェックの 1 番目）。

読み方は 3 点。

- **判定の中身は無傷ではない。** 空応答は 0 だが、JSON の破損が 3/30（全角の閉じ引用符・
  途中で終わる）、指示に無い基準の追加が filter と judge の両方で出た。9.3 秒の実行が
  「1 発判断は成立している」の根拠にならないのは、時間ではなくここが理由である。
- **自己一貫性は低い。** 同じ入力を 3 回引いて答えが揃ったのは 10 ケース中 3 つだけ。
  多数決が効く余地はあるが、3 回引けば所要時間も 3 倍になる。
- **この基準線は上振れ側。** 入力は中央値 850 字で、本番の判定系（2,500〜5,100 字）より
  短く綺麗である。ここで割れているなら本番ではもっと割れる。

台帳は `ledger-2026-08-11-judge-qwen35-9b.jsonl`（実行時の出力先は `/tmp/agent-judge-eval/`。
`JUDGE_EVAL_DIR` で移せる）。

## 2026-08-11 の実測 — 判定系を gemma4:e4b で引き直す

同じハーネス・同じ 10 ケースで `--model` だけ替えた。**合計 17/30（qwen は 14/30）。**
**この測定は配列契約の振り替えが入る前に取ったので split は両者 0/6**、そこを除くと
**17/24 対 14/24** である（振り替え後の split は gemma4:e4b でも 4/6）。

```bash
python3 tools/agent-tools/eval/judge_eval.py --model gemma4:e4b --repeat 3
```

| 役割 | qwen3.5:9b | gemma4:e4b |
|---|---:|---:|
| reduce | 4/6 | **6/6** |
| evaluator | 4/6 | **5/6** |
| filter | 3/6 | 3/6 |
| judge | 3/6 | 3/6 |
| split | 0/6（エンジン） | 0/6（エンジン） |

- **壁時計が桁で違う。** 中央値 4.5 秒・最大 26.8 秒（qwen は中央値 2〜45 秒、J2 で 81 秒の
  例もあった）。判定系を 3 回引いて多数決を取っても、qwen の 1 回より速い。
- **形式の破損が 0/30。** qwen は全角の閉じ引用符や途中終了で 3/30 落としていた。
- **基準の取り違えは同じ場所で残る。** F2（依存の有無だけを訊く）は 3 本とも同じ誤答で、
  **自己一貫性 3/3**——迷って割れているのではなく、確信して間違える。ここは多数決が
  効かない側の失敗であり、qwen と同じ結論になる。
- **judge の落ち方が鏡写し。** qwen は基準 2 つの J1 を取り基準 1 つの J2 を落とし、gemma は
  逆に J2 を 3/3 で取り J1 を 0/3 で落とす（テスト通過かつ最短の c4 を選び、依存を増やす
  という 2 つ目の基準を落とす）。**基準を 2 つ以上重ねると、どちらのモデルもどれかを捨てる。**

台帳は `ledger-2026-08-11-judge-gemma4-e4b.jsonl`。

## 2026-08-11 の実測 — 手法パックで判定の質は上がるか

`--methods` でカタログ（`methods/*.json`）の手法を有効化して同じ 8 ケースを引く。適用条件の
判定は本番の `agentcore.methods.select` をそのまま呼ぶので、`when` に合わない役割へは注入
されない。本番 context のうちここに無いのは段（tier）だけで、`--tier`（既定 `small`）で名乗る。

```bash
python3 tools/agent-tools/eval/judge_eval.py --repeat 3 --cases F1,F2,J1,J2,R1,R2,E1,E2 \
  --methods restate-task,output-contract-strict,checklist-acceptance
```

**合計は動かない。動いたのは出力の壊れ方である。**

| 条件 | 合計 | filter | judge | reduce | evaluator | 復唱キーの混入 |
|---|---:|---:|---:|---:|---:|---:|
| 手法なし | 15/24 | 1/6 | 3/6 | 5/6 | 6/6 | 0/24 |
| 手法あり（`restate-task` を含む） | 16/24 | 1/6 | 4/6 | 5/6 | 6/6 | **3/24** |
| 手法あり（`restate-task` を除外後） | 16/24 | 2/6 | 2/6 | 6/6 | 6/6 | 0/24 |

`restate-task`（「着手前にタスクを3行以内で復唱し、作る成果物の形式を明示してください」）は
`when` が段だけの宣言で、`role_for` が split / filter / judge / reduce / extract を worker へ
落とすため、これらへも入っていた。これらは `agents/ollama-json.json` で起動され**出力全体が
1 個の JSON に縛られる**ので、復唱は本文ではなくキーになる:

```
{"task_restatement": "...", "output_format_contract": "...", "kept": ["c1","c3"]}
{"task_restated": "...", "output_form_justification": "..."}
```

**合計の差は読まないこと。** 同じ 8 ケースの手法なし基準線は測るたびに 14 / 15 / 17（いずれも
24 中）で、1 点差はこの幅の中に沈む。判断材料になるのは混入の 3/24 → 0/24 のほうで、合計は
「悪化していない」以上を言わない。

**カタログ側を直した。** 出力へ何かを書かせる手法（`restate-task` / `plan-first` /
`spec-first`）の `when.purposes` を散文の kind（work / generate / synthesize）に限った。
機械が成果を解釈する kind を外すのは、混入のほかにもう 1 つ理由がある——`extract_json` は
`[`…`]` を `{`…`}` より先に試すので、前置きが付くとオブジェクトの成果が入れ子の配列へ化ける
（`{"items":[1,2],"count":2}` は前置き付きで `[1,2]` になり count が消える）。この探索順自体は
手法とは独立した既存の弱点で、ここでは触っていない。

台帳は `ledger-2026-08-11-judge-methods-qwen35-9b.jsonl`（混入が出ている修正前の 24 行。
`answer` 列に上記のキーが残っている）。以後の測定では、どの行にどの手法が効いたかが
`methods` 列に入る——宣言した手法が `when` で落ちて 1 つも効いていない実行を、効いた前提で
数えないため。同じ理由で、実行前のヘッダにも役割ごとの適用結果を出す。

### 狙い撃ちの規律も効かない — filter / judge はプロンプトでは直らない

基準の取り違え（訊かれていない基準を持ち込む）を名指しで潰す候補プリセットを作り、
同じハーネスで測った。カタログには入れていない——`methods/` は golden で件数もハッシュも
固定されているので、**採否を決める前の候補はカタログの外で測る**（`--methods` は
`.json` のパスも受ける）。

```bash
python3 tools/agent-tools/eval/judge_eval.py --repeat 3 --cases F1,F2,J1,J2,R1,R2 \
  --methods tools/agent-tools/eval/method-candidate-criteria-fidelity.json,output-contract-strict
```

`criteria-fidelity`（`when.purposes` を filter / judge / reduce / split / classify に限った）で
filter 3/6・judge 3/6・reduce 6/6。**狙った 2 つが動かない。** F2（依存の有無だけを訊く）は
3 本とも、J2（行数だけを訊く）は 2/3 で、いまだに前段の「テストが通る」を持ち込む。
reduce だけ上がったが、上の注意書きどおり**この幅の差は読まない**。

**多数決も効かない。** 台帳の 3 回分を突き合わせると、F2 は `[c1,c3]` `[c3,c6]` `[c3]` で
多数決を取ると `c3`、J2 は `c3,c3,c4` で `c3`——どちらも不正解へ収束する。
**割れ方が正解の周りに散っていない**ので、引き直しでは埋まらない。ここは合計点ではなく
答えの中身を見ているので、上の再現幅とは別に読んでよい。

独立なレバーを 4 本引いて（素・既存プリセット・狙い撃ちの候補・多数決）どれも
filter / judge を動かさなかった。worker のときと同じ判断をする——**基準の取り違えは
プロンプトでは直らない。** 打ち手は 2 つに絞られる。

- 基準を機械が判定できる形に組み替える（決定的な前処理で候補を削り、モデルには残りだけを
  訊く）。誤りが存在チェックで無害化される「候補生成 + 決定的検算」と同じ筋。
- 判定そのものをクラウドへ戻す。

evaluator（6/6）と reduce、および基準が 1 つで材料に明示されている filter（F1 は 3/3）は
この結論の対象外——**そこは使える。**

台帳は `ledger-2026-08-11-judge-methods-arms.jsonl`（`arm` 列で腕を分ける）。候補プリセットは
`method-candidate-criteria-fidelity.json` に残してある（効かなかったのでカタログには入れない）。

**測定の衛生を 1 つ。** この 2 本は別セッションのハーネスと ollama を共有した時間帯があり、
壁時計は信用できない（正解率は run ごとに独立なので有効）。実行前のチェックの 2 番目は
他プロセスだけでなく**他セッションのハーネス**にも当てはまる。

## 2026-08-11 の実測 — 埋め込みは現行の検索を上回るか（bge-m3）

[品質再点検 §6 案 d](../../../docs/plans/2026-08-10-agent-ollama-quality-and-role-refit-proposals.md)
の検証。生成品質の袋小路とは独立に進められる筋で、**客観指標で決着する**のが利点。

```bash
python3 tools/agent-tools/eval/retrieval_eval.py --model bge-m3 --k 5
python3 tools/agent-tools/eval/retrieval_eval.py --tfidf-only   # モデル未取得でも基準線は出る
```

コーパスは ltm-use の実記憶 64 件 + リポジトリの設計書・計画書 146 件（**妨害文書**）。
正解は記憶側にだけ置き、パスで固定する。基準線は現行実装そのもの（ltm-use の
`similarity.py`・日英混合の TF-IDF）。

**同じ 20 問を 2 通りの訊き方で引く。** 記憶の用語をそのまま使う `lexical` と、
用語を思い出せず意味だけで探す `paraphrase`。ここを分けないと結論を取り違える。

| 訊き方 | 腕 | hit@1 | hit@5 | MRR |
|---|---|---:|---:|---:|
| lexical | TF-IDF（現行） | 85% | 95% | 0.900 |
| lexical | bge-m3 | 80% | 100% | 0.900 |
| lexical | RRF 併用（対等） | 90% | 100% | 0.950 |
| paraphrase | TF-IDF（現行） | 30% | 35% | 0.328 |
| paraphrase | **bge-m3** | **50%** | **60%** | **0.576** |
| paraphrase | RRF 併用（対等） | 40% | 40% | 0.420 |

- **用語を覚えているうちは現行で足りる。** lexical で hit@5 95%。ここに埋め込みを足す
  理由は無い（MRR は同点）。
- **用語を忘れた瞬間に現行は崩れる。** paraphrase で 35%。正解が 120 位・196 位まで沈み、
  1 位を妨害文書に取られる。bge-m3 は hit@5 を 60%・MRR を +0.248 押し上げる。
- **素朴な RRF 併用は害になる。** 対等だと 60% → 40%。paraphrase では TF-IDF の順位が
  ほぼ雑音で、等しく混ぜると埋め込み単独より悪くなる。重みを 5:1 まで振ると hit@5 は
  60% へ戻るが hit@1 は 30% へ落ち、MRR は 0.416 と単独（0.576）に届かない
  ——**混ぜて得はしない。訊き方で振り分けるか、用途を分けるほうが筋が良い。**

費用は索引 210 件で 130 秒（1024 次元）、クエリ 1 回 170 ms（TF-IDF は 7 ms）。
記憶の規模なら索引は毎回作り直しても許容範囲で、キャッシュすれば再測は数秒。

**測り方で 1 度やり直した。** 初版は記憶 64 件だけを対象にしていて、TF-IDF が
hit@1 95%・hit@5 100% と天井に張り付き、どの腕も差が出なかった。小さいコーパスと
用語の重なるクエリでは**現行が強すぎて比較にならない**——妨害文書と言い換えクエリを
入れて初めて指標が働いた。

絶対値では paraphrase 60% なので、埋め込みを入れても「思い出せない検索」が解決する
わけではない。効果があるのは確かなので、宛先（ltm-use の recall・agent-audit の材料選別・
artifacts の参照解決）と索引の持ち方は設計書に切る。リランカーは ollama に rerank の口が
無いため、この測定には含めていない。

**2026-08-23 追記 — 段構えを実装し、同じハーネスで受け入れた。** 設計書
（[ltm-use-embedding-recall-design](../../../docs/designs/ltm-use-embedding-recall-design.md)）の
腕「TF-IDF の最上位コサインが 0.11 未満のときだけ bge-m3」を `cascade_ranker` として足した
（`--cascade-threshold` で掃引できる）。再測（261 件・妨害込み）: lexical 90% / 100% / 0.950、
paraphrase 50% / 60% / 0.560——bge-m3 単独と同じ精度を、lexical 側の経路を 1 行も変えずに出す。
本番経路（ltm-use の `recall_memory.search_with_index`・実記憶 75 件）でも lexical hit@5
80% → 95%・paraphrase 25% → 85% で、受け入れ基準（paraphrase ≥ 55% かつ lexical ≥ 95%）を
両空間で満たした。本番の TF-IDF は title / summary / tags だけで作るためハーネスより弱く、
しきい値未満へ落ちる lexical 問が 6/20 あったが、埋め込みで拾えたので下がらない。

## 2026-08-13 の実測 — 事前分解は効くか（tier:basic / gemma4:e4b × aider）

```bash
python3 tools/agent-tools/eval/worker_eval.py --model gemma4:e4b --cli aider \
  --tasks T1,T1seq,T1gate --repeat 3
```

| アーム | 受入 | 呼び出し | 壁時計 中央値 |
|---|---:|---:|---:|
| T1（一発） | 1/3 | 3 | 446s |
| T1seq（分解のみ） | 0/3 | 6 | 237s |
| T1gate（分解 + ゲート + 再試行） | 3/3 | 12 | 952s |

**分解だけでは上がらない。下がる。** T1seq の 3 本はすべて実装ステップで同一の壊れ方
（KiB で止まり MiB / GiB へ繰り上がらない）をした。実装ステップの初回失敗は全実行を
通算して 13/13——ばらつきではなくこのモデルの決定的な癖で、課題文に
`human_bytes(1048576)=='1.0 MiB'` と書いてあっても直らない。**散文の仕様例はオラクルとして
機能しない。** 機械が測った不一致を突きつけて同じ手順をやり直させると 4/4 で通った。

対照 2 本（`--tasks T1impl_blind,T1impl_diag`）は両方 3/3。診断文を渡すと再試行が 28% 速い
（中央値 107s 対 148s）が、受入は変わらない。効いているのは **決定的な検知** と **再投入**
であって診断の中身ではない。ただし検査を省いて常に 1 回やり直す形には倒せない——停止条件が
消え、done の根拠が自己申告へ戻る。**検査は要る。返す値は真偽で足りる。**

詳細と現行 statemachine への転用可否は `results/archive/2026-08-13-t1-decomposition-report.md`。

## 推論条件の腕 — sampling と Thinking（計画 P10）

過去の測定は**推論条件を一度も明示していない**。この 2 つは同じ性格の交絡なので、
同じ対照群で 1 度に測る。どちらも**未指定なら 1 バイトも宣言しない**ので、
既存の台帳と同じ条件がそのまま再現される（腕を足しただけで既定は変えていない）。

### sampling（コード worker 経路）

`agents/aider.json` は温度を指定しておらず、`aider_settings()` も渡していなかった。
つまり**実効値は aider の既定**で、我々はそれを確認したことがない。これは結論に効く——
T1 の実装ステップは初回 13/13 が同一の壊れ方をしたが、**貪欲デコードなら同じ入力に
同じ出力が返るのは当たり前**である。「決定的な癖」がモデルの性質かサンプリングの性質かは
まだ分離できていない。

```bash
# 基準線（従来と同一条件。何も宣言しない）
python3 worker_eval.py --model gemma4:e4b --cli aider --tasks T1,T2 --repeat 3

# sampling 腕（例。Gemma 3 の推奨値。Gemma 4 の推奨は要確認）
python3 worker_eval.py --model gemma4:e4b --cli aider --tasks T1,T2 --repeat 3 \
  --temperature 1.0 --top-p 0.95 --top-k 64
```

- **対照群（T2）を必ず入れる。** 上限や温度は失敗を安くすると同時に合格を壊しうる
  （`--num-predict 1024` が T2 を 3/3 → 0/3 にした前科がある）。T1 だけ回して
  「良くなった」と読むのは、この轍を踏み直すことになる。
- sampling 腕の設定ファイルは**宣言したものだけ**を書く（`aider_settings(base=False)`）。
  `edit_format` / `use_repo_map` / `num_ctx` を巻き込むと、測っているのが
  「温度の効果」ではなく「温度と文脈長と編集形式の効果」になる。
- 台帳の `sampling` 列に条件が残る。`null` は「宣言しなかった＝ aider の既定」であって
  空欄ではない。起動行にも同じことを出す。
- **同一入力の再現性も見る。** 同じ課題を 3 回引いて失敗が同形かどうかが、
  「決定的な癖」の正体を分ける。

### Thinking（判定・レビュー経路）

`agents/*.json` は think をヘッドレスの全役割で off に焼き込んである。根拠は
2026-08-10 の実測だが、**その台帳は `ledger-2026-08-10-qwen35-9b.jsonl` で qwen のみ**であり、
gemma4:e4b がこのリポジトリに入るのは翌日である。sampling とまったく同じ構図——
ある条件で測った結論が、別の条件へ黙って持ち越されている。反証も同じリポジトリ内にある
（`agents/ollama-list-thinking.json` は gemma4 の split で Thinking を使う）。

さらに**機構が 2 つある**。`--think on|off` は API の `think` フィールド、
`--think prompt` は system prompt 先頭の `<|think|>`（Gemma 4 系の作法）で、経路が違う。
後者は `--format` の強制 off に**巻き込まれない**ので、
「JSON 契約の役割では Thinking を使えない」という現行の制約が当てはまらない可能性がある。

```bash
# レビュー（RV1/RV2）— サイズで跳ねた唯一のジャンル。think で e4b が届くか
python3 text_eval.py --model gemma4:e4b --cases RV1,RV2 --repeat 3            # 基準線
python3 text_eval.py --model gemma4:e4b --cases RV1,RV2 --repeat 3 --think prompt

# 基準の取り違え（F2/J2）— 4 レバー全滅の場所。think は 5 本目のレバーになるか
python3 judge_eval.py --model gemma4:e4b --cases F1,F2,J1,J2 --repeat 3 --think prompt

# 安く先に見る: 記録済みプロンプトのオフライン再生（ライブ実行を焼かない）
agent-ollama --replay --replay-limit 20 \
  --arm model=gemma4:e4b,think=off,format=json \
  --arm model=gemma4:e4b,think=prompt,format=json
```

見るのは 3 点。**(1)** `prompt` 方式で thinking が実際に発生するか（ログの
`thinking_chars`）。**(2)** `--format json` と併用して本文が返るか（空応答率——
qwen では 39/39 が空だった）。**(3)** 正解率が動くか。
**そして壁時計を必ず同時に見る**——qwen で think on が全滅した理由は品質ではなく
p90 942 秒（`agent_timeout` 600 秒超）だった。gemma4 は decode が 2 倍以上速いので
結論が変わりうるが、**変わることを確かめずに戻さない**。

動かなければ現状維持でよい。**測っていないものを「効くはず」で入れない規律は変えない。**

### 実測 2026-08-15 — 段0 の結果

モデルは gemma4:e4b。台帳は `results/archive/ledger-2026-08-15-p10-{worker-sampling,text-think,judge-think}.jsonl` と `replay-2026-08-15-p10-think.jsonl`。

**sampling。実効値は temperature=0 だった。** aider は既定（`use_temperature: true`）で
リクエストへ `"temperature": 0` を明示送信する。ソースと `--verbose` のライブ実行の両方で
確認した。つまり**過去の worker 実測はすべて貪欲デコード**である。

| 腕 | T1 | T2（対照） | T1 の失敗の形 |
|---|---|---|---|
| 基準線（宣言なし = temp 0） | 0/3 | 3/3（中央値 209s） | 3 本とも同形（追加テスト自体が落ちる） |
| 推奨 sampling（1.0 / 0.95 / 64） | 1/3 | 3/3（中央値 237s） | 3 様に割れる（PASS / ImportError / テスト未追加） |

「初回失敗 13/13 同形」は貪欲デコードの帰結で、モデルの決定的な癖とはまだ言えない——
推奨 sampling では失敗が揺れ、1 本は受入まで届いた。**対照群 T2 は壊れない。**
代償は壁時計で、T1 の中央値が 470s → 600s（timeout 2 本）。
(b) 族「引き直しても揺れない」は温度 0 下の観察だったので、`check_on_exhausted` の
既定（escalate）の前に「推奨 sampling で引き直す」段を挟む選択肢に実測の根拠が付いた。

**Thinking（`--think prompt`）。3 点の答え:**

1. **thinking は本文に出ない。** replay 20 件で think 系マークアップ 0。`--format json` の
   文法制約下では思考セクションは物理的に出せない。ただし出力は変わる（腕間一致 15%・
   出力トークン +21%）——効き方は明示的な思考ではなくプロンプト条件付け。
2. **本文は返る。** 空応答 0/20（qwen は 39/39 空だった）。壁時計も中央値 10.25s で
   off（11.18s）と同等。**gemma4 では JSON 契約役割と think=prompt は併用できる。**
3. **正解率はレビュー網羅性だけ動く。**

| セル | think=off | think=prompt |
|---|---|---|
| RV1（レビュー網羅） | 0/3（同日基準線。8/14 実測も 2/6 帯） | **2/3**（合格 run は 4s → 24〜33s に伸び検出 2 件） |
| RV2(違反特定) | 0/3 | 0/3（violations に dict が混ざり出力が崩れ気味） |
| F1 / J2 | 3/3（8/11） | 3/3 |
| F2 / J1 | 0/3（8/11） | 0/3（**誤答まで同一**: F2=c3・J1=c4） |

基準の取り違え（F2 / J1）は think でも誤答ごと再現した。**5 本目のレバーも空振り**で、
決定化（P4）行きの判断は維持。RV1 が動いたのは事実だが 12b（6/6）の代替には届かない。

## 実測 2026-08-15 — 段1 実機再測（P1 配線の検証）

T1gate 相当（implement → add_tests・決定的 check + 再投入・課題文とチェッカーは
worker_eval と同一定数）を、本番経路 `agent-loop statemachine` + aider / gemma4:e4b で
3 run 引いた。台帳は `results/archive/ledger-2026-08-15-p1-live-t1gate.jsonl`。

**結果: 3/3・中央値 313s・escalate 0。** ハーネス模擬（中央値 952s）より短い
（再試行回数の揺れがあるので同条件比較ではないが、実機経路のオーバーヘッドが
受入を壊さないことは確定）。

ただし**素通しでは 0/3 帯だった**。模擬との差分が 3 つあり、いずれも
「定型の実行で小型モデルに訊いてはいけないことを訊く」配線だった（修正済み・
`agent-loop/test/test_statemachine.py` の CheckGateTest に固定）:

1. **check ゲート状態の書込を書式契約で落としていた。** 編集 CLI は黙って直すのが普通
   （契約文を返さない）だが、機械契約の合成が `startswith:last_output:` 型の遷移しか
   見ておらず、`equals:check_ok:true` 型では check まで到達せず落ちた。→ 検査だけを
   材料に遷移するステートでは完了済み書込へ機械契約を合成し、判定を check に委ねる。
2. **check 失敗後の再投入が制御周（次の一手を訊く）へ戻っていた。** e4b は診断を渡されても
   調査ループ（テスト再実行）で周を使い切る。→ 再投入は前試行が書いたファイルへの編集で
   直接入る（worker_eval の再試行と同型）。再投入がアクション不成立なら escalate へ数える。
3. **初回のファイル割付を制御席に訊いていた。** e4b は「テストを書く」前に pytest 実行や
   `pip install` の環境いじりへ逸れて周を焼く。→ state の `write:` 宣言（schema 拡張）で
   割付を固定し、宣言があれば制御周を挟まず編集 CLI へ直行する。

一般化すると、**定型（事前分解済み）の実行では、割付・完了判定・再投入先はすべて宣言で
決まっており、モデルに訊く場面は編集そのものだけ**である。制御周は宣言の無い
非定型アクションのフォールバックに残る。

## 実行前のチェック

1. `agent-ollama` を再ビルドしたか（`bash tools/agent-tools/install.sh`）。
   ソースを直しただけでは zipapp に入らない。
2. ollama サーバを他のプロセスと共有していないか。agent-project / agent-flow は止める。
3. Mac がスリープしないか。`caffeinate -i -m -w <pid>` を当てる。スリープすると
   壁時計だけが進み、monotonic は止まるので、上限の判定と記録がずれる。
4. git worktree から回すなら、その worktree の直下に `.venv` があるか。チェッカーと
   `--test-cmd` はリポジトリルートの `.venv/bin/python` を使う（`worker_eval.py` の
   `VENV_PY`）。worktree には `.venv` が無いので、メインの checkout から symlink を張るか、
   メインの checkout で回す。

---

## 実測 2026-08-15 — E6/E7（決定化パイプと economy クラウド 0 受入）

実装計画（2026-08-15 候補ベース実行）の E6/E7。モデルは gemma4:e4b、
台帳は `results/archive/ledger-2026-08-15-e6-*.jsonl` と
`results/archive/2026-08-15-e7-cloud-zero-acceptance.json`。

### E6-1: 多基準 filter / judge の決定化パイプ（P4）— 3/3 帯に到達

F2 / J1（4+1 レバー全滅・0/3）を、**モデル=事実抽出のみ・判定=機械**へ組み替えた
セル F2P / J1P で引き直した。抽出の出力は `{"facts":[{id,tests,extra_deps,lines}]}`、
判定は `agentcore.nodecontract.decide_candidates`（1 実装。欠測は undecided として
確定を拒む）。

| セル | 素 F2/J1（過去） | 決定化パイプ | 中央値 |
|---|---:|---:|---:|
| F2P（filter 相当） | 0/3 | **3/3** | 7s |
| J1P（judge 相当） | 0/3 | **3/3** | 5s |

**組み替えの注意（1 回目の失敗から）。** 抽出ゴールを filter / judge の kind のまま
流すと、flow-worker プロンプトの役割行（「末尾に {"kept": ...} を添える」等）が
ゴールを上書きし、モデルが判定へ滑り戻る（F2P 1/3・旧契約の即答が混入）。
**決定化パイプの抽出は独立ノード（extract 系）として走らせる**こと。台帳の
前半 6 行（kind=filter/judge）がその失敗の証跡、後半 6 行（kind=extract）が本測定。

### E6-2: 制約つき生成のゲート（P5）— 機械検査 + 再投入

チェッカーの決定的診断（note）を付けた 1 回再投入（`text_eval.py --repair`。
statemachine の check 再投入と同じ運び方）。

| セル | 素（過去・12b/e4b 4/6 帯） | 検査+再投入 | 内訳 |
|---|---:|---:|---|
| SM2（220 字・必須言及・数値捏造なし） | 4/6 | **3/3** | 1 本は再投入で回復 |
| PR1（予算 10 の一意最適） | 4/6 | 2/3 | 2 本再投入で回復・1 本は組合せ選択の誤りが残存 |

PR1 の残差は制約検査でなく**組合せ最適の選択誤り＝P4 系**。この形は総当たりが
決定的に書ける（selfcheck が実際に総当たりで検算している）ので、実運用では
モデルに選ばせず決定化する。

### E7: strategy=economy の定型 flow — クラウド消費 0 の受入

隔離 control（v2・economy・候補=ollama/gemma4:e4b のみ・dual-write）で
plan-file 定型（filter→judge→reduce）を `--executor agent` 完走。

- 全 3 ノード done。result の `execution_decision` は全て
  `selection_source=qualified-candidate / rank=1 / control_revision=100`。
- 予算台帳の CLI は `ollama-json` のみ——**metered CLI（claude/codex/copilot/kiro）の
  行は 0**。昇格経路は不発火（クラウド行 0 が負性確認）。
- E5 の輪: `collect_flow_buses` → `agent-audit qualify`（dry-run）が同 run から
  3 セル（ollama-json/gemma4:e4b × filter/judge/reduce）を観測し、samples=1 は
  min_samples 未満なので **trial 止まり**（保守側の既定どおり昇格しない）。

副観測: flow 側 j1（単基準のつもりの judge）が依存 f1 の絞り込みを無視して
c4 を選んだ——多基準の取り違えは本番経路でも再現する。判定を決定化パイプへ
寄せる根拠がまた 1 つ増えた。

---

## 引き直しの腕 — best-of-N と決定的ゲート採択（計画 2026-08-22 案 1・実装済み）

段 0（P10）が「実効 temperature は 0 で、推奨 sampling では失敗が揺れる（T1 0/3 → 1/3・
対照 T2 は 3/3 維持）」を出した。**揺れる失敗は引き直しで拾える**——これを腕にしたのが
`--resample N` である。狙いは受入率そのものより **escalate 率**（＝クラウド昇格の頻度）で、
壁時計はローカルの時間、節約したいのはクレジット、という交換はここでも成立する。

```bash
# 基準線（引き直さない。従来の腕と同一条件）
python3 worker_eval.py --model gemma4:e4b --cli aider --tasks T1gate,T2gate,T3gate --repeat 3 \
  --agent-policy off --temperature 1.0 --top-p 0.95 --top-k 64

# 引き直し腕（他は基準線と同一。変えるのは --resample だけ）
python3 worker_eval.py --model gemma4:e4b --cli aider --tasks T1gate,T2gate,T3gate --repeat 3 \
  --agent-policy off --temperature 1.0 --top-p 0.95 --top-k 64 --resample 3
```

**族を分けて読む。** 失敗には 2 族ある（[gate-generality](results/archive/2026-08-14-gate-generality-report.md)）。
(a) 仕様の読み違い族は真偽ゲート + 再投入で直り、P10 で sampling が失敗を揺らしたのもここ
（T1）。(b) 作業の丸ごと欠落族（T3gate——9 attempt が同文 `C3 fail: 契約テストが追加されて
いない`）を推奨 sampling で引き直した記録は**無い**。台帳の `family`（`a` / `b`）と集計末尾の
「族別 escalate」で分けて読み、**引き直しの採否は (a) で決める**。(b) が動かないのは引き直しの
失敗ではなく適用範囲の外で、答えは成果物を 1 つに割ること（`nodecontract.local_patch_blockers`
の適格条件を満たす形へ分解する）。T1gate だけで測って「escalate が下がった」と読むと、
運用で escalate を出している (b) 族に効かない腕を入れることになる。

**多数決ではない。** プロンプト・多数決の 4 レバーが全滅したのは filter / judge の
**判定**領域で、あちらは判定をモデルに訊いた。ここはモデルに任せるのは生成だけで、
採択は決定的ゲートだけが行う——候補生成 + 決定的検算（P4 の標準形）の worker 版である。

順序は **診断つき再投入 → 引き直し → escalate**。手順ごとに `max_retries` を使い切って
なお通らなければ、作業ツリーを**手順開始時点へ戻して**独立に引き直す。戻すのが要点で、
前の抽選の成果を残したまま引くと、それは既にある blind 腕（診断を渡さない再投入）と
同じものになる。戻しは追跡済みファイルを `git checkout` で、未追跡（＝課題の仕込み）を
控えから復元する（`snapshot_worktree` / `restore_worktree`）。

- **引き直すのはゲートのある手順だけ。** 採択する機械がいない手順を引き直すと、
  同じ課題を複数回呼んだだけになる（自己申告での採択は P1 が外した道）。
- **`--resample > 1` は sampling の宣言を要求する**（aider 経路）。貪欲デコードの
  引き直しは同じ壁時計を払って同じ出力を受け取るだけなので、起動前に落とす。
  「引き直しても揺れない」を対照として測りたいなら `--temperature 0` と明示する
  ——このハーネスは「未宣言」と「既定値を明示」を別物として扱う。
- **既定（`--resample 1`）は従来と 1 バイトも変わらない。** 控えも戻しも走らないことを
  契約テストで固定してある（`ResampleTest.test_default_arm_never_touches_the_worktree`）。

読むのは受入率だけではない。台帳へ次が増えた: `resample`（宣言した上限）・
`draws`（実際に使った本数）・`escalate`（ゲート付き手順が全部使い切って通らなかったか）・
`family`（失敗の族 a / b。課題ごとに宣言し、宣言漏れは契約テストで落とす）。
`retry_count` は**診断つき再投入の回数**を数えるよう定義を直した（引き直しは初回投入なので
数に入れない）。引き直しの無い既存の腕では従来と同じ値が出る。

採用条件: **(a) 族の escalate 率が下がり、対照群（T2gate）に退行がないこと。** 上限を上げても
`draws` が伸びていないなら、受入の差は引き直し以外の何かで説明しないといけない。(b) 族
（T3gate）は同じ腕で必ず一緒に引き、動かなければ「引き直しの適用範囲外」として記録する。

### 実測 2026-08-23/24 — 段 1（基準線 vs `--resample 3`・gemma4:e4b × aider・各 3 回）

上のコマンドそのままで直列に回した（この Mac 16 GB・`--agent-policy off`・推奨 sampling）。
台帳: `results/archive/ledger-2026-08-23-stage1-baseline-gemma4-e4b.jsonl` /
`…-stage1-resample3-gemma4-e4b.jsonl`。

| 腕 | T1gate（a） | T2gate（a・対照） | T3gate（b） | 族別 escalate | 壁時計 中央値（T1 / T2 / T3） |
|---|---:|---:|---:|---|---|
| 基準線（resample 1） | 3/3・esc 0 | 3/3・esc 0 | 0/3・esc 3 | (a) 0/6・(b) 3/3 | 564s / 135s / 1257s |
| `--resample 3` | 3/3・esc 0（1 本が draw 2） | 3/3・esc 0 | 0/3・esc 3（全部 3 draw・9 call） | (a) 0/6・(b) 3/3 | 1040s / 144s / 3452s |

**採否: 採用しない（`check_on_exhausted` の既定は escalate のまま）。** 採用条件「(a) 族の escalate
率が下がる」は、基準線の (a) が既に 0/6 で下げようが無かった。引き直しが働いた唯一の場面
（T1gate#2: テスト手順が再投入 2 回で通らず、作業ツリーを戻して引き直した 1 本目で通った）は
機構が設計どおり動いた証拠だが、受入・escalate の数字は動かさず、壁時計だけが中央値で 2 倍になった。
(b) 族は予告どおり動かない——T3gate は両腕とも 0/3、27 attempt がすべて同文
`C3 fail: 契約テストが追加されていない`。引き直しは (b) に対して同じ壁時計を 3 倍払って同じ欠落を
受け取るだけで、T3gate 1 run が 1 時間を超えた（4124s）。(b) の答えは引き直しではなく成果物を
割ること（§4.2 A1 / A2）。n = 3 なので率ではなく「動かなかった」の証拠として読む。

付記: 基準線の T1gate 3/3 は P10 の T1（一発）1/3 より高いが、腕が違う（ゲート + 再投入あり）。
同条件の過去値は T1gate 3/3（2026-08-13・温度 0）で、sampling を宣言しても退行していない。

### `T3splitgate`（一成果物/node）— **(b) 族で初めて 0 が動いた**（2026-08-29）

T3gate の schema + 契約テストを、schema 1ファイルと契約テスト 1ファイルの2手順へ分けた。
各手順の直後に C1 / C3 の決定的 checker を置き、失敗時はその手順だけを有界再投入する。
最終 checker と seed は T3gate と共通なので、変えるのは成果物の粒度と gate の位置だけである。

```bash
python3 worker_eval.py --model gemma4:e4b --cli aider --tasks T3gate,T3splitgate --repeat 3 \
  --agent-policy off --temperature 1.0 --top-p 0.95 --top-k 64
```

実測（gemma4:e4b・aider・policy off・推奨 sampling・n=3）。台帳:
`results/archive/worker/ledger-2026-08-29-t3splitgate-vs-t3gate-gemma4-e4b.jsonl`。

| 腕 | 受入 | 中央値 | 呼び出し | escalate | 落ち方 |
|---|---|---|---|---|---|
| T3gate | **0/3** | 1151s | 9 | 3/3 | 9 試行すべて C3 fail（契約テストが追加されていない） |
| T3splitgate | **3/3** | 561s | 6 | 0/3 | — |

**全滅 ⇔ 全通**なので、n=3 でも差として読んでよい唯一の形である（「n の読み方」）。
`3/3` は成功率 100% の推定ではない——率として読むなら n ≥ 10。

**効いたのは再投入ではなく分割である。** 分割した腕は 6 呼び出しすべてが
**再投入 0**（`retry_count=0`）で、各手順が 1 回目に通った。つまり E4B は schema も
契約テストも個別には書ける。一括で渡すと 2 つ目が丸ごと落ちる——そこへ診断つき再投入を
9 回積んでも、9 回とも同じ C3 fail で返ってくる。**(b) 族は再試行の強化では掘れず、
成果物を割ると消える**（F3「構造で殺すしかない」の実証）。

壁時計も分割側が安い（中央値 561s 対 1151s・呼び出し 6 対 9）。ただし最悪ケースは重なる
——`T3splitgate#3` は 2 手順目が壁時計上限（600s）に当たっており、**受入は通っているのに
`mode` は timeout** である。仕事が終わったあとも aider が回り続けた形なので、
A1 の呼び出し上限（`AGENT_MAX_TOOL_ROUNDS_WRITE`）が効く場所はここである。

次に測るなら n を伸ばす側で、腕の追加ではない。

### `T3autosplit`（成果物スロットを機械が割る）— 人が割ったのと同じだけ通る（2026-08-29）

T3splitgate の 2 手順は人が文面を書いた。こちらは書かない。宣言するのは処理契約の
`deliverables`（成果物スロット）だけで、手順への割り方は本番の
`agentcore.nodecontract.split_by_deliverables` が作る（goal は元の goal ＋
「この手順で作る成果物は 1 つだけ」の定型文）。seed・最終 checker・gate の位置・
再投入の上限は T3splitgate と同一なので、**変えたのは割り方を人が書いたか機械が書いたか
だけ**である。

```bash
python3 worker_eval.py --model gemma4:e4b --cli aider --tasks T3autosplit --repeat 3 \
  --agent-policy off --temperature 1.0 --top-p 0.95 --top-k 64
```

実測（gemma4:e4b・aider・policy off・推奨 sampling・n=3）。台帳:
`results/archive/worker/ledger-2026-08-29-t3autosplit-gemma4-e4b.jsonl`。

| 腕 | 割り方 | 受入 | 中央値 | 呼び出し | 再投入 |
|---|---|---|---|---|---|
| T3gate | 割らない | 0/3 | 1151s | 9 | 9 試行すべて C3 fail |
| T3splitgate | 人が手順を書く | 3/3 | 561s | 6 | 0 |
| T3autosplit | 機械が成果物から割る | **3/3** | 495s | 6 | 0 |

**機械が割った分解は人の分解と同じだけ通った。** 6 呼び出しすべてが `retry_count=0` で
1 回目に通っており、人が書いた per-step goal（「成果物は ... の 1 ファイルだけ」）と
定型文の差は結果に出ていない。分解に要る情報は成果物のパスだけだった、と読める。

中央値の差（495s 対 561s）は n=3 の並びの差で、速くなったとは読まない。`#2` は
2 手順とも壁時計上限に当たって `mode=timeout` だが受入は通っている——T3splitgate#3 と
同じ形で、仕事が終わったあとも aider が回り続ける現象である。

---

## 決定的コンテキスト・スライシングの腕 — T5（計画 2026-08-22 案 2・段 3）

案 2 は `agentcore.context_slice`（`ast` で対象シンボルと依存だけを抜く・LLM なし）を
`--read` の材料に使うと**見落としが減るか**を問う。既存の T1 / T3 は参照材料が小さく
（humansize.py は十数行）、切っても差が出ないので、材料が大きい課題を 1 つ足した。

**T5**: 編集対象は小さい `eval/report.py`。バグは `apply_tax(net, tax_rate)` に小数を渡していること
——`apply_tax` は **ベーシスポイント**（10% = 1000 bp）を取る。その事実は 570 行の `eval/bigmod.py`
の真ん中に埋めた docstring にしか書いていない。テスト（3 件）が仕様の正で、bigmod とテストを
書き換えたらズル。腕は `--read` の渡し方だけが違う:

| 腕 | `--read` | 狙い |
|---|---|---|
| T5noread | 渡さない | 材料なしの対照（推測だけで当たるか） |
| T5 | `eval/bigmod.py` 全文（570 行） | 「入れれば読める」が成り立つか（MRCR 25.4 の帯） |
| T5slice | `context_slice` の抜粋（apply_tax / prorate と依存。数十行） | 見るべき範囲を機械が先に確定する |

抜粋は `eval/bigmod.slice.py` へ書いて渡し、台帳の `slice` に kept / total 行数と省略数、
`read_mode` に whole / slice / none を残す。切れなければ原本へ倒し、倒したことも台帳に残す
（静かに倒れると、抜粋が効いていない条件で測ってしまう）。契約テスト `SliceArmTest`。

チェッカーは **apply_tax 経由の修正**だけを受ける。最初の版はテスト通過だけを見ていて、read なしの
腕が `int(net * (1 + tax_rate))` と自前で税を掛けて 3 件通した（2026-08-24・2 本とも同じ修正＝温度 0）。
それではテストは通っても「参照材料を読んだか」が測れない。課題文に「税は bigmod.apply_tax を
呼んで計算する」を足し、`ast` で apply_tax の呼び出しが無い修正を落とす。

```bash
python3 tools/agent-tools/eval/worker_eval.py --model gemma4:e4b --cli aider \
  --tasks T5noread,T5,T5slice --repeat 3 --agent-policy off \
  --temperature 1.0 --top-p 0.95 --top-k 64     # 温度 0 だと 3 回が同一出力になる
```

読むのは受入率と `tokens_in`、それに失敗の形（bp を読み当てたか）。採用条件は「T5slice が T5 を
受入で下回らず、tokens_in が減ること」。T5 が T5noread と同じなら「入れても読めていない」の証拠で、
それ自体が案 2 の前提（事実 7）を支える。

### 実測 2026-08-24 — gemma4:e4b × aider・推奨 sampling・各 3 回

台帳: `results/archive/ledger-2026-08-23-stage3-t5-gemma4-e4b.jsonl`。

| 腕 | 受入 | 壁時計 中央値 | tokens_in | 失敗の形 |
|---|---:|---:|---|---|
| T5noread | 0/3 | 216s | 5.0k〜8.0k | `apply_tax(int(net), tax_rate)` のまま——bp に辿り着かない |
| T5（570 行を全文） | 3/3 | 214s | 13.0k〜13.1k | — |
| T5slice（15 行の抜粋・56 シンボル省略） | 3/3 | 144s | 3.6k〜3.7k | — |

**採用条件は満たした**（受入で下回らず、tokens_in −72%・壁時計 −33%）。

### 実測 2026-08-24 — T6（同じ課題で材料を 2,020 行へ・`--num-ctx 32768`・各 3 回）

「入れれば読める」が規模で崩れるかを、材料だけ 3.5 倍にして測った（`BIGMOD_XL`。
台帳 `results/archive/ledger-2026-08-24-stage3-t6-gemma4-e4b.jsonl`）。

| 腕 | 受入 | 壁時計 中央値 | tokens_in |
|---|---:|---:|---|
| T6（2,020 行を全文） | 3/3 | **579s** | 41.5k〜62.9k |
| T6slice（15 行の抜粋・201 シンボル省略） | 3/3 | **99s** | 5.5k〜5.6k |

読み方。(1) **受入は 2,020 行（1 ファイル約 21k token）でもまだ崩れない**——「入れれば読める」の
限界はこの課題では出なかった。auto-test の失敗出力が助けた可能性も同日の追試で消えた:
**auto-test を切った一発（T6noat / T6slicenoat・各 3 回）でも両腕 3/3**（noat 中央値 63s・
tokens_in 20.6k ⇔ slice 70s・2.6k。台帳 `results/archive/ledger-2026-08-24-stage3-t6noat-gemma4-e4b.jsonl`）。
つまり「編集対象の import 先の仕様を 21k token の材料から読み当てる」形では、e4b の長文弱点
（MRCR 25.4）は発現しない——弱点が効くのは複数箇所の同時追跡（T1/T3 型の横断）であって、
単一シンボルの検索ではない、という切り分けまでがこの腕の結論。**見落とし面積の縮小を案 2 の
根拠にする道はここで閉じる**（経済だけが根拠として残る）。(2) **経済は規模で開く**——570 行で
tokens_in −72% / 壁時計 −33% だった差が、2,020 行では **−87% / −83%**（1 呼び出し 10 分 → 1.6 分）。
全文の腕は編集ループの毎ターンに材料を再送するので、材料サイズが壁時計へ倍々で効く。
(3) 材料が無いと 0/3（T5noread）なので、read 調査がファイルを正しく当てることが前提で、抜粋は
その**内側**の節約である（§4.1 C3 の固定条件）。結論: 本番配線（`--read` 材料の自動抜粋）は
**prefill / 壁時計の節約として入れる価値が規模とともに増す**。見落とし族への効果は根拠にしない。

## planner の最小 eval — P9 の最初の 1 セル（2026-08-23・計画 2026-08-22 §4.2 B1）

`coverage.json` で missing だった planner を direct にした。測るのは**本番の planner そのもの**
——flow-planner スキルの `plan.py`（3 段パイプライン）を agent-flow と同じ引数
（`--agent-cli ollama-json --model … --granularity auto --review false --probe-root …`）で子プロセス
として呼び、返ったグラフを決定的チェッカーで判定する。正解は要求文を組んだ時点で決まる
**構造**（構成的ラベル）で、判定役（LLM）は使わない。

```bash
python3 tools/agent-tools/eval/planner_eval.py --selfcheck          # チェッカーの自己検証（LLM 無し）
python3 tools/agent-tools/eval/planner_eval.py --model gemma4:e4b --repeat 3
```

| ケース | 要求の形 | 正解の構造 |
|---|---|---|
| PL1 | ラベル付き 3 段（前段の成果が無いと着手できない） | 3 ラベルが別ノードにあり、鎖 A→B→C（推移的依存）。split 無し |
| PL2 | 独立 3 件 + 最後に比較表 | 3 件は互いに依存しない・3 件すべてに依存する統合ノードがある・pattern に fan-out-and-synthesize |
| PL3 | notes/ の ITEM-01〜12.md をファイルごとに処理し索引へ | split がちょうど 1 つ・split の後ろに静的チェーン無し（map/reduce は実行時展開）・pattern に map-reduce |
| PL4 | README 1 行のタイポ修正 | 成果ノード（work/generate）≤ 2・verify 以外の余計な kind 無し・全体 ≤ 3（過分解の検出） |

どのケースでも先に**契約**（id 一意・deps が実在・循環なし・kind が正典・pattern がカタログ内）を見る。
契約違反は `contract`、構造外れは `wrong`、`plan.py` 自体の失敗は `cli_error` として台帳に残る。

**最初の 1 本で本番の欠陥が出た。** flow-planner の Phase 3 は「JSON 配列のみ」を要求していたが、
ollama の JSON モード（`--format json`）は配列を返せない（2026-08-11 split の実測と同じ穴）。
つまり **agent-ollama 経路の flow-planner は Phase 3 で構造的に必ず落ち、agent-flow は黙って
組み込み planner → stub へ縮退していた**（`Phase 3: tasks is not a list`）。出力契約を
`{"tasks": [...]}` のオブジェクトへ改め（裸の配列も従来どおり受ける）、同じ腕で通るようになった。
P9 が「測っていない面」と呼んだものの中身は、能力の未測定ではなく**経路の不通**だった。

### 実測 2026-08-23 — gemma4:e4b（ollama-json・granularity auto・各 3 回）

台帳: `results/archive/ledger-2026-08-23-p9-planner-gemma4-e4b.jsonl`（goal 全文つき。チェッカーを
直したら台帳から再判定できる）。

| ケース | 正解 | 中央値 | ノード数 | 外れ方 |
|---|---:|---:|---|---|
| PL1 鎖 | 2/3 | 67s | 3 / 7 / 3 | 1 本は 7 ノードに膨らみ、同じラベルを 2 ノードが名乗って同定不能 |
| PL2 fan-out + 統合 | 3/3 | 85s | 6 / 5 / 8 | —（統合ノードは extract / synthesize / work と揺れるが構造は正しい） |
| PL3 列挙 | 1/3 | 64s | 4 / 3 / 3 | 2 本は split → map → reduce を**静的に**書いた（engine は split 完了後に `-m*` / `-reduce` を動的生成するので、静的 map は全件を 1 ノードで受ける） |
| PL4 単一 | 0/3 | 69s | 5 / 4 / 6 | 3 本とも「読む → 特定 → 直す → 検証（→ 適用）」に割る（coarse 解決でも成果ノード 3〜4） |

読み方（n = 3）。**構造が要求文に書いてある（順序・独立 + 統合）ものは組める**。崩れるのは
(1) **engine の約束を知らないと書けない形**（map-reduce の動的展開——Phase 3 の指示文にはあるが
効いていない）と (2) **小さい仕事を小さいまま置けない**（過分解——1 行の修正に 4〜6 ノード。
worker が e4b なら各ノードが 1 呼び出しなので、壁時計がそのまま 4〜6 倍になる）。(1) は
プロンプトの問題ではなく決定的ゲートの問題で、flow-planner の `gate_tasks` に「split の後ろに
静的 map/reduce があれば落として作り直す」を足した（同日。列挙駆動 force のときの split 存在検査と
同じ置き場。engine の約束に反する形を機械で止めるので、モデルの数字を根拠にした判断ではない）。
効いたかの再測（PL3）は次の腕で取る。(2) は granularity の下端（coarse = 1〜3 成果ノード）の中でも
起きているので、レンジの問題ではなく「読む・特定する」を成果ノードにしてしまう癖——
`[scope]` を持たない手順ノードを成果ノードに数えない、が次の一手。こちらは**本書の数字を
採用根拠にして直す**段階で、まだ直していない（測定と修正を同じ PR に混ぜない）。

### 宣言は届くか — PL5 / PL6（2026-08-29）

判定契約（`decision`）も成果物スロット（`operation.deliverables`）も、**宣言が唯一の入口**である。
planner が書かなければ本番で一度も発火しない。そこで「planner が宣言を書けるか」を 2 ケース足した。

| ケース | 要求 | 正解（構成的） |
|---|---|---|
| PL5 | `eval/humansize.py` の実装と `eval/test_humansize.py` のテスト（成果物 2 つ） | 要求が名指しした 2 ファイルが、本番の分割器（`split_by_deliverables`）を通したあとに**それぞれ別スロットの唯一の成果物**になる。要求に無い成果物を足したら不合格 |
| PL6 | 候補から「追加依存が要らないもの」だけ残す | filter / judge に `decision` が付き、本番の `decision_contract_errors` が受理する（`criteria` 空は不合格） |

判定には**本番の検査関数をそのまま呼ぶ**（`engine.operation_contract_errors` /
`decision_contract_errors` / `split_by_deliverables`）。ここで写して緩めると、
「eval は通るのに本番では剥がされる宣言」を合格にしてしまう。

実測（gemma4:e4b・ollama-json・granularity auto・n=3）。台帳
`results/archive/ledger-2026-08-29-planner-declarations-gemma4-e4b.jsonl`。

| ケース | 正解 | 中央値 | 外し方 |
|---|---:|---:|---|
| PL5 宣言（成果物スロット） | 2/3 | 202s | 1 本は 3 成果ノードのうち 2 つしか宣言せず、残り 1 つ（PR を開く手順）に宣言が無い |
| PL6 宣言（判定契約） | 1/3 | 116s | 1 本は `tie_break` を文字列で書き（engine が宣言ごと剥がす）、1 本は filter / judge を作らなかった |
| PL1 / PL2 / PL3 / PL4（同時に引いた回帰） | 2/3 / 3/3 / 0/3 / 1/3 | 105–134s | 08-23 の 2/3・3/3・1/3・0/3 と同じ帯（n=3 では差として読まない） |

**宣言は届く。ただし黙って落ちる経路が 2 つあった。**

1. **engine が壊れた宣言を黙って剥がしていた。** `tie_break` が文字列だと `decision` ごと
   無視され、ノードはモデル判定のまま走る——「宣言したのに効かない」が誰にも見えない。
   剥がすときに log を出すようにした（`判定契約を無視: <id>（<理由>）`）。
2. **flow-planner が宣言を運んでいなかった。** `normalize_tasks` は既知キーだけを通すので、
   モデルが `operation` / `decision` を書いても捨てられていた。運ぶようにし、Phase 3 の
   ゲートへ「filter / judge には `decision` が要る」「宣言したなら器を合わせる」を足した
   （**宣言の有無ではなく、engine が剥がす形かどうか**を見る。成果物を作らないノードに
   `operation` を求めない）。

### 2 本目以降（ゲート込み・2026-08-30）— **宣言は出る。噛み合わない 1 語で全部を失っていた**

1 本目の 2 つを直したうえで PL5 / PL6 を引き直した。チェッカーも同時に厳しくしてある
（1 本目は「宣言したノードの割合」を見ていた。以降は**要求が名指しした成果物がそれぞれ
1 スロットの唯一の成果物になるか**を見る）ので、率は 1 本目と直接比べられない。

台帳: `results/archive/ledger-2026-08-30-planner-declarations-gated-gemma4-e4b.jsonl`
（PL5 / PL6）と `ledger-2026-08-30-planner-pl6-tiebreak-fix-gemma4-e4b.jsonl`（PL6 再測）。

| ケース | 正解 | 中央値 | 落ち方 |
|---|---:|---:|---|
| PL5 宣言（成果物スロット） | **2/3** | 116s | 1 本が要求に無い成果物（`docs/human_bytes_spec.md` ほか）を足した |
| PL6 宣言（判定契約）・修正前 | 1/3 | 165s | 2 本が `filter` に `tie_break` を書き、その fact を `facts` で宣言していない |
| PL6 宣言（判定契約）・修正後 | **3/3** | 281s | — |

**落ちていたのは使われない 1 語だった。** `filter` の `tie_break` は `decide_candidates` が
読まない（順位基準は judge のためのもの）。ところが器が崩れていると engine は `decision` を
**丸ごと**剥がすので、使わない 1 語のために機械判定そのものを失っていた。ゲートは
この形を検出できていて作り直しも走ったが、e4b は書き直しても同じ形を出した。そこで
planner スキルの正規化で **`filter` の `tie_break` だけを落として decision 本体は運ぶ**
（`judge` の tie_break は使うので落とさない。落としたことは stderr に出す）。
ゲートは残す——モデルにはまず自分で直す機会を与え、それでも残る 1 語だけを機械が外す。

ゲートは 3 つ足してある: `criteria` / `tie_break` の fact を `facts` で宣言していない、
`filter` に `tie_break` を付けている、同じ成果物を 2 ノード以上が宣言している。

（PL5 の率は途中で 0/3 と読んだ時期がある。チェッカーが**集約・検証ノードに付いた宣言まで**
スロット判定に数えていたためで、分割が効くのは work / generate だけである。台帳に宣言を
残してあるので、同じ台帳を新しいチェッカーで再判定した——走り直していない。）

**深夜の壁時計は使わない。** 8/29 深夜の走行は 1 本 2500〜6400 秒だったが、マシンの
スリープと throttle を含む。判定は返ってきたグラフへの決定的検査なので正解 / 不正解は
影響を受けないが、時間は測定値として採らない。上の中央値はマシンが起きている時間帯の再測。

同じ実測で**別の欠陥も出た**。PL3 の 0/3 は「split の後ろに静的ノード」で、ゲートは
`kind` が map / reduce のときしか見ていなかった——e4b は同じ形を `work` で書いて素通りしていた。
engine（`plan_strategy_user`）は kind に関係なく拒むので、ゲートもそちらへ揃えた。

**ゲートを直しても PL3 は動かなかった（2026-08-30 再測・n=3）。** 3 本とも `split → map →
reduce` を静的に書き、3 本とも**ゲートは発火している**（同じグラフを `gate_tasks` に掛けると
`t2: split の後ろに静的 map ノードを置かない` が出る）。つまり残っているのは検出ではなく
**作り直し**で、e4b は不合格理由を渡されても同じ形を書き直す。所要は 111 / 133 / 137 秒。
kind 非依存にした修正が効くのは `work` / `generate` で書かれた別の外し方に対してで、
この 3 本はどれも map / reduce だった（修正前のゲートでも捕まる形）。台帳
`results/archive/ledger-2026-08-30-planner-pl3-gate-remeasure-gemma4-e4b.jsonl`。

ハーネス側の欠陥も 1 つ直した。`planner_eval` の壁時計上限は `subprocess.run(timeout=)` に
任せていたが、plan.py が起動する**孫プロセス（エージェント CLI）がパイプを握ったまま**なので
上限で親を殺しても `communicate()` が EOF を待ち続ける（実際に 70 分走り続けた）。
プロセスグループごと落とすようにし、timeout の判定も壁時計ではなく**打ち切りの事実**で
行うようにした（壁時計はマシンのスリープを含むので、monotonic で計る上限と一致しない）。

---

## 候補生成 + 決定的検算 — 生成側の最小 eval（2026-08-23・計画 2026-08-22 §4.2 C2）

next-eval-plan §2 の順 4。E6 が決定化したのは filter / judge の**判定**側で、こちらは**生成**側
——モデルは候補を出すだけ、採否は機械が存在チェックで決める。候補の誤り（無いパス・当たらない
regex・捏造したテスト名）は機械が落とすので、測るのは「**落とした後に正解が残るか**」。

```bash
python3 tools/agent-tools/eval/candidate_eval.py --selfcheck
python3 tools/agent-tools/eval/candidate_eval.py --model gemma4:e4b --repeat 3
```

材料は全部プロンプト内（9 ファイルの合成リポジトリ）。正解は手書きせず**内容から決定的に導く**
——grep は正規の regex をその場で掛けた行集合、パスは `TAX_RATE` を含む唯一のファイル、
テスト名は `ast` で集めた `test_*` のうち丸めを確かめるもの。出力契約は `{"candidates": [...]}`
（JSON オブジェクト。ollama の JSON モードは配列を返せない）。

| ケース | 候補 | 機械の検算 | 正解 |
|---|---|---|---|
| CG1 | `prorate` の定義・呼び出し行に当たる Python regex（≤ 3） | コンパイル・合成リポジトリ全文へ適用 | 行集合が正規 regex の結果と一致する候補が 1 つでもある（余計な行があれば「絞れていない」） |
| CG2 | 「税率 10% が 8%」を直すのに触るパス（≤ 3） | 存在チェック | 存在する候補に `billing.py` がある |
| CG3 | 丸めを確かめる既存テスト関数名（≤ 3） | `ast` で集めた実在名 | 実在する候補に `test_prorate_rounds_up` がある |

note には**機械が落とした候補数**を必ず出す——「無害化が働いた回数」がそのまま、この形を本番
（read_allocation のパス・verify コマンドのテスト名・read-only agent の grep）へ入れる根拠になる。

### 実測 2026-08-23 — gemma4:e4b（各 3 回）

台帳: `results/archive/ledger-2026-08-23-c2-candidate-gemma4-e4b.jsonl`。

| ケース | 正解 | 中央値 | 機械が落とした候補 | 外れ方 |
|---|---:|---:|---|---|
| CG1 grep パターン | 0/3 | 3s | 1 / 3（+ 1 本は JSON 崩れ） | regex を作らず、**当たる行そのもの**や `r"prorate(…)"` の生文字列を返した（読み違い族） |
| CG2 パス候補 | 3/3 | 1s | 0 | —（候補 1〜3 件、全部実在） |
| CG3 テスト名 | 3/3 | 1s | 0 | —（候補 1 件、実在） |

読み方（n = 3）。**材料がプロンプト内にある「選ぶ」候補生成（パス・テスト名）は e4b で足りる**
——捏造は 6 回で 0 回、無害化は一度も働かなかった。**「作る」候補生成（regex）は落ちる**が、
落ち方は能力ではなく読み違い（regex の概念を取り違える）で、これは text-eval の (a) 族と同じ
形——決定的検査（コンパイル + 行集合一致）を付けた再投入で拾える可能性が高い（未測）。
本番へ入れる順は CG2 / CG3 の形（read_allocation のパス候補・verify のテスト名候補）から。


## 廃止済み agent-project verify の評価記録（2026-08-23・計画 2026-08-22 §4.2 B1）

当時 `coverage.json` で missing だった agent-project の `verify` を direct にして測った記録。
測ったのは当時の**本番プロンプト**（`agent_project._charter_criteria_prompt`）と**本番の正規化**
（`normalize_verification`——証跡の無い pass を fail へ落とすフェイルクローズ）。合成ワークスペース
（git 初期化済み）に真 2 件・偽 2 件の達成条件を置く（偽の片方は「pytest が落ちる」）。

2026-08-24 に、この結果を routing へ反映して自然文 verifier を本番から撤去した。現在の project
acceptance は charter に明記された deterministic command だけを機械評価し、自然文は人の検収へ
送る。そのため `verify` は現行 surface でも coverage 対象でもなく、以下は再実行手順ではなく反証の
来歴として残す。

```bash
python3 tools/agent-tools/eval/project_verify_eval.py --selfcheck
python3 tools/agent-tools/eval/project_verify_eval.py --model gemma4:12b --arm verify   # 本番の変種
python3 tools/agent-tools/eval/project_verify_eval.py --model gemma4:e4b  --arm tools    # 道具あり
```

腕は 2 つ。`verify` は本番の verify 変種 `ollama-verify`（`--format json`・**道具なし**）で、道具が
無い verifier は何も確かめられないので pass が 1 つでもあれば**捏造**（実行していない証跡を書いた）。
`tools` は `ollama` の書込モード（`--tools bash`）で実際に確かめられる腕で、判定の正しさを見る。

### 実測 2026-08-23（各 3 回）

台帳: `results/archive/ledger-2026-08-23-p9-project-verify.jsonl`。

| 腕 | モデル | 正解 | 中央値 | 様式 |
|---|---|---:|---:|---|
| verify（本番変種・道具なし） | gemma4:12b | 0/3 | 17s | `contract` 3（criteria の JSON を返さず `{"analysis": "…確認しました"}` の散文 JSON） |
| verify（本番変種・道具なし） | gemma4:e4b | 0/3 | 24s | `wrong` 3（**捏造 pass 12/12 条件**。実行していない `grep` / `pytest` を証跡に書く） |
| tools（`--tools bash`） | gemma4:e4b | 0/3 | 83s | `wrong` 2（`grep "税率 10%"` を字面で打って偽陰性・cwd を見失う）・`contract` 1（末尾 JSON 無し） |

読み方（n = 3）。**本番の局所 verify 経路（ollama-verify）で charter 達成条件を判定させてはいけない。**
(1) 道具が無いので確かめようがなく、e4b は 12/12 で pass を捏造する（本番の正規化は証跡の有無
しか見ないので、書かれた証跡を信じて通す）。(2) 12b は `--format json` の下で「本文 + 末尾 JSON」の
契約を満たせず散文 JSON を返し、正規化が全 fail に落とす——捏造はしないが検証にもならない。
つまりプロンプト（本文 + JSON）と変種（JSON のみ・道具なし）が**最初から噛み合っていない**。
道具を持たせた e4b でも 0/3 で、確かめ方が字面（`grep "税率 10%"` は「税率は 10%」に当たらない）。
**局所で成立する verify は決定的コマンド（`verification.commands` / receipt）だけ**で、自然文の達成条件は
道具を持つ候補（クラウド CLI）へ回すか人の検収へ——local-first 計画の「役割ごとに割り当てる」の
割り当て表に、この 1 行を足すのが次の一手（測定と修正を混ぜないため、ここでは直していない）。

## agent-dashboard の Doctor — 4 モードの最小 eval（2026-08-23・計画 2026-08-22 §4.2 B1）

`coverage.json` で missing だった `doctor/*` 4 面を direct にした。プロンプトは**本番のビルダー**
（`agent.js` の `doctorPrompt`）を node で呼んで組む。応答は Markdown の自由記述なので、決定的に
測れる 2 点だけを見る——(1) 指示した N 見出しだけを使っているか（見出し契約）、(2) スナップショットを
組んだ時点で決まる正解トークン（失敗ログのモジュール名・取りこぼした acceptance の id・差分の値）が
**その見出しの節**に出ているか。読みやすさ・網羅性（C5）は測らない。

```bash
python3 tools/agent-tools/eval/doctor_eval.py --selfcheck
python3 tools/agent-tools/eval/doctor_eval.py --model gemma4:e4b --repeat 3
```

| ケース | モード | 正解トークン |
|---|---|---|
| DR1 | failure-diagnosis | 結論 / 根本原因に `yaml`（ログの `ModuleNotFoundError`）・対処対象が実行環境 |
| DR2 | consultation | 次にすること に承認待ち `N-12` |
| DR3 | plan-critique | 取りこぼし に `A3`（不正行のスキップ） |
| DR4 | delivery-rationale | 変更の意図に税率 `0.10`・acceptance 対応に `1100` |

### 実測 2026-08-23 — gemma4:e4b（各 3 回）

台帳: `results/archive/ledger-2026-08-23-p9-doctor-gemma4-e4b.jsonl`。**12/12**（DR1 中央値 18s・
DR2 14s・DR3 40s・DR4 25s）。見出し契約の違反 0、正解トークンの欠落 0。材料が全部スナップショットに
あり、答えが「読んで指す」形の役割は e4b で足りる——text-eval の抽出・分析 6/6 と同じ帯。
この帯を 12b や クラウドへ回す理由は無い。


## P11（B2）— MoE の RAM 実測プローブ（2026-08-24・実行は 32 GB 機で）

`gemma4:26b`（26B A4B・重み 16.75 GiB）は **16 GB 機では重みだけで不成立**（registry manifest の
机上で確定）。32 GB 機での成立判定を数分で出す道具が `moe_ram_probe.py`——ollama の API と OS の
数字だけで、num_ctx ごとの常駐サイズ・load・prefill / decode を測り、「常駐が 物理 − 余白 3 GiB を
超えたら不成立」を機械で判定する。**pull はしない**（16.75 GiB のダウンロードは人が承認してから）。

```bash
# 対象マシン（RAM 32 GB の推論機）で。pull は人の承認の後
ollama pull gemma4:26b
python3 tools/agent-tools/eval/moe_ram_probe.py --model gemma4:26b --output /tmp/moe-ram.json
```

成立なら次は計画 P11 の手順 2——`judge_eval` / `text_eval` / `worker_eval` の既存セルへ
`--model gemma4:26b` を差し替え、基準線（e4b・12b）と並べる。e4b（8.95 GiB）との同居は
25.7 GiB + KV で際どいので、`keep_alive` で両方常駐させる運用は前提にしない（probe の判定は
単独常駐の話である）。
