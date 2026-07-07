# kiro-flow — gitlab コメントの捕捉・emit と分解への還元 設計案

> 対象ツール: `tools/kiro-flow/`（`executors/gitlab.py` / `.github/skills/flow-planner/`）
> 対になる文書: [`kiro-projects-feedback-reduction-design.md`](./kiro-projects-feedback-reduction-design.md)
> （学習ストア＝統一フィードバックバスの本体・verify 合成の品質は kiro-projects 側が担う）
> ステータス: Draft（設計案。実装は未着手）

## 0. 位置づけ

「ユーザーの決定・指摘を全体へ還元する」仕組みは 2 ツールにまたがる。本書は **kiro-flow 側＝
フィードバックの発生源（gitlab イシューの人コメント）を捕捉して構造化 emit する層**と、
**分解（flow-planner）を再考させる受け口**を扱う。集約先の学習ストア（`decisions/`＋ltm）・
verify 合成の品質・recall の適用は kiro-projects 側の同名文書に置く。

```
[gitlab イシューの人コメント]                     本書の担当
   却下 / 承認 / 作業中コメント ──emit(§1)──▶ result.data.{guidance,notes}
                                                     │
                              （kiro-projects が蒸留し learn/verify ストアへ）
                                                     │ recall
   flow-planner ◀──§2 `--learnings`──────────────────┘
   （分解グラフ自体を過去の指摘で変える）
```

## 1. 問題 — 人コメントの捕捉が「却下時のみ」

現状 `executors/gitlab.py` が人コメントを読むのは **決着時だけ**で、しかも実質**却下**に偏っている。

- `_human_comments()`（`443-459`）: `kiro-flow:` で始まる自動コメントとシステムノートを除いた人コメントを
  新しい順に連結（2000 字上限）。
- `_rejected_payload()`（`861-886`）: **却下時**にこれを `data.guidance` と
  `[gitlab-reject] … やり直し指示: {guidance}` に載せる。
- 承認時（`status:approved` 到達）は `data` に人コメントを載せていない。
- **作業中（open のまま決着していない）イシューに投稿された通常コメントは、決着を伴わない限り一切拾われない。**

結果、ユーザーが「却下」という強い操作をせずに残した**通常の指摘・方向づけコメント**は捨てられる。
これがユーザー指摘「個々のイシューに投稿したコメントが同様のタスクに活きない」の**発生源側の欠落**である。

## 2. 設計 — すべての人コメントを構造化 emit する（却下以外も含む）

**却下・承認・作業中のすべての人コメントを捕捉対象にし**、蒸留（kiro-projects §2）へ渡せる形で emit する。

### 2.1 決着時 emit の対称化（却下・承認の両方）

- `_rejected_payload` に加え **`_approved_payload`（新設/拡張）でも `_human_comments` を `data.notes` に載せる**。
  却下＝`avoid` 寄り、承認＝`learn` 寄りの素材として下流（kiro-projects）が振り分ける。
- どちらも生コメントを verbatim で載せ、**一般化（蒸留）は下流に一任**する（executor は「読む・運ぶ」だけ＝
  現状の責務分離を保つ。executor に知能を入れない）。

### 2.2 作業中コメントの逐次 emit（park & poll に相乗り）

- park & poll の監視主体は `watch_interval`（既定 90 秒）毎にイシューを再確認している
  （`README` §park & poll）。この**既存ポーリングに相乗り**して、前回確認以降に増えた**新規の人コメント**を
  `data.notes`（増分）として run のバスに書き出す。GitLab への追加 API は `get-comments` 1 本で、
  多重ポーリングは監視 1 本のバッチに畳まれる既存設計をそのまま使う（負荷を増やさない）。
- **重複防止**: コメントは `note.id`（GitLab のノート ID）でキー付けし、既出は再 emit しない。決着時の
  guidance/notes とも id で重複排除する（同じコメントを二度 learn 化しない）。
- **ノイズ抑制**: 逐次 emit は「決着していない作業中の議論」を含みうるため、蒸留の段で
  「durable な指示か（一過性の相談でないか）」を判定して落とす（判定は kiro-projects §2 の蒸留に寄せる。
  executor は素材を運ぶだけ）。

### 2.3 emit する構造化データ（executor 契約の拡張）

| フィールド | いつ | 中身 |
|-----------|------|------|
| `data.guidance` | 却下決着（既存） | 却下時の人コメント（verbatim） |
| `data.notes` | 承認決着（新） / 作業中増分（新） | 承認コメント・作業中コメント（`[{id, body, ts, author_is_human}]`） |
| `data.decision` | 決着時（既存） | `approved` / `rejected` |

`notes` を**成功 result にも載せる**のが要点。従来 done の result は人コメントを運ばなかったため、
承認されたイシューに書かれた良い指摘（正例）が全部捨てられていた。

## 3. 設計 — 分解（flow-planner）を過去の指摘で変える

蒸留された learn/avoid を、**分解グラフの生成そのもの**に効かせる（＝「タスク分解を再考する」到達）。

- kiro-projects が recall した learn/avoid を、要求本文とは別の **`--learnings`（構造化・有界）** channel で
  flow-planner（`.github/skills/flow-planner/`）へ伝搬する。要求本文に畳み込むと分解後の各ノードに
  薄まって届くため、**planner の戦略選定・粒度・verify gate 挿入の判断材料として独立に渡す**。
- flow-planner はこれを「過去に同種の要求がこう却下された／こう直された」という制約として読み、
  例: 「この種は 1 段細かく割れ（granularity 上げ）」「集約前に verify gate を必ず挟め」「この分割は避けよ」を
  分解に反映する。3 段パイプライン（要求分析→戦略選定→グラフ生成）の**戦略選定**段に注入するのが自然。
- **有界化**: 件数・文字数上限を設け（planner を振り回さない）、マッチは kiro-projects の Jaccard recall に一任。

> flow-planner カタログ（`patterns-catalog.yaml`）に、learnings を受けたときの戦略調整例
> （granularity / 追加 verify gate / 分割回避）を variants として追記する。

## 4. スコープ外（別途判断）

- **kiro-flow の `verify` ノード（LLM 判定）の CLI 化**: kiro-flow 内側の `verify` は
  `execute_kiro(kind="verify")` の LLM 判定で、CLI 検証は存在しない。これを CLI 化するかは本案の範囲外
  （本案が対象にする「不確実性をなくす verify」は kiro-projects 側の verify＝終了コードゲート）。将来課題。

## 5. 影響ファイル（kiro-flow 側）

| ファイル | 箇所 | 変更 |
|----------|------|------|
| `tools/kiro-flow/executors/gitlab.py` | `_rejected_payload` 861-886 / 承認 payload / park&poll 監視 | §2 承認・作業中コメントの emit、id 重複排除 |
| 〃 | `_human_comments` 443-459 / `_get_comments` 407-435 | §2.2 増分抽出（前回 id 以降） |
| `.github/skills/flow-planner/` | 戦略選定段 / `patterns-catalog.yaml` | §3 `--learnings` 受け口・戦略調整 variants |

## 6. kiro-projects との境界

- executor は**運ぶだけ**（生コメント＋id）。蒸留・learn 化・recall・verify 化・cohort 還流・昇格ラダーは
  **すべて kiro-projects 側**（同名文書 §2〜§4）。
- 逆向き（kiro-projects → flow-planner）の `--learnings` は kiro-projects が recall して渡す（本書 §3 は受け口の定義）。
