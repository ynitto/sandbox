# agent-flow — 自己回復リトライ設計（transient 障害の自律回復）

> 作成日: 2026-07-16 ／ ステータス: **実装済み**（2026-07-16・レイヤ1〜4＋§15 の教訓環流）
> 対象ブランチ: `claude/agent-flow-retry-recovery-gnhgf7`
> 関連ファイル: `tools/agent-flow/agent_flow/agent.py`, `continuation.py`,
> `orchestrate.py`, `work.py`, `run.py`, `daemon.py`, `bus.py`,
> `tools/agent-project/agent_project/brief.py`, `mr.py`, `config.py`
> 関連設計書: [agent-flow-design.md](./agent-flow-design.md)（§8.2 継続判断・§12 障害対応・§17 ADR task timeout）,
> [agent-flow-retry-inheritance-design.md](./agent-flow-retry-inheritance-design.md)（run 世代間の引き継ぎ）

---

## 1. 背景と問題

agent-flow には既に複数のリトライ・回復機構があるが、**「リトライすれば解決する見込みの失敗
（transient）」を自律的に回収する層が無い**。現状の障害点と扱いを棚卸しすると:

| # | 障害点 | 現状の扱い | 問題 |
|---|--------|-----------|------|
| A | ノード実行失敗（`run_agent` の rc≠0・空応答・timeout） | failed result → run 静止 → 評価役が retry ノード生成（`max_retries` 系統ブレーカ） | **一時エラーでも重い経路**: 静止待ち＋評価役 LLM 1 呼び出し＋グラフ書き換えを経由し、内容失敗と同じ `retries` 予算を焼く |
| B | 環境要因の失敗（quota/auth/env トリアージ） | `_env_failure_reason` が run を即 failed 終端（人が直す） | 妥当（変更なし）。ただし quota は「時間をおけば回復」なのに人手が要る |
| C | transient 分類の失敗（接続断・5xx・overloaded） | 分類タグは付くが**専用の扱いが無い**＝ A と同じ経路 | トリアージの `transient=一時的（通常リトライで解ける）` という宣言が実装されていない |
| D | エージェント CLI のハング | task timeout で kill → failed → A の経路（ADR §17） | timeout の失敗文言（日本語）が transient パターン（英語）に**マッチせず未分類**扱い |
| E | planner（LLM）失敗 | 黙って stub 戦略へフォールバック | 一時エラーでも**再試行せずいきなり品質劣化**（stub のキーワード分解） |
| F | 評価役（LLM）失敗 | `_evaluator_fallback` → 未達ノードが残れば run failed 終端 | 一時エラー 1 回で **run が即死**。回収は人 / agent-project 頼み |
| G | 出力契約違反（split が JSON 配列を返さない・verify/evaluator の JSON 崩れ） | 寛容パースで救えなければ data=None → 展開されない / fallback 終端 | LLM の形式ミスは**同じ指示＋エラー指摘の再呼び出しでほぼ直る**のに、しない |
| H | ワーカー死亡（crash/OOM） | lease 失効 → 再 claim（自動） | 妥当（変更なし） |
| I | orchestrator/daemon 消失 | 生存リース失効 → daemon が adopt・`max_resumes`（自動） | 妥当（変更なし） |
| J | git push 競合 | `pull --rebase` リトライ（自動） | 妥当（変更なし） |
| K | run が failed で終端した後 | **自律回復なし**。人の `run --run-id`（`retry_failed`）か agent-project の新世代 run（`--inherit-from`）のみ | transient 起因の failed run は「少し待って再開」すれば直る見込みが高いのに、外部依存 |

つまり **H/I/J（プロセス・転送層）は自己回復が済んでいる**が、**LLM 呼び出し層（A/C/D/E/F/G）と
run 終端後（K）に自己回復が無い**。本設計はここを埋める。

### 検証失敗という語について

「検証で失敗」には 3 つの別物が混ざるので先に切り分ける:

1. **verify ノードの実行エラー**（LLM 呼び出し自体が失敗）→ 本設計の対象（障害点 A/C）。
2. **verify=fail**（検証は成功し「内容が不合格」と判定）→ **内容の問題**。既存の
   作り直しループ（`replaces`＋再検証・`max_retries`）が正しい経路であり、変更しない。
3. **agent-project の検証コマンド NG**（終了コードゲート）→ agent-project の責務
   （`retries`＋`--inherit-from` の新世代 run）。本設計の対象外だが、境界を §8 に記す。

---

## 2. 手本: Claude Dynamic Workflows の失敗の扱い

agent-flow の元ネタである Claude（公式）Dynamic Workflows / Workflow ハーネスは、失敗を
**層ごとに握りつぶし方を変えて**吸収する:

| Claude DW の機構 | 内容 | agent-flow への対応（本設計） |
|---|---|---|
| API エラーの内部リトライ | `agent()` は「terminal な API エラーで**リトライの後に**死ぬ」＝一時エラーはハーネスが**呼び出しの内側で**再試行し、スクリプト層に見せない | §4 レイヤ 1: `run_agent` 内の transient 再試行 |
| structured output の検証リトライ | schema 不一致は「tool-call 層で検証されモデルが**その場で再試行**」＝形式ミスはグラフに漏らさない | §5 レイヤ 2: 出力契約違反の修復リトライ |
| null 伝播と filter | 死んだ agent は null になり、スクリプトが `.filter(Boolean)` で流れを止めずに続行 | 既存: 静止判定＋`replaces` 付け替え（デッドロック回避）に相当 |
| resume（cached prefix） | `resumeFromRunId` で完了済み呼び出しは即キャッシュ返し・失敗以降だけ再実行 | 既存: `retry_failed`（done 温存・failed のみ pending 戻し）／`inherit_from` に相当 |
| 予算・上限 | agent 総数 1000・budget 天井で暴走を有界化 | 既存: `max_iterations`/`max_fanout`/`max_retries`。§9 で新レイヤにも上限を規定 |

**要点**: Claude DW は「一時エラーは呼び出しの最下層で透明に再試行し、上位層（グラフ・再計画）
には内容の失敗だけを見せる」。agent-flow は現状すべての失敗を最上位（再計画）まで持ち上げている。
本設計はこの**階層化**を移植する。

---

## 3. 設計原則: 失敗の三分法 × 回復レイヤ

失敗を既存トリアージ（`classify_agent_failure`）の軸で 3 種に分け、**種別ごとに回復する層を固定**する:

| 失敗種別 | 例 | 回復レイヤ | 理由 |
|---|---|---|---|
| **transient**（リトライで解ける） | 接続断・ECONNRESET・5xx・overloaded・timeout | **レイヤ 1**（呼び出し内・即時）→ 残れば **レイヤ 4**（run 再開・cooldown 付き） | 待って同じことをやり直せば直る。グラフを触る必要が無い |
| **内容の問題**（タグ無し・verify=fail） | 成果物が要求を満たさない | **レイヤ 3**（既存の再計画 retry・変更なし） | 同じ入力の再実行では直らない。作り直し・付け替えが必要 |
| **環境の問題**（quota/auth/env） | 認証切れ・CLI 不在・利用上限 | 即 run failed 終端（既存）。quota のみ opt-in でレイヤ 4 | どのノードをリトライしても同じ理由で落ちる。人が直す（quota だけは時間が直す） |

レイヤ構成（下ほど安く・速く・先に効く）:

```
レイヤ1  in-place リトライ      run_agent 内。transient を数秒〜数十秒のバックオフで再試行
レイヤ2  形式修復リトライ        出力契約違反（JSON 崩れ等）を「エラー指摘付き再呼び出し」で 1 回修復
レイヤ3  グラフ再計画リトライ    既存（retry ノード・replaces・max_retries）。内容の失敗専用に純化
レイヤ4  run 自己回復（auto-heal）daemon が transient-failed run を cooldown 後に retry_failed → 再開
```

**不変条件**: 上のレイヤで吸収された失敗は下のレイヤの予算（`max_retries`・`max_resumes`）を
**消費しない**。逆に、上のレイヤで回収し切れなかった transient は**ノード単位で粘らず run 単位で
打ち切って**レイヤ 4 に渡す（26 ノード × max_retries を焼き尽くした quota 事故と同じ理屈。
環境がまだ不調なら他ノードも同じ理由で落ちるため）。

---

## 4. レイヤ 1: in-place リトライ（`run_agent` の transient 再試行）

### 4.1 仕組み

`run_agent`（全 LLM 呼び出しの単一チョークポイント。planner / evaluator / worker 全 kind /
裁定が通る）に再試行ループを入れる:

```
run_agent(prompt, model, purpose):
  for attempt in 0..transient_retries:
    try:
      return _run_agent_once(...)          # 現在の run_agent 本体
    except RuntimeError as e:
      cls = classify_agent_failure(str(e))
      if cls != "transient" or attempt == transient_retries:
        raise                              # 非 transient・試行し尽くし → 従来どおり上位へ
      sleep(transient_backoff * 2**attempt + jitter)   # 既定 5s → 10s → 20s…
```

- **対象は transient 分類のみ**。quota は回復に分〜時間単位を要するため対象外（即 raise →
  既存の `_env_failure_reason` 経路）。auth/env も対象外（人が直す）。内容の問題（タグ無し）も
  対象外（同じ入力の再実行は無意味・レイヤ 3 の仕事）。
- **timeout を transient に編入**: `subprocess.TimeoutExpired` 時の RuntimeError 文言に
  `[agent-error:transient]` タグを明示付与する（現状は日本語文言が英語パターンに掛からず
  未分類＝内容失敗扱いになっている・障害点 D）。ハングは一時的な確率が高く、再試行 1 回の
  価値がある。恒久ハングでも試行ごとに timeout で有界。
- **回数・間隔**: `transient_retries`（既定 2）・`transient_backoff`（既定 5 秒・指数×2＋
  ジッタ）。1 ノードの最悪 wall time は `(1+retries) × agent_timeout + Σbackoff` で有界。
- **分散安全性**: 実行中は既存の Heartbeat が claim lease を延長し続けるため、再試行で実行が
  延びても他ワーカーに横取りされない（機構の追加不要・ADR §17 の合成と同じ）。
- **ログ**: 各再試行を `log()` に出す。最終失敗時のエラー文言に「(N 回再試行後)」を含め、
  レイヤ 4 の判定（§7）とヒトの調査の両方が「レイヤ 1 は済んでいる」ことを読めるようにする。

### 4.2 ワークスペースの衛生（実装ノート）

executor=agent の work 系ノードはワークスペース clone を編集しうるため、失敗した試行が
**部分編集を残したまま**再試行に入る可能性がある。これは現行の再計画 retry（同一 run の同一
clone を再利用）と同じ性質であり、本設計では**悪化させない**（許容）。決定的に潔癖にしたい
場合の `git reset --hard` による試行間クリーンアップは、レイヤ 1 が `run_agent`（バス・
ワークスペースを知らない free 関数）にある都合で**将来課題**とする（§13）。

### 4.3 なぜ `run_agent` 内か（配置の判断）

- worker（`call_executor`）・orchestrator（planner/evaluator）の各呼び出し点に個別ループを
  置く案もあるが、**1 箇所で全役割に効く**チョークポイントが既にあるのだから使う。
  障害点 A/C/D/E/F の「LLM 呼び出しの一時失敗」を一挙に覆う。
- プラグイン executor（gitlab 等）は `run_agent` を通らないが、そちらは**ポーリング内で
  自前の再試行**を既に持つ（マージ API 失敗は決着させず次回再試行、など）。プラグイン契約に
  リトライを足すのは各プラグインの責務とし、本設計では触らない。
- 二重リトライの防止: レイヤ 1 は `run_agent` の**内側だけ**。worker / orchestrator は
  従来どおり例外を 1 回で上位へ流す（外側にループを足さない）。

---

## 5. レイヤ 2: 形式修復リトライ（出力契約違反の 1 回修復）

### 5.1 仕組み

LLM の応答が**出力契約**（split の JSON 配列・verify の `{"ok",...}`・evaluator の
decision JSON・planner のグラフ JSON）を満たさないとき、**同じプロンプト＋「前回の出力は
契約違反（何がどう壊れていたか）。契約どおりにだけ再出力せよ」**を付けて 1 回だけ呼び直す
（`format_retries` 既定 1）。Claude DW の structured output 検証リトライの移植。

共通ヘルパを 1 つ用意し、既存の寛容パーサと組み合わせる:

```
def run_agent_structured(prompt, model, purpose, parse, describe_violation):
    text = run_agent(prompt, model, purpose)         # レイヤ1 込み
    ok, value, why = parse(text)
    for _ in range(format_retries):
        if ok: return value
        repair = (prompt + "\n\n[前回の出力は契約違反でした] " + why +
                  "\n説明・前置きを付けず、契約どおりの形式だけで再出力してください。")
        text = run_agent(repair, model, purpose)
        ok, value, why = parse(text)
    if ok: return value
    raise FormatContractError(why, last_text=text)   # 上位は従来のフォールバックへ
```

### 5.2 適用箇所と失敗時の落ち先

| 箇所 | 契約 | 修復後も違反なら（従来どおり） |
|---|---|---|
| `plan_strategy_agent` | パターン＋tasks の JSON | stub 戦略へフォールバック（既存） |
| `continue_agent`（評価役） | decision JSON | `_evaluator_fallback`（既存。ただし §6 参照） |
| `execute_agent` kind=split | 文字列 JSON 配列 | data=None → 評価役が状況を見て判断（既存） |
| `execute_agent` kind=verify | `{"ok","issues"}` | `_normalize_verify` の推定（既存） |
| kind=reduce | count 整合 | `_reconcile_count` の補正（既存） |

- **寛容パーサが先・修復リトライは後**: `extract_json`／`_normalize_verify` で救える崩れは
  呼び直さない（呼び直しは LLM 1 回分のコスト）。パーサでも救えないときだけ修復を試みる。
- 修復呼び出しも `run_agent` を通るのでレイヤ 1 の transient 再試行が内側で効く（合成）。

---

## 6. レイヤ 3 の純化と、評価役失敗の扱いの是正

レイヤ 3（再計画 retry）は変更最小に留め、**内容の失敗専用**へ純化する:

1. **transient はレイヤ 3 に到達させない**: レイヤ 1 で回収できなかった transient 失敗ノードは、
   `_env_failure_reason` の判定対象に **transient を追加**して run を打ち切る（§7 のレイヤ 4 が
   拾う）。これで retry ノード生成（＝`retries` 予算・評価役 LLM 呼び出し）は内容の失敗だけに
   使われる。quota 事故で 26 ノードが個別にリトライを焼いた既知の失敗モードの transient 版を
   同じ手で塞ぐ。
2. **失敗の構造化**: worker が failed result を書くとき、トリアージ結果を `data.error_class`
   （`transient`/`quota`/`auth`/`env`/`content`）と `data.attempts`（レイヤ 1 の試行数）に載せる。
   現状は output 文字列先頭のタグだけで、評価役・viewer・agent-project が文字列マッチに依存
   している。評価役プロンプトの結果サマリにも error_class を出し、「リトライで直る失敗では
   ない」ことを判断材料として明示する。
3. **評価役の一時失敗で run を殺さない**（障害点 F の是正）: `continue_agent` の例外パスは
   現状 transient でも `_evaluator_fallback`（未達あり→failed 終端）に落ちる。レイヤ 1・2 で
   大半は吸収されるが、それでも失敗した場合は**分類を見て**落ち先を分ける:
   - transient / quota → run failed 終端（理由に `[agent-error:…]` タグ）＝レイヤ 4 の回収対象。
   - それ以外（JSON 崩れが修復でも直らない等）→ 従来の `_evaluator_fallback`。

verify=fail → 作り直しループ、classify ルーティング、`replaces` 付け替え、サーキット
ブレーカーはすべて**無変更**。

---

## 7. レイヤ 4: run 自己回復（daemon auto-heal）

### 7.1 何を拾うか

failed 終端した run のうち、`meta.failure_reason` のトリアージタグが**自己回復候補**のもの:

| タグ | 既定 | cooldown |
|---|---|---|
| `transient`（レイヤ 1 を経てなお失敗） | **回収する**（`auto_heal: true`） | `heal_backoff`（既定 300s・指数） |
| `quota`（利用上限） | 回収しない（`heal_quota: false`・opt-in） | `quota_cooldown`（既定 3600s） |
| `auth` / `env` / 内容失敗 / canceled / superseded | 回収しない | —（人・agent-project の責務） |

### 7.2 仕組み

daemon の poll ループに auto-heal ステップを足す（既存の孤児 adopt と並ぶ回復ステップ）:

```
for run in failed runs where failure_reason タグ ∈ 回収対象:
  1. superseded / canceled / cancel-requested → skip（新世代・人の意思を尊重）
  2. meta.heal_next_at 未到達 → skip（cooldown 中）
  3. heal_count >= max_heals かつ前回から done ノード増加なし → skip
     （進捗があれば heal_count をリセット＝max_resumes と同じ「進捗で数え直す」思想）
  4. reclaim_request で claim（分散時も 1 daemon だけが heal する・既存プロトコル流用）
  5. bus.retry_failed()（failed ノードだけ pending へ・done 温存・簿記掃除 — 既存）
     meta に heal_count+1 / heal_next_at = now + heal_backoff * 2**heal_count を記録
  6. orchestrator を再 spawn（既存 _spawn_orchestrator。graph 有り → resume 動作）
```

- **簿記は meta に閉じる**: `heal_count` / `heal_next_at` / `heal_progress`（前回 heal 時の
  done ノード数）。`retry_failed` の簿記掃除リストに**含めない**（heal 横断で数える必要が
  あるため）。人の明示 retry（`run --run-id`）では従来どおり全部リセットしてよい。
- **上限**: `max_heals`（既定 2・進捗でリセット）。`max_resumes`（孤児再開）とは独立のカウンタ
  （対象が違う: resumes=orchestrator 消失、heals=transient failed 終端）。
- **単発実行（`cmd_run`・daemon 無し）**: 監視ループ内で同じ判定を行い、プロセス内で
  orchestrator を再 spawn する（バス簿記は共通）。`auto_heal: false` で無効化。

### 7.3 agent-project（消費者）との競合整理

agent-project は failed run を見ると `retries+1` の**新世代 run**（`--inherit-from` 付き）を
作る。auto-heal と二重回復になり得るが、既存機構が決定的に裁く:

- daemon は**新世代 run の存在を検知したら旧 run を superseded にマークする**
  （`_superseded_run_ids`・既存）。auto-heal はステップ 1 で superseded を skip するため、
  消費者が先に動けば heal は身を引く。
- heal が先に動いて run が running に戻れば、消費者側の `inherit_from` は安全条件
  （「実行中でリース有効なら no-op」）で旧 run を壊さない（既存）。
- 残る競合窓は「heal 直後〜消費者が新世代を submit するまで」の短時間だが、どちらの経路も
  done 温存・冪等（`retry_failed` / `inherit_from`）なので、最悪でも重複実行であって破壊は無い。
  agent-project 側の推奨設定（`act_timeout=0`）では消費者は run の終端まで待つため、
  heal で running に戻った run はそのまま待たれ、競合自体が起きない。
- **agent-project 側フォローアップ（本設計の対象外・推奨）**: `_settle_failure` のトリアージで
  transient を env 系と同様「リトライを焼かない」扱いにし、`heal_next_at` が生きている run は
  据え置く。これで消費者側の retries 予算も transient で減らなくなる。

---

## 8. 検証失敗の経路（まとめ）

| 事象 | 種別 | 回復経路 |
|---|---|---|
| verify ノードの LLM 呼び出しが一時失敗 | transient | レイヤ 1（その場で再試行）→ 残ればレイヤ 4 |
| verify の出力 JSON が崩れる | 形式 | 寛容パース → レイヤ 2（修復 1 回）→ `_normalize_verify` 推定 |
| verify=fail（内容不合格） | 内容 | レイヤ 3（既存: 依存の作り直し＋再検証・`max_retries`）— 無変更 |
| agent-project の検証コマンド NG | 内容（上位層） | agent-project の retries＋`--inherit-from`（既存）— 対象外 |
| 検証コマンド自体が環境起因で走らない | 環境（上位層） | agent-project の env トリアージ（既存）— 対象外 |

---

## 9. 暴走ガード（予算の掛け算を有界に保つ）

新レイヤ追加で最悪ケースの掛け算が伸びるため、上限を明示する:

- 1 LLM 呼び出しの最悪時間: `(1 + transient_retries) × (1 + format_retries) × agent_timeout + Σbackoff`
  （既定: 3 × 2 × 600s ＝ 上限 1 時間・実際は transient と形式違反が同時多発しない限り遥かに短い）。
- ノード単位: レイヤ 1 の試行はレイヤ 3 の `retries` を消費しない。逆に transient は
  レイヤ 3 に入らないため、`max_retries × transient_retries` の掛け算は**発生しない**
  （経路が排他）。
- run 単位: `max_heals`（進捗リセット付き）× cooldown 指数バックオフで、恒久障害の run が
  無限に heal と失敗を往復しない。`max_iterations` / `max_fanout` / `max_retries` は据え置き。
- すべての再試行はイベント（`events/*.jsonl`）と meta 簿記に記録され、`status` / doctor が
  「どのレイヤで何回リトライしたか」を可視化する（§10）。

---

## 10. 設定・可観測性

### 10.1 設定キー（`CONFIG_DEFAULTS` / `agent-flow.yaml`）

| キー | 既定 | 意味 |
|---|---|---|
| `transient_retries` | 2 | レイヤ 1: transient の in-place 再試行回数（0 で無効） |
| `transient_backoff` | 5 | レイヤ 1: 初回バックオフ秒（指数×2＋ジッタ） |
| `format_retries` | 1 | レイヤ 2: 出力契約違反の修復再呼び出し回数（0 で無効） |
| `auto_heal` | true | レイヤ 4: transient-failed run の自動再開 |
| `heal_backoff` | 300 | レイヤ 4: heal cooldown 初期値（秒・指数） |
| `max_heals` | 2 | レイヤ 4: 進捗なし heal の上限（進捗でリセット） |
| `heal_quota` | false | レイヤ 4: quota 失敗も回収するか（opt-in） |
| `quota_cooldown` | 3600 | レイヤ 4: quota 回収時の cooldown（秒） |

CLI には `--auto-heal/--no-auto-heal` のみ出す（他は設定ファイル調整で足りる。閾値を
環境変数でなく設定ファイルに置くのは既存方針どおり）。子プロセスへは既存の `--config`
絶対パス伝搬で届く。

### 10.2 可観測性

- **events**: `retry_transient`（node/attempt/wait）・`format_repair`（node/why）・
  `run_healed`（run/heal_count/cooldown）を追加。
- **result**: failed result の `data.error_class` / `data.attempts`（§6-2）。
- **status**: run ヘッダに heal 簿記（`healed ×N`）、ノード行に `(再試行 ×N)` を表示。
- **doctor**: `transient_retries=0` かつ `auto_heal=false` の組み合わせに warn
  （自己回復が全て無効＝従来動作の明示 opt-out として扱う）。

---

## 11. 影響ファイル

| 箇所 | 変更 |
|---|---|
| `agent_flow/agent.py` `run_agent` | レイヤ 1: transient 再試行ループ・timeout への transient タグ付与・`run_agent_structured` ヘルパ追加 |
| `agent_flow/agent.py` `execute_agent` | レイヤ 2: split/verify の契約パースを `run_agent_structured` 経由に |
| `agent_flow/patterns.py` `plan_strategy_agent` | レイヤ 2: planner JSON の修復リトライ（失敗時 stub フォールバックは維持） |
| `agent_flow/continuation.py` `continue_agent` | レイヤ 2: decision JSON の修復リトライ。例外パスの分類分岐（transient→failed 終端タグ・他→fallback） |
| `agent_flow/orchestrate.py` `_env_failure_reason` | transient を打ち切り対象に追加（文言は「自動再開候補」に） |
| `agent_flow/work.py` `cmd_work` | failed result への `data.error_class`/`attempts` 記録 |
| `agent_flow/bus.py` | heal 簿記ヘルパ（`heal_count`/`heal_next_at`/`heal_progress` の read/write・`retry_failed` は heal 簿記を消さない） |
| `agent_flow/run.py` / `daemon.py` | レイヤ 4: auto-heal ステップ（superseded/canceled skip・reclaim・retry_failed・再 spawn）。cmd_run 監視ループにも同等処理 |
| `agent_flow/config.py` / `cli.py` / `agent-flow.yaml.example` | §10.1 の設定キー・`--auto-heal` |
| `agent_flow/status.py` / `doctor.py` | §10.2 の表示・診断 |
| `docs/designs/agent-flow-design.md` §12 | 障害対応表に本設計のレイヤを追記（実装時） |

---

## 12. テスト計画（`test_agent_flow.py` 追補）

- **レイヤ 1**: transient エラーを N 回注入 → N+1 回目で成功し done になる／quota・auth・
  内容失敗は再試行しない／`transient_retries=0` で従来動作／timeout 文言が transient 分類される。
- **レイヤ 2**: split が 1 回目に散文＋2 回目に正しい配列を返すモック → fan-out が展開される／
  修復しても壊れていれば従来フォールバック／`format_retries=0` で無効。
- **レイヤ 3 純化**: transient failed ノードが retry ノードを生まず run failed（タグ付き）で
  終端する／内容失敗は従来どおり retry ノード＋サーキットブレーカー。
- **レイヤ 4**: transient-failed run が cooldown 後に retry_failed→resume され done まで走る
  （stub＋失敗注入）／superseded・canceled は触らない／`max_heals` 超過（進捗なし）で打ち切り・
  進捗ありでカウントリセット／分散（GitBus）で heal の claim が 1 daemon に決まる。
- **予算**: `max_retries × transient_retries` の経路排他（transient がレイヤ 3 の retries を
  消費しない）をカウンタで検証。
- **回帰**: 評価役 fallback・env トリアージ・inherit_from・orphan adopt の既存テストが
  無変更で通ること。

---

## 13. マイルストーン

| M | 内容 | 価値 |
|---|---|---|
| M1 | レイヤ 1（run_agent 再試行＋timeout タグ）＋ failed result の error_class 構造化 | 最頻の一時エラーが最小コストで消える。以降の層の判定材料も揃う |
| M2 | レイヤ 3 純化（`_env_failure_reason` へ transient 追加）＋ 評価役例外の分類分岐 | transient がリトライ予算を焼かない・run 即死の是正 |
| M3 | レイヤ 4（daemon / cmd_run の auto-heal・簿記・上限） | 無人運転で failed run が自己回復 |
| M4 | レイヤ 2（形式修復リトライ）＋ status/doctor 可視化 | LLM 形式ミスの静かな劣化を解消 |

M1→M2 は依存（M2 は M1 が transient を先に減らす前提で打ち切りを強気にできる）。
M4 は独立で並行可。

将来課題: レイヤ 1 再試行前のワークスペース `git reset`（§4.2）／プラグイン executor 契約への
リトライヒント追加／agent-project 側の transient 据え置き（§7.3）。

---

## 14. 不採用案

- **worker / orchestrator の呼び出し点ごとの個別リトライループ**: `run_agent` 1 箇所で全役割に
  効くため冗長。二重ループは予算の掛け算を生む（§4.3）。
- **lease 失効による transient 回収**（再試行せず claim を手放して他ワーカーに任せる）:
  他ワーカーも同じ外部要因（API 障害）で落ちる公算が高く、claim の往復コストだけ増える。
  環境不調はノードを替えても直らない——だからこそ run 単位で cooldown するレイヤ 4 が正しい。
- **verify=fail（内容）の自動リトライ強化**: 内容の失敗は同一入力の再実行で直らない。既存の
  作り直しループ（評価役駆動）が正しい形であり、本設計では触らない。
- **全 failed run の無差別 auto-heal**: 内容・env 失敗は再開しても同じ結果になり、LLM 呼び出しを
  無駄に焼く。トリアージタグで回収対象を限定する（quota は opt-in）。

---

## 15. 実装で確定した追加事項（2026-07-16）

設計レビュー（learn とブリーフの棚卸し）で見つかった「教訓が届かない・死蔵される」穴を、
本実装に同梱して塞いだ。**自動リトライ（レイヤ1〜4）が「同じ試行のやり直し」を担い、
本節が「失敗の教訓を次の試行・将来のタスクに効かせる」を担う**——両輪で
「タスクノードでも run 単位でも自動リトライし、同じエラーを繰り返しにくい」が成立する。

### 15.1 inherit 時の meta.request 穴の修正（agent-flow）

`--inherit-from` の部分 done 引き継ぎで、`_seed_from` が **meta.request を旧 run の request で
コピー**していた（`ensure_run` は既存 meta を上書きしない）。worker は全体文脈 `run_request` を
meta から読むため、**リトライの引き金になった差し戻し指摘（run ブリーフ・feedback 入りの
新 request）が再実行ノードに届かなかった**。`inherit_from(..., request=)` で新世代の要求文を
渡し、seed される meta.request を新 request で上書きする（未指定は旧 request＝後方互換）。
`cmd_orchestrate` が `args.request` を伝搬する。

### 15.2 auto-heal はブリーフを再注入しない（仕様の明記）

レイヤ4 の heal は**同一 run の resume**（`retry_failed`）であり、submit を経由しないため
`build_request` は再構築されない＝heal 時点までに追記された run ブリーフは注入済み request の
まま変わらない。これは仕様: heal は transient 回復（知識の更新を伴わないやり直し）専用で、
**知識の更新を伴うやり直し（差し戻し・verify=NG）は従来どおり新世代 run（`--inherit-from`）**が
担い、そこでは §15.1 により最新ブリーフが届く。

### 15.3 教訓捕捉の単一入口 capture_insight（agent-project）

learn（タスク横断・類似時想起・恒久）とrun ブリーフ（タスク内・無条件注入・一時）は役割が
直交するため**ストアは統一しない**が、**捕捉の入口を `capture_insight(cfg, task, text, source,
learn=)` に統一**した。1 つの指摘が task スコープ（brief 追記）と project スコープ
（decisions/ の learn 行 → auto-resolve → hits 閾値で rules.md 昇格）へ射影される。
これにより従来 brief のみで learn へ届く道が無かった**ノード発見制約（source="node"）**が
learn ラダーに乗り、タスク完了後も教訓が死蔵されない。cohort 波及は発生源で learn 捕捉済みの
ため brief のみ（二重 learn を作らない）。feedback / revise / gitlab-reject は従来から
両スコープへ書いており挙動不変。

### 15.4 完了時のブリーフ退役 retire_brief（agent-project）

タスク done（`archive_task`）時に run ブリーフを**納品書へ転記してから削除**する:
蓄積された制約・教訓は `archive/<id>.md` の「## run ブリーフ」節に成果物として残り
（一般化できる項目は §15.3 の learn 射影で既に正本ラダーに居る）、`<root>/brief/` の死蔵と
**task-id 再利用時の前世代ブリーフ誤注入**を防ぐ。

### 15.5 実装メモ

- 失敗の構造化: worker は failed result の `data.error_class`（transient/quota/auth/env/content）
  と `data.attempts`（レイヤ1 の試行数）を記録し、`_env_failure_reason` は output のタグより
  こちらを優先して読む。
- timeout の transient 編入により、既存テスト `AgentTimeoutTests` はレイヤ1 無効
  （`_TRANSIENT_RETRIES=0`）で従来挙動を検証する形に更新。
- heal 簿記（`heal_count`/`heal_progress`/`heal_next_at`/`heal_exhausted`）は meta に閉じ、
  `retry_failed(clear_heal=True)`（人の明示 retry・既定）だけが白紙化する。auto-heal は
  `clear_heal=False` で呼び、heal 横断で「進捗なし回数」を数える。
- テスト: `TransientRetryTests` / `FormatRepairTests` / `TransientRunBreakTests` /
  `AutoHealTests` / `InheritRequestTests`（agent-flow）、
  `TestCaptureInsightAndRetireBrief`（agent-project）。
