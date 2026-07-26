# S6 + S7 詳細設計: 「エージェントが書き、人が直す」バックログと、spec のブラウンフィールド適合

ステータス: 実装済み（詳細設計 + 実装で確定した差分を反映）
入力: [`2026-07-25-agent-improvement-spec.md`](2026-07-25-agent-improvement-spec.md) §3 S6（C7・C8・C9）/ S7（C6）
前提: [`2026-07-26-s4-s5-review-and-verification-detailed-design.md`](2026-07-26-s4-s5-review-and-verification-detailed-design.md)
（`acceptance:` 書式は S5 側で確定済み。**本設計はそれに従う側**）
実装フェーズ: Phase 3（S6 → S7 の順）

S6 と S7 を 1 本にまとめる。両方が「実行前に人が読む 1 枚（バックログ md と計画レビュー票）」の
中身を決めており、別々に設計すると必須セクションの定義が二重になるため。

---

## 0. この設計が答える問い

仕様書 S6 は「エージェントが書き、人が直す」フローを定めたが、**そのために何を直せばよいかは
書いていない**。調査の結果、`acceptance:`（S5 で確定した一次表現）が **生成側・レビュー側・編集側の
どこにも通っていない**ことが分かった（§1.2）。S6 の主眼は新機能の追加ではなく、
**S5 が確定させた表現を、それを生む経路と人が直す経路に通すこと**である。

---

## 1. S6: バックログの生成・レビュー・整合

### 1.1 現状（調査結果）

| 要素 | 状態 |
|---|---|
| charter → backlog の分解 | `plan.py:101-125` `_plan_decompose_prompt` にプロンプトがハードコード。差し替え口なし |
| 分解プロンプトが出させる項目 | `title` / `verify` / `workspace` / `refs` / `after` / `cohort_items` / `why` / `out_of_scope` / `hints`。**`acceptance` は無い** |
| 既存タスクの注入 | **無い**。プランナーは毎回ゼロから全件出し、`_enqueue_specs` が投入側で Jaccard 照合して落とす |
| 重複照合 | `charter.py:776` `_is_duplicate` = 正規化タイトルの Jaccard ≥ `learn_threshold`（既定 0.5）のみ |
| 墓標 | **無い**。ただし `cmd_reject`（`needs.py:507-552`）が rejected として archive へ退避し、`_existing_titles` は archive も読む＝**却下タスクの再投入は既に弾かれている**。加えて `avoid:` DR を残す（`learn_capture` on かつ理由ありのとき） |
| 人編集タスクの保護 | replan は既存タスクを**上書きしない**（`_enqueue_specs` は重複を skip するだけ）＝上書き保護は実質もう効いている。穴は §1.6 の 2 つ |
| 計画レビュー票 | `needs.py:144-157` `_task_definition_block`。**`acceptance` を載せていない**（TASK_GUIDE_KEYS + accept/verify_template/after/note/workspace/charter/assess/route のみ） |
| dashboard の編集 | `revise` のフィールド編集。`REVISE_FIELDS`（`commands.py:297`）にも **`acceptance` は無い**。編集 UI 自体も未実装（ペイロード契約だけ先行） |
| 随時取り込み | `inbox/` `enqueue` `intake_cmd` の 3 経路。整合ステップは無い。`- charter:` タグも付かない |
| 観点メモ | `notes/` は**存在しない** |
| flow-planner | スキル分離済み。ただし `patterns.py:368-370` `_find_flow_planner_script()` が名前 `"flow-planner"` を**ハードコード**。agent-project 側の `flow.py:110` は `--planner` は渡すが **`--granularity` を渡していない** |

### 1.2 最初に直すもの — `acceptance` の受け渡しが 4 か所で切れている

S5 は `acceptance:` を検証の一次表現として確定させ、読み出し（`task_acceptance`）・検証・
検収カードまでは通した。だが**タスクに `acceptance` を入れる側**は誰も通っていない。

| # | 場所 | 症状 |
|---|---|---|
| a | `model.py:213-227` `task_from_spec` | 既知キーに `acceptance` が無いため、`{"acceptance": ["A","B"]}` は「未知キー保持」の枝（`:225-227`）に落ち、`str(list)` されて **`- acceptance: ['A', 'B']` という Python の repr 1 行**になる。`task_acceptance` はこれを 1 項目の基準として読む |
| b | `model.py:199` `has_plan` | `verify or accept or verify_template` のみ。**`acceptance` だけのタスクは inbox（人の triage）へ落ちる**。`verify.py:218` `has_verify_plan` は acceptance を数えるのに、投入側の判定と食い違っている |
| c | `needs.py:144-157` | 計画レビュー票に acceptance が出ない。**人は S5 が「人が読んで判断する材料」と定めたものをレビュー票で見られない** |
| d | `commands.py:297` `REVISE_FIELDS` | `acceptance` が無いので dashboard からも CLI からも直せない。直せるのは旧 `accept`（1 行）だけ |

**決定: S6 の実装は a〜d の修理から入る。**backlog-planner を先に作ると、生成した acceptance が
repr 1 行に潰れたまま人に見えない状態で回り始める。順序は §3 に反映する。

`acceptance` は**複数行フィールド**なので、単値前提の既存機構をそのまま使えない。契約を固定する:

- **spec（JSON）側**: `"acceptance": ["基準1", "基準2", ...]`（配列。文字列 1 本も許し 1 要素として扱う）
- **md 側**: `- acceptance: <1 基準>` を要素数だけ並べる（S5 §2.3・`backlog.md.example` の正典どおり）
- **`task_from_spec`**: `acceptance` を既知キーとして扱い、**配列は要素ごとに `t.extra.append` する**
  （`",".join` も `" ⏎ ".join` もしない。基準にカンマは普通に出る）
- **`has_plan`**: `verify.py:has_verify_plan` と同じ述語に寄せる（acceptance を数える）
- **`REVISE_FIELDS`**: `acceptance` を**リスト置換**として追加する。`revise` の置換規約（`''`/`'-'`/`'none'` は削除）は据え置き、値が配列なら全行を差し替える。dashboard の `REVISE_KEYS` も揃える
- **`_task_definition_block`**: acceptance を**箇条書きで**（1 行 1 基準）先頭付近に出す

`schemas/task.schema.json` に `acceptance`（`["array","string"]`）を追加する。S4/S5 では
「Task.extra が複数行を往復するのでスキーマ変更不要」と結論したが、それは **md の読み書き**の話。
**JSON 表現（enqueue --json / inbox/*.json / intake_cmd の stdout / backlog-planner の出力）**では
配列を宣言しないと a の repr 事故が再発する。

### 1.3 比較検討: 台帳（`backlog-ledger.jsonl`）を新設するか

仕様 S6-4 は「生成・人編集・承認・却下・削除のイベントを `backlog-ledger.jsonl` に追記する」と
している。ここは**真実の置き場が増えるか減るか**を左右するので、先に決める。

| 案 | 長所 | 短所 |
|---|---|---|
| ① 仕様どおり `backlog-ledger.jsonl` を新設 | 履歴が 1 か所に揃う。イベント型なので後から集計軸を足せる | **同じ事実が 3 系統に重複する**（journal＝時系列・`decisions/<id>.md`＝DR・archive＝却下の現物）。読む側が要るのは畳み込み後の集合（「今の墓標一覧」「このタスクは人編集か」）だけなのに、それを得るのに全行の畳み込みが要る。壊れたとき真実が 2 つになる |
| ② 既存の 3 つ（backlog/archive の md・`decisions/` の `avoid:`・journal）から**導出**する | 新しい真実を作らない。却下の再投入抑止は**もう効いている**（`_existing_titles` が archive を読む） | 「人が md を消しただけの削除」は痕跡が残らない。人が墓標を手で足せない。プランナー入力に載せるには毎回 archive 全走査 |
| ③ **属性の持ち主で分ける**（採用） | 人編集は**タスクに属する** → タスク md の `- edited: human`。墓標は**もう存在しないタスクに属する** → タスク md には置けないので専用ファイル `tombstones.md`。承認・却下の履歴は journal と DR が既に持っている | ファイルが 1 つ増える（が、イベントログではなく**現在の墓標一覧**なので畳み込み不要・人が読めて手で書ける） |

**採用: ③。**判断の軸は「その事実は誰に属するか」。イベント台帳は履歴を持つのが利点だが、
履歴は journal と `decisions/` が既に持っており、S6 が新たに要るのは**現在の集合**だけである。

```
<root>/tombstones.md
  # 墓標（このタスクは作り直さない。解除は agent-project revive）
  - cli チャットの起動先を選べるようにする :: 別案に置き換えた :: 2026-07-26 :: charter=v3
  - …                                      :: 理由            :: 日付      :: charter タグ（任意）
```

1 行 1 墓標・`::` 区切りは `followup:` / `learn:` / `avoid:` と同じ既存の語彙。人が直接書き足せる。
`cmd_reject` は archive への退避と DR の `avoid:` に加えて、この行を追記する。

### 1.4 `backlog-planner` スキル

`backlog-verifier`（S5 §2.4）と**完全に対称**にする。同じ形にしておけば、片方の運用知識が
そのままもう片方に効く。

- 実行は agent-project 内蔵の LLM 1 回呼び出し（`_run_agent_cli(purpose="plan")`。既存の
  `agents: plan:` 上書き・ノード予算・失敗トリアージがそのまま効く）
- プロンプトと出力契約は `.github/skills/backlog-planner/`（`SKILL.md` + `scripts/prompt.py`）。
  `scripts/prompt.py` は**プロンプトを組み立てるだけ**（LLM は呼ばない）。解決順は
  `find_skill_script`（`verify.py:226-254`）をそのまま使う
- 設定 `planner_skill`（既定 `backlog-planner`）でスキル名も差し替え可能
- スキルが見つからないときの組み込みプロンプトを残す（`_builtin_verifier_prompt` と同型）。
  **現行の `_plan_decompose_prompt` の中身がそのまま組み込みの本体になる**——計画が止まると
  プロジェクトが 1 歩も進まないので、スキルを必須にしない

**入力 JSON**（`SKILL.md` が契約の正典）:

| キー | 内容 |
|---|---|
| `charter` | `build_charter_request` の本文 + owns 注記（既存 2 関数の出力をそのまま） |
| `granularity` | `coarse` / `fine` / `finest`（`plan_granularity_directive` の語彙） |
| `rules` | `rules.md` の抜粋 |
| `repo_context` | `context/<repo>.md`（repo-map）の抜粋 |
| `existing` | 既存タスク `[{id, title, status, edited, summary}]`（**現行に無い**。§1.8） |
| `tombstones` | `[{title, reason}]`（**現行に無い**。§1.7） |
| `notes` | 観点メモの本文（`distill-notes` のときのみ。§1.10） |

**出力**: タスク spec の JSON 配列。既存キー（`title` / `verify` / `workspace` / `refs` /
`after` / `cohort_items` / `why` / `out_of_scope` / `hints`）に次を足す:

| キー | 必須 | 内容 |
|---|---|---|
| `acceptance` | **必須** | 受入基準 3〜7 項目の配列（S5 §2.3 の書式。verifier がこれを判定する） |
| `desc` | **必須** | 作業概要: 変更対象（リポジトリと主要ファイル/モジュールの見込み）・作業ステップ 3〜7 行・影響範囲 |
| `scope` | 任意 | 触ってよい範囲（`desc` の「変更対象」を機械が使える形にしたもの） |
| `size` | **必須** | `S` / `M` / `L`（分解の妥当性判断用） |
| `why` | **必須** | charter のどの目標に効くか 1〜2 文（現行は任意扱い → 必須へ） |

**新フィールドは `size` の 1 つだけ**にする。仕様 S6-2 の「作業概要」は既存 `desc`（作業内容の詳細）、
「変更対象」は `scope`、「やらないこと」は `out_of_scope` に載る。同じものに 2 つ目の名前を付けない。
`size` は誘導記述ではなく分解の妥当性判断のメタなので `TASK_GUIDE_KEYS` には入れない
（act 要求文に注入しない）。

### 1.5 必須セクションの決定的ゲート

`_validate_backlog_spec(spec) -> list[str]`（欠落キー名）を置き、**LLM の判断を待たずに機械で見る**。

| 段 | 挙動 |
|---|---|
| 1 回目に欠落 | 欠落したタスクだけを列挙して「これらを埋め直せ」と 1 回だけ再要求する（プロンプトに前回出力を添える） |
| 2 回目も欠落 | **捨てない。人の目に入る場所へ置く**（下表） |

仕様 S6-2 は「proposed に入れず再生成を要求する」までしか定めていない。2 回目を**捨てる**設計は
採らない——捨てると人には「プランナーが何も出さなかった」としか見えず、charter の書き方が悪いのか
スキルが壊れたのかを切り分ける材料が消える。**沈黙で落とすより、見える形で止める。**

置き場は**人が設定したレビュー面に合わせる**:

| 設定 | 置き場 | 理由 |
|---|---|---|
| `plan_review: on`（既定） | `proposed` | 計画レビュー票が立ち、`- needs_reason:` に欠落項目が出る。人が直して承認できる |
| `plan_review: off` | `draft` | 票が立たない設定なので、消化対象外にして journal に残す |

**`draft` 一択にはしない。**dashboard には draft → ready の昇格導線が無く（`revise` は status を
変えない）、票も立たないので、`plan_review` が on のプロジェクトでは draft タスクが誰の目にも
触れないまま滞留する。「捨てない」を守ったつもりで、実質捨てているのと同じになる。

`acceptance` の項目数（3〜7）は**ゲートにしない**（下限 1 のみ）。項目数は基準の性質で正当に
変わるもので、数で弾くと planner が水増しする方向に効く。7 を超えたら needs に注記だけ出す。

### 1.6 人編集の保護 — 穴は 2 つだけ

replan は既存タスクを上書きしない（§1.1）ので、仕様 S6-3 の「以後の replan で上書き・再生成の
対象外」は**大部分がもう成立している**。残る穴は:

**(a) 人が title を書き換えると、元 title のタスクが「新規」として再投入される。**
プランナーは charter から毎回同じものを出すため、人が「CLI チャットの起動先を選べるようにする」を
「起動先ドロップダウンの追加」に直すと、次の replan で元の title が重複照合をすり抜けて復活する。

→ 生成時に `- planned_title:`（プランナーが付けた原題）をタスクに残し、`_existing_titles` が
title と planned_title の**両方**を返す。人が題を直しても指紋は残る。

**(b) プランナーが「これは人が確定させた」を知らない。**
→ `existing[]` に `edited: human` を載せ、スキル入力契約に「`edited: human` のタスクは
**再提案しない**（改善案があれば別タスクとして出す）」を書く。

**`- edited: human` を付ける場所**は 2 つだけ:
- `_apply_revise_fields` が 1 件以上の変更を返したとき（CLI / dashboard の両方がここを通る）
- 計画レビュー票（needs）の確定チェックボックスが `[x]` になったとき（人が票を読んで承認した＝
  内容を人が引き受けた）

**エディタでの md 直接編集は検出しない。**タスク md の内容署名を持てば検出できるが、
署名の維持コスト（persist_task のたびに更新・状態同期との競合）に対して得るものが薄い。
直接編集する人は revise か needs のどちらかを通るのが普通で、通らない場合も (a) の
planned_title が復活を防ぐ。**検出できないものを検出しようとしない。**

### 1.7 墓標と revive（未決事項 5 後半の決着）

**抑止（hard suppress）は正規化タイトルの完全一致のみ。類似は抑止せず提示に回す。**

| 一致の度合い | 扱い |
|---|---|
| 正規化タイトルが**完全一致** | 投入しない（墓標） |
| Jaccard ≥ `learn_threshold`（類似） | **投入は止めない**。プランナー入力の `tombstones[]` に理由付きで載せ、投入時は needs に「却下済みの『〜』に似ています（理由: 〜）」を注記する |

根拠: **抑止は取り返しがつかない**（プランナーが出したものが黙って消えるので、人はそれが
起きたことに気づけない）。**提示は取り返しがつく**（人が見て却下すればよい）。Jaccard 0.5 は
「dashboard の board UI を作る」と「dashboard の board 観測 UI を作る」を同一視する強さがあり、
これを恒久抑止に使うと後から本当に要るタスクが起票できなくなる。

正規化 = NFKC → 小文字化 → `\w+` トークン化 → **区切り無しで連結**（`_norm_title`）。
区切りを残さないのは、日本語のタイトルは分かち書きの有無が書き手次第だから——「X をやる」と
「X を やる」は同じタスクであって別物ではない。**語順は保つ**（集合にしない）: 語順まで捨てると
「A を B にする」と「B を A にする」が同一指紋になり、逆向きのタスクを潰す。

**スコープ**: 墓標行の `charter=` タグが付いていればその charter のタスクにだけ効く。
タグ無しは全 charter に効く（人が手で書いた墓標の既定）。

**解除**: `agent-project revive <タイトル or 指紋>` で該当行を削除し journal と DR に残す。
`replan --revive` は**全墓標を 1 回だけ無視する**（行は消さない）。仕様 S6-4 の `--revive` を
「消す」ではなく「今回だけ無視する」にするのは、再分解の結果を見てから消すか決められるようにするため。

### 1.8 重複判定の 2 段化

| 段 | 実装 |
|---|---|
| ① 生成時（新設） | `existing[]` と `tombstones[]` をスキル入力に載せ、「既存と重複する項目は出力しない」を契約に含める |
| ② 投入時（現行維持） | `_is_duplicate` の Jaccard 照合。**最終防衛線として残す**（スキルは差し替え可能なので、投入側の護りを外すとカスタムスキルが重複を通せてしまう） |

仕様 S6-5 の①「決定的照合 = 指紋（正規化タイトル + workspace + charter タグ）の一致」は
**採らない**。workspace を指紋に含めると、同じ内容のタスクが「書込先の推定が揺れた」だけで
別物になり、重複が通る。workspace は `assign_plan_workspace` が推定で決めるフィールドなので
指紋の材料として不安定である。指紋は**正規化タイトル**（+ charter タグでスコープを切る）だけにする。

### 1.9 随時取り込みの整合パス（C8）

`inbox/` / `enqueue` / `intake_cmd` の 3 経路が通る 1 か所（`enqueue_task`）に整合ステップを置く。
既に `apply_intake_recall`（過去 hold との照合）がこの位置にあるので、その隣に並べる。

1. **重複照合**（§1.8 ②と同じ述語）。既存タスクと重複するなら**新規作成せず**、
   既存タスクへ `feedback` / `refs` として追記する案を needs に出す（人が採否を決める）
2. **charter 帰属の推定**: `- charter:` タグが無いタスクに、現行 charter 名を付けて投入する

**あわせて `project.py:480-482` のバグを直す。**

```python
has_consumable = any(
    t.consumable() and (not multi or task_charter_name(t) == charter_name)
    for t in current_tasks)
```

`_existing_titles` の `_match`（`charter.py:752-756`）は `tag in ("", charter)`＝**タグ無しは
どの charter にも属しうる**として扱うのに、ここは完全一致を要求する。結果、タグ無しの
消化可能タスクがあっても `has_consumable=False` になり、**再分解が誤発火する**。
2 つの述語を `task_belongs_to_charter(task, charter_name)` 1 つに寄せて `_match` 側の規則に揃える。
（2 の帰属推定が入れば新規タスクにはタグが付くが、既存のタグ無しタスクは残るので修正は要る。）

### 1.10 観点メモ（C9）

- `<root>/notes/*.md` に自由記述で書き溜める（dashboard に「メモを追加」UI）
- **plan は notes を自動で消費しない**（仕様どおり）。人の明示操作でのみ動く
- 操作: CLI `agent-project distill-notes` / dashboard ボタン（`commands/` に
  `{"command": "distill-notes"}` を投函。`replan` と同じプロジェクト単位の指示）
- 動作: `notes/*.md` を backlog-planner 入力の `notes` に載せて呼び、出力を**整合パス（§1.9）経由で**
  proposed 投入 → 消費したメモを `notes/archive/` へ移す

**CLI 名について**: 仕様 S6-7 は `agent-project distill` としているが、`distill` は既に
`distill_learn`（人コメント → 一般化ルールへの蒸留）で使われており、`agents:` の purpose キーにも
`distill` がある（`prioritize.py:83`）。同名にすると「どちらの蒸留か」が設定で区別できなくなるので
**`distill-notes`** にする。purpose は専用に増やさず `plan` を使う（呼ぶ相手が backlog-planner で
同じだから）。

### 1.11 flow-planner の名前固定と `--granularity` 欠落

S6 の主眼ではないが、仕様 S6-1 が「内側にも対称に」と定めた分の始末:

- `patterns.py:368-370` `_find_flow_planner_script()` のハードコードを解き、
  agent-flow の設定 `planner_skill`（既定 `flow-planner`）で解決する
- `flow.py:110` / `:194` の `agent-flow run` 引数に **`--granularity cfg.granularity` を足す**
  （agent-flow 側の `--granularity` は `cli.py:53` に既にある。渡していないだけ）

### 1.12 設定キー（すべてプロジェクト yaml 専有）

| キー | 既定 | 意味 |
|---|---|---|
| `planner_skill` | `"backlog-planner"` | バックログ分解のプロンプト・出力契約を供給するスキル名 |
| `plan_sections` | `"required"` | `required` = 欠落は 1 回再要求 → なお欠落なら draft（§1.5） / `warn` = 注記のみで proposed 投入 |
| `flow_planner_skill` | `"flow-planner"` | agent-flow 側（§1.11）。agent-flow の設定に置く |

`notes/` `tombstones.md` の場所は設定にしない（`backlog` の親から導出＝`verifications/` と同じ流儀）。

---

## 2. S7: spec のブラウンフィールド適合

### 2.1 現状（調査結果）

| 要素 | 状態 |
|---|---|
| ルーティング | `prioritize.py:779-816` `route_spec_tasks`。`spec_track`（既定 off）かつ `_assess_max(t) >= spec_threshold` または policy `spec:` 一致で spec タスクを前置 |
| 採点 | `- assess: c=N r=N a=N`（複雑さ / リスク / 曖昧さ、**各 1〜3**。`configfile.py:149`） |
| しきい値 | `spec_threshold` 既定 3。`configfile.py:757` で `min(3, max(1, ...))` にクランプ |
| spec の中身 | `spec.md` / `design.md` / `tasks.md` の 3 点セット固定（`_spec_instructions`）。verify は 3 ファイル非空 + `tasks.md` に `"title"` |
| 展開 | `expand_spec_tasks` が `tasks.md` の JSON を実装タスク群へ。元タスクは after を付け替えて総合検証として最後に走る |

**仕様の訂正**: 仕様 S7-2 は「既定: light=2, full=4 相当」としているが、**採点の上限は 3**
（各軸 1〜3・`_assess_max` は最大値）なので 4 には決して到達しない。**full=3 / light=2 とする。**

### 2.2 3 段ルーティング

| `_assess_max(t)` | ルート |
|---|---|
| `>= spec_threshold_full`（既定 3） | フル spec（現行どおり 3 点セット + tasks.md 展開） |
| `>= spec_threshold_light`（既定 2） | **ライト spec**（`design.md` 1 枚・展開なし） |
| それ未満 | スキップ（直接実行。現行どおり） |

- `spec_threshold`（旧）は**残して full に読み替える**（`spec_threshold_full` 未指定時のみ採用）。
  既存プロジェクトの設定を壊さない
- policy の `spec:` 強制は**フル**のまま（現行踏襲）。`spec_light:` は足さない——policy の語彙を
  増やす価値より、「強制したいものはフルでよい」の単純さを採る
- 決定は現行どおり `- route:` に記録して再ルーティングしない。人の `- route: direct` が常に勝つ

### 2.3 ライト spec の実体

フル spec との差は**成果物 1 枚と、展開の有無**だけ。既存プリミティブの組み替えで済ませる。

| | フル | ライト |
|---|---|---|
| タスクのマーカー | `- spec_for: T` | `- spec_for: T` + **`- spec_kind: light`** |
| 成果物 | `specs/<T>/spec.md` `design.md` `tasks.md` | `specs/<T>/design.md` のみ |
| verify | 3 ファイル非空 + `tasks.md` に `"title"` | `test -s specs/<T>/design.md` |
| `review: human` | あり | あり（据え置き。spec は人が見る前提） |
| tasks.md 展開 | する | **しない**（元タスクをそのまま実行） |
| 元タスクへの効き | after で実装タスク群の後ろへ | `design.md` を act の文脈へ注入 |

`design.md` の中身（`_spec_instructions` のライト版）: 変更方針・影響範囲・受入条件の差分記述。
**「既存コードのどこをどう変えるか」に絞り、要求仕様（spec.md）と実装分解（tasks.md）は書かせない。**
ブラウンフィールドでは要求は既に charter とタスクの `why`/`desc` にあり、分解は元タスクの粒度で足りる
——3 点セットのオーバーヘッドの正体はこの 2 枚である。

`expand_spec_tasks` は `spec_kind: light` を見て**展開せず** `spec_expanded: light` を立てる
（`none` とは区別する。`none` は「フルなのに tasks.md が壊れていた」という別の事象で、journal の
文言も違う）。

### 2.4 既存コード文脈の前置（S6-2 と共通機構）

S6-2 の必須セクション（`desc` に変更対象の見込みを書く）も S7 の `design.md`（影響範囲）も、
**repo 文脈が無いと書けない**。現状 `ensure_repo_maps` は `cfg.repo_map`（既定 off）の opt-in で、
plan の直前にだけ呼ばれる（`project.py:485`）。

**決定: plan 経路（backlog-planner 呼び出しの直前）では `repo_map` 設定に関わらず必ず走らせる。**
`repo_map` の意味は「plan 以外の経路でも生成するか」に縮小する。

理由: S6 は「変更対象の見込みを書けていないタスクは draft に落とす」という決定的ゲートを持つ
（§1.5）。文脈が無ければゲートが恒常的に発火し、**設定 1 つで機能全体が空回りする**。
コストは有界（HEAD sha キャッシュ済み・charter の書込先 repo 数分・変化が無ければ 0 回）。

ライト/フル spec タスクの前にも同じ `ensure_repo_maps` を呼ぶ（`route_spec_tasks` の中）。
生成に失敗しても空のまま進む（現行の失敗時挙動を維持＝ネットワーク断で計画が止まらない）。

---

## 3. 実装単位

| # | 対象 | 内容 |
|---|---|---|
| **S6-0a** | `model.py` / `schemas/task.schema.json` | `acceptance` を既知キー化（配列 → 複数行）・`has_plan` を `has_verify_plan` に寄せる |
| **S6-0b** | `needs.py` | `_task_definition_block` に acceptance を箇条書きで載せる |
| **S6-0c** | `commands.py` / dashboard `actions.js` | `REVISE_FIELDS` / `REVISE_KEYS` に `acceptance`（リスト置換） |
| S6-a | `.github/skills/backlog-planner/` | `SKILL.md` + `scripts/prompt.py`（§1.4 の入出力契約） |
| S6-b | `plan.py` | `plan_via_agent` をスキル呼び出しへ。現行プロンプトを組み込みフォールバックに移す。`_validate_backlog_spec` と 2 段ゲート（§1.5） |
| S6-c | `charter.py` / `plan.py` | `_norm_title` の共有・`planned_title`・`existing[]` の組み立て |
| S6-d | `needs.py` / `commands.py` | `tombstones.md` の読み書き・`cmd_reject` からの追記・`revive` サブコマンド・`replan --revive` |
| S6-e | `model.py` / `project.py` | 整合パス（§1.9）＋ `task_belongs_to_charter` への述語統一 |
| S6-f | `commands.py` / `cli.py` | `distill-notes`（notes → backlog-planner → 整合パス → proposed） |
| S6-g | dashboard | plan-review カードの acceptance 表示・項目単位の編集・メモ追加 UI・`distill-notes` ボタン |
| S6-h | agent-flow `patterns.py` / agent-project `flow.py` | `flow_planner_skill` と `--granularity` 伝播（§1.11） |
| S7-a | `configfile.py` | `spec_threshold_light` / `spec_threshold_full`（旧キーの読み替え） |
| S7-b | `prioritize.py` | 3 段ルーティング・`spec_kind: light`・ライト版 `_spec_instructions` / `_spec_verify` |
| S7-c | `prioritize.py` | `expand_spec_tasks` がライトを展開しない・`design.md` の act 文脈注入 |
| S7-d | `plan.py` / `prioritize.py` | `ensure_repo_maps` の plan 前置を無条件化（§2.4） |
| — | `backlog.md.example` / `README` / `CHANGELOG` | `size` / `planned_title` / `spec_kind` / `tombstones.md` / `notes/` |

**順序**: S6-0a/b/c → S6-a/b → S6-c/d → S6-e/f → S6-g → S6-h → S7-a/b → S7-c/d。

**S6-0（acceptance の受け渡し）を最初に単独で入れる**理由は §1.2 のとおり。ここだけで
「S5 が書式を決めた acceptance を、人がレビュー票で見て直せる」状態になり、
backlog-planner が無くても手書きタスクで先に効果が出る（＝独立に検証できる）。

---

## 4. 未決事項の決着（仕様書 §5-5）

### 4-1. 人編集タスクの保護と charter 大改訂の衝突

**決着: 自動では消さない。棚卸し票 1 枚で人に返す。**

charter が根本から変わったとき、`edited: human` のタスクを自動で却下・削除する経路は作らない。
「大改訂かどうか」を測る指標が無いためである（`_charter_full_signature` は**変わったか否かの
2 値**で、程度を持たない。程度を LLM に測らせる案は、人の編集を LLM の判断で捨てる設計になるので
採らない——S6 の原則は「人の記述 > エージェント提案」である）。

代わりに **charter 変更検知時（`charter_changed`）に、`edited: human` かつ現行 charter に
紐付かないタスクを 1 枚の棚卸し票（needs）にまとめて出す**。タスクごとに票を作らない
（大改訂なら数十件になり、票が票を埋める）。人は各行を reject（→ 墓標）するか、放置する
（→ そのまま残る）。放置が既定の挙動＝**何もしなければ何も失われない。**

`--revive` と同じく「明示操作でのみ状態が変わる」に統一する。

### 4-2. 墓標の指紋衝突

**決着: 抑止は正規化タイトルの完全一致のみ。類似は抑止せず、プランナー入力と needs 注記で提示する。**

詳細と根拠は §1.7。要点は「抑止は取り返しがつかず、提示は取り返しがつく」。
指紋に workspace を含めない理由は §1.8。

---

## 5. テスト計画

**S6-0（acceptance の受け渡し）**
1. `{"acceptance": ["A","B"]}` の enqueue → md に `- acceptance: A` / `- acceptance: B` の 2 行。
   `str(list)` の repr にならない（回帰）
2. `acceptance` のみのタスクが `proposed`（plan_review on）/ `ready`（off）に入る。inbox に落ちない
3. 計画レビュー票に acceptance が箇条書きで載る
4. `revise` で acceptance を配列置換・`'-'` で全削除。md 往復で行数が保たれる

**S6（生成とレビュー）**
5. `plan_via_agent` が `planner_skill` を解決する / 見つからないとき組み込みプロンプトへ落ちる /
   `planner_skill` で名前を差し替えられる
6. 必須セクション欠落 → 1 回再要求 → なお欠落なら `draft` + `needs_reason`（捨てない）
7. `plan_sections: warn` では欠落しても proposed に入る
8. `existing[]` に `edited: human` が載る。人が title を変えたタスクは `planned_title` で
   重複照合され再投入されない
9. `_apply_revise_fields` が変更を返すと `edited: human` が立つ / needs の `[x]` でも立つ

**S6（墓標・整合・メモ）**
10. `cmd_reject` が `tombstones.md` に 1 行足す。同一タイトルの再投入は止まる
11. **類似（Jaccard ≥ threshold）だが完全一致でないタスクは投入される**（needs に注記付き）
12. `charter=` タグ付き墓標は別 charter のタスクを止めない
13. `revive` で行が消える / `replan --revive` は行を残したまま今回だけ無視する
14. 整合パス: 既存と重複する enqueue は新規作成せず needs に追記案を出す
15. `has_consumable` がタグ無しタスクを数える（回帰: 再分解の誤発火）
16. `distill-notes` が `notes/*.md` を消費し proposed 投入 → `notes/archive/` へ移動。
    plan は notes を自動消費しない
17. `--granularity` が `agent-flow run` の argv に載る（回帰）/ `flow_planner_skill` で名前を変えられる

**S7**
18. `_assess_max` 3 → フル / 2 → ライト / 1 → スキップ
19. 旧 `spec_threshold: 3` のみの設定が full=3 として読まれる（後方互換）
20. ライト spec タスクの verify は `design.md` 1 枚。`spec.md` / `tasks.md` を要求しない
21. ライト spec が done → **展開されず** `spec_expanded: light`。元タスクが `design.md` を
    文脈に持って実行される
22. policy `spec:` 強制はフルへ行く
23. `ensure_repo_maps` が `repo_map: false` でも plan 前に走る / 生成失敗でも plan は進む

---

## 6. 実装で確定した差分

| 項目 | 実装 |
|---|---|
| **S5 の設定キーが Config に届いていなかった**（設計時に見落としていた不具合） | `verifier` / `verifier_skill` / `verify_side_effects` は `CONFIG_DEFAULTS` にあるだけで `Config` へ渡されておらず、読み出しは `getattr(cfg, …, 既定)` に落ちていた。**設定しても効かない**状態だったので、S6 の設定追加と同時に配線した（Phase 2 の積み残しではなく、素の欠落） |
| **必須項目の欠落は `draft` 一択にしなかった** | §1.5 のとおり `plan_review` に合わせて proposed / draft を選ぶ。dashboard に draft → ready の導線が無く、draft 固定だと「捨てない」が実質「見えない場所に捨てる」になる |
| **`_has_project_human_wait` も同じ穴を持っていた** | §1.9 では `has_consumable` だけを挙げたが、人待ち判定も `task_charter_name`（タグ無し = `"default"`）を `""` と比べており、**タグ無しタスクが常にスコープ外**だった。同じ述語（`task_belongs_to_charter`）に寄せて両方直した |
| **`--granularity` はグローバル引数だった** | agent-flow の `--granularity` は**サブコマンドより前**に置く必要がある。`run` の後ろに付けると `unrecognized arguments` で毎回失敗する（テストで固定した） |
| **組み込みプロンプトにも入力を載せた** | §1.4 は「組み込みは現行のハードコードプロンプトそのもの」としていたが、それだと**既存タスク・墓標・メモ・再要求が落ちる**。スキル未導入の環境では再要求が同じプロンプトの繰り返しになり（欠落が直らない）、`distill-notes` はメモを読まないまま分解していた。`_builtin_planner_extras` で同じ情報を足した |
| **`_norm_title` は区切り無しで連結** | §1.7 の当初案は「空白 1 つで連結」だったが、日本語は分かち書きの有無が書き手次第で「X をやる」と「X を やる」が別指紋になる。区切りを落とし、語順だけ保つ形にした |
| **`MULTILINE_KEYS` という総称にした** | `acceptance` 専用の分岐ではなく「複数行フィールド」の集合として `model.py` に置き、`task_from_spec` / `REVISE_FIELDS` / spec 展開 / dashboard の `REVISE_LIST_KEYS` がそれを参照する。2 つ目が出たときに 4 か所を探し直さずに済む |
| **`revise` は行の集合を丸ごと置換** | 行単位の差分編集にすると「何行目を消すか」を UI と本体で二重に数えることになる。UI は常に全行を送り、本体は全行を差し替える |
| **`distill-notes` の指示は失敗しても `.err` に落とさない** | メモを書く前に押しただけで人が消す残骸が積む。`heal` と同じ判断で、受理レシートを書いて指示ファイルは消す |

**実績**: agent-project 1044 件 / agent-flow 564 件 / agent-dashboard 全スイート green。

## 7. 積み残し（Phase 4 以降へ）

1. **charter acceptance の LLM 一発合成**（Phase 2 積み残し P2-d）— `resolve_charter_acceptance` は
   今も自然文 → コマンドの合成に依存する。S5 のコンセプトを charter（マイルストーン収束判定）へ
   広げる設計は本設計の範囲外。**ただし S6 で「基準を書くのはエージェント、直すのは人」の
   経路ができるので、charter acceptance も同じ形（基準リスト + 証跡）に寄せる下地は揃う**
2. **md 直接編集の検出**（§1.6）— 内容署名を持てば可能だが維持コストに見合わない。
   `planned_title` で実害は塞いだ
3. **墓標の自動失効** — 古い墓標が残り続けることの害（新しい文脈では要るタスクが恒久抑止される）は
   完全一致に限った時点で小さいが、ゼロではない。日付は行に持たせてあるので、必要になったら
   `revive` の一括操作を足せる
4. **dashboard の notes UI の同期** — `notes/` は状態リポジトリ配下なので state 同期で全 PC へ届くが、
   同時編集の衝突解決は state 同期の既存規則（リモート優先ファイル指定）に委ねる。
   メモの粒度で衝突が問題になったら、ファイル名にノード id を入れる
5. **`size` の活用** — 分解の妥当性判断用に出させるが、S6 では**表示するだけ**。
   「L ばかりなら granularity を上げて再分解」のような自動調整は入れない（自動で計画を作り直す
   経路を増やすと、人が直した計画が動く理由が増える）
