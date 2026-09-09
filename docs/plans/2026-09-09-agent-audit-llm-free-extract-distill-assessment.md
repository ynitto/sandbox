# agent-audit の extract / distill を LLM なしで回す検討

> 対象: `tools/agent-audit/`
> 日付: 2026-09-09
> 関連: [`agent-audit-design.md`](../designs/agent-audit-design.md) ADR-2 / ADR-3、[`agent-audit-spec.md`](../specs/agent-audit-spec.md) §6

## 結論

できる。しかも設計書 ADR-2 が自分で書いた撤退条件（「決定的な抽出器で同等の recall が得られる分野が増えたら、その kind から LLM を外す」）をこの機の実測が満たしている。

- 定期実行の枠はすでにある。`~/.agents/agent-loop.yaml` の `audit-calibrate` hook が 60 分ごとに collect → qualify → calibrate → extract → distill --review → tune を回す宣言になっている。cron 例も setup guide §6 にある。
- 止まっているのは LLM 段だけ。extract の最終実行は 2026-08-10、distill は 08-09。それ以降 205 件の候補が未抽出のまま積んである。
- その LLM 段が過去に出したものは、5 件の観測がすべて同じ 1 文（codex-native の `model` が空）で、洞察は 0 件。これは収集器のデータ欠落であって運用の知見ではない。決定的なルールで同じ結論が 0 秒で出る。

提案は「決定的な抽出器を extract / distill の既定にし、LLM は transcript を保存している record に限った上乗せにする」。実装は 1 モジュール（`rules.py`）と既存 2 コマンドへの分岐 1 か所ずつ。store・gate・tasks・report・tune はそのまま使う。

## いまの状態（この Mac の実測）

| 項目 | 値 |
|---|---|
| records | 30,616 行（session 11,409 / session-usage 7,689 / ledger 11,244 / calibration 231 / event 36 / memory 7） |
| `kind: run` / `result` の record | 0 件（flow-bus・project-root・amigos・loop-log を源泉に入れていない） |
| extract 済み | 16 record |
| observations | 5 件。全部 `config-issue`「codex-native の model が空」 |
| insights | 0 件 |
| extract の未処理候補 | 205 件。全部 `long-session`（run record が無いので他の filter は当たらない） |
| audit workload の LLM 消費 | 381 呼び出し、2,733 秒（2026-08-09〜10 の 2 日間） |
| control の audit 振り先 | cursor / grok-4.5（pause ではない） |

381 回呼んで 16 record しか進んでいないのは、extract が失敗を「呼び出し回数に数えて record は未処理のまま残す」仕様のため、同じ record が毎時間再投入されていたから。約 45 分の LLM 時間で得た知見が 1 文。

もう一つ大事なのは **材料が無い** こと。extract の filter（failed / retried / verify-flip / needs）が見る `status`・`error_class`・`retries`・`verify` は run / result record にしか無く、この機には 1 件も無い。session record が持つのは turns・seconds・tokens・model だけで、transcript も保存していない（`with_transcripts: false`）。この入力で LLM に「改善点」を訊いても、model が空だという以上のことは言えない。LLM の recall が低いのではなく、読ませているものに情報が無い。

## LLM 段が本当にやっていること

| 段 | 決定的にできる部分（既に Python） | LLM にしか任せていない部分 |
|---|---|---|
| extract | 候補の選抜、digest の切り詰め、間隔・蓄積・回数ゲート | record 1 件 → `kind` と `text` の観測文 |
| distill | クラスタリング（token overlap）、蓄積ゲート、洞察 id の改訂 | クラスタ → `statement`・`kind`・`suggested_action`・`confidence`・`declaration` |
| review | なし | supported / weak / refuted |

下流が洞察から読む項目は、`tasks` が `statement` / `suggested_action` / `occurrences` / `confidence`、`tune` が `declaration`（型付き）と `review.verdict`、`report` が一覧表示。

`declaration` は tune の許可パス 3 種（tuning の injections / env、profiles の tier candidates、rates）へ書くためのものだが、rates は `calibrate` が、tier candidates は `qualify` がすでに決定的に書いている。LLM だけが生成できる宣言は「profile に足す injection 文」で、この機では一度も昇格していない（`tuning_promotions: 0`）。

つまり LLM 固有の価値は「transcript を読んで未知の失敗パターンを一般化する」の 1 点に絞られる。それは transcript を保存し、run record を集めて初めて成立する。

## 提案: 決定的抽出器を既定にし、LLM を上乗せにする

### extract

`rules.py` に record → observations のテンプレを置く。observation の器（`kind` 5 種、`text`、`evidence`、`extract_agent`）は変えない。`extract_agent` を `rules` にして LLM 由来と区別する。

| 条件（record の項目） | kind | text のテンプレ |
|---|---|---|
| `status: failed` または `error_class` あり | avoid | `{tool}/{workload} で {agent_cli}:{model} が [agent-error:{error_class}] で失敗` |
| `retries >= 2` | prompt-issue | `{tool}/{workload} の {purpose} が {retries} 回再試行` |
| `verify: fail` | avoid | `{tool}/{workload} で verify 不合格（{agent_cli}:{model}）` |
| `escalations > 0` | skill-gap | `{tool}/{workload} が人へ {escalations} 回エスカレーション` |
| `decision_comparisons` に `agree: false` | config-issue | `{decision} で LLM 判定と決定的ルールが不一致` |
| session で `model` 空 または `measured: false` | config-issue | `{source} の session に model / usage が記録されない` |
| long-session | learn | `{agent_cli}:{model} の session が {turns} turn / {seconds}s` |

observation id は `observation_id(rec_id, index)` を rules と LLM で衝突させないよう、rules 側は index にルール名を使う（`"rule:failed"` など）。

digest・ゲート・`extracted` の記帳はそのまま。LLM は `excerpt_ref` を持つ record にだけ、かつ LLM が使える（control が pause でない、budget が残る）ときだけ呼ぶ。呼べなくても rules の観測は残るので exit 1 で hook を止めない。

### distill

クラスタ鍵を token overlap から構造鍵 `(kind, テンプレ名, tool, agent_cli, model, error_class)` へ変える。rules 由来の観測は text が定型なので overlap 0.5 でも同じ結果になるが、鍵で束ねる方が読める。LLM 由来の観測は従来どおり overlap で束ねる。

洞察はテンプレで組む。

- `statement`: クラスタ代表の text に件数と期間を付ける
- `kind`: avoid → rule-candidate、skill-gap → skill-improvement、config-issue → config-fix、learn / prompt-issue → usage-optimization
- `confidence`: occurrences 2〜4 で low、5〜9 で medium、10 以上で high
- `scope`: `{purpose, model}` を record から写す
- `suggested_action`: kind ごとの定型 1 文（「rules.md に禁止事項として足す」「収集器の reader を直す」など）
- `declaration`: null 固定。型付き宣言は calibrate / qualify に任せる
- `review`: null。`--review` は LLM が使えるときだけ効く

`tasks` はこのまま agent-project へ出せる。`tune` は declaration が null なので昇格しない（現状と同じ）。

### 設定と既定値

`agents.extract` / `agents.distill` に `{agent_cli: rules}` を書けるようにし、これを組み込み既定にする。LLM を使いたい人は従来どおり `{agent_cli: ollama}` などを書く。`rules` のときは ledger への記帳も control の参照もしない。

### 順序

1. `rules.py` と extract / distill の分岐、テスト（`test_extract_distill.py` に stub 無しのケースを足す）。
2. 設計書 ADR-2 に「config-issue / avoid / prompt-issue / skill-gap は決定的抽出へ移した。LLM は transcript ありの record 限定」と追記。仕様書 §6 の表に `rules` 行を足す。
3. hook はそのまま。`agents` を書かなければ次の 60 分で rules が走る。

## 却下した案

- **hook から extract / distill を外すだけ**。定期実行は続くが洞察は永久に 0 のまま。いまと同じ。
- **control で audit を pause にする**。同上。LLM 呼び出しは止まるが 205 件は積み上がり続ける。
- **`stats` の集計を直接 tasks へ出す**。observations / insights の層を飛ばすので「同じ失敗が前回より増えた」という時間軸の判定（`clusters` の既知件数）を捨てることになる。層は残す方が安い。
- **ollama を extract に固定する**。設定例の既定はこれだが、この機では extract の入力に情報が無いので弱モデルでも強モデルでも出るものは同じ。呼ぶだけ無駄。

## 前提と注意

- **run record を集めなければ rules も LLM も avoid / prompt-issue / skill-gap を出せない**。agent-audit.yaml に `flow_buses` / `project_roots` / `loop_logs` を書くのが先。これは本提案と独立に必要で、2026-08-29 の棚卸し（`local-llm-open-items-inventory.md` 穴 2.1）でも同じ指摘をしている。
- session record だけの現状で rules が出せるのは config-issue（model 空）と learn（長時間 session）の 2 種。前者は収集器を直せば消える。
- hook が実際に発火しているかは未確認。`~/.agents/agent-loop.log` に audit の行が無く、extract の最終実行が 08-10 で止まっている。ps に見える agent-loop は statemachine 実行の 1 本だけで、`agent-loop.yaml` の prompts を回す常駐は見えない。rules 化しても常駐が無ければ回らない。
- 決定的抽出は既知のカテゴリしか言えない。未知の失敗を transcript から拾う仕事は LLM に残す。その入口は `with_transcripts: true` にして初めて開く。
