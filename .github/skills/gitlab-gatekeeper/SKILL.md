---
name: gitlab-gatekeeper
description: GitLab の MR/イシューのマージ承認「門番」スキル。packet モード=人間が1枚で決めるマージ判断パケットを生成（紐づく MR に未対応レビューコメントがあれば needs-review へ差し戻して終了）。decision モード=人間の承認/否認を執行（承認はマージ＋イシュークローズ、否認や不可は needs-review へ戻し差し戻しコメントを生成）。「レビューパケットを作って」「マージ判断パケット」「この MR を1枚に」「#42 を承認/否認して」で発動。
metadata:
  version: 3.0.0
  tier: experimental
  category: review
  tags:
    - review
    - gitlab
    - human-in-the-loop
    - gatekeeper
    - documentation
---

# gitlab-gatekeeper — マージ承認の門番

GitLab の変更を、**人間レビュアーが 1 枚見て承認/差し戻しを決められる成果物**に変え、
さらに**人間が下した承認/否認を GitLab へ確実に執行する**門番スキル。散らばった情報
（イシュー本文・コメント・差分・CI・受け入れ条件）を読みに行くレビューを、
**キュレーション済みの結論を確認する**レビューに変える。

> マージの最終**判断**は人間が下す。このスキルは判断を代行しない（「承認すべき」と書かない）。
> 人間が明示した決定（承認/否認）だけを受けて、ラベル遷移・マージ・差し戻しを**執行**する。

## モード

| モード | 起動 | 役割 |
|---|---|---|
| **packet**（既定） | 「レビューパケットを作って」「マージ判断パケット」 | 判断材料を 1 枚に集約。**ただし未対応レビューコメントがあれば差し戻して終了**（§Gate A） |
| **decision** | 「#42 を承認して」「#42 を否認、理由は〜」 | 人間の承認/否認を執行（マージ＋クローズ／差し戻し＋コメント生成）（§decision モード） |

どちらのモードか曖昧なら、対象 IID と「パケット生成か・承認/否認の執行か」を 1 問だけ確認する。

## 設計の前提（重要）

このスキルは**部品**であり、運用ポリシーを内蔵しない。次は **呼び出し側が決める**:

- **対象の限定（ステータス/ラベル/担当）** — スキルは選別ラベルをハードコードしない。
  呼び出し側がセレクタ（gl.py の `--label` / `--state` / `--assignee`、または明示の IID 群）を渡す。
- **間隔・スケジューリング** — このスキルはポーリングもループもしない。定期実行は呼び出し側
  （`/loop`・cron・kiro-loop 等）の責務。スキルは「いま渡された対象を 1 回処理する」だけ。
- **出力先**（packet 時） — 成果物をどこへ置くか（Obsidian Vault のパス / `.html` ファイル / Markdown / stdout）。
- **ラベル/マージのポリシー** — 下表の既定を持つが、呼び出し側が上書きできる。

| ポリシー | 既定 | 用途 |
|---|---|---|
| `needs_review_label` | `status:needs-review` | 差し戻し先のステータス（Gate A・否認・マージ不可で付与） |
| `ready_labels` | `status:review-ready` | 差し戻し時に外す「レビュー待ち/承認待ち」ステータス（複数可・カンマ区切り） |
| `merge.squash` / `merge.remove_source_branch` | 共に false | 承認マージ時のオプション |
| `require_ci_success` | true | 承認マージの前提として CI 成功を要求するか |

呼び出し側がこれらを指定しない場合は上の既定を使い、破壊的操作（マージ・クローズ）の直前に対象を 1 行で要約してから実行する。

## パス解決

- このSKILL.mdのディレクトリを `SKILL_DIR` とする。
- GitLab 操作は `gitlab-idd` スキルの **`scripts/gl.py`** を再利用する（接続情報・トークンも共有）。
  パスは `find` で `.github/skills/gitlab-idd/scripts/gl.py` を探す。`GITLAB_TOKEN` が必要。
  以降 `$GL` は **gl.py を含むディレクトリ**（`.github/skills/gitlab-idd/scripts`）を指し、`python3 $GL/gl.py …` で呼ぶ。
- レビュー観点の詳細は **`agent-reviewer` スキルの `references/`** を再利用する（重複定義しない）。
- 出力の整形は、インストール済みなら下表のドキュメント系スキルに委譲する（自前で凝った整形を書かない）。

### 共通ヘルパ：紐づく MR の解決

イシュー `<iid>` に紐づく MR は **ブランチ命名規約**で引く（`gitlab-idd` の `make-branch-name` と同じ前提）:

```bash
python3 $GL/gl.py list-mrs --source-branch-prefix feature/issue-<iid> --state opened
```

- 0 件 → MR 未作成。packet では「MR なし」と明示、decision では人へ確認（執行できない）。
- 複数件 → opened を優先し、なお複数なら呼び出し側に対象 MR を確認（独断で選ばない）。

### 共通ヘルパ：未対応レビューコメントの判定

```bash
python3 $GL/gl.py get-mr-discussions <mr_iid> --unresolved
```

返ってきた**解決可能（resolvable）かつ未解決（unresolved）のスレッドが 1 件以上**あれば「未対応レビューコメントあり」。
ラベル変更等の system note は discussion ではないため対象外（このコマンドは discussion スレッドのみ返す）。

### 共通ヘルパ：差し戻し（needs-review へ戻す）

```bash
python3 $GL/gl.py update-issue <iid> \
  --add-labels "$needs_review_label" --remove-labels "$ready_labels"
python3 $GL/gl.py add-comment <iid> --body-file <生成したコメント.md>
```

---

## packet モードの実行プロトコル

### Step 1 — SELECT（呼び出し側のセレクタで対象を取得。status は絞らない）

呼び出し側が渡したセレクタをそのまま gl.py に流す。例:

```bash
# 例: 「label=ready の opened イシュー」
python3 $GL/gl.py list-issues --label ready --state opened
# 例: 明示の IID 群が渡されたら list は飛ばしてそれを使う
```

セレクタが無ければ **絞らずに opened を対象**とし、件数が多ければ呼び出し側に範囲を確認する。
スキル側の独断でラベル/ステータスを足したり引いたりしない。

### Step 2 — Gate A（未対応レビューコメントの差し戻し）★必須・パケット生成より先

各対象イシューについて、判断材料を集める**前に**次を実行する:

1. 共通ヘルパで紐づく MR を解決する。MR が無ければ Gate A はスキップ（Step 3 へ）。
2. 共通ヘルパで未対応レビューコメントを判定する。
3. **未対応コメントが 1 件以上ある場合**:
   - 共通ヘルパで `needs_review_label` へ差し戻す（`ready_labels` を外す）。
   - 差し戻しコメントを生成・投稿する。本文は「未対応のレビューコメントがあるため `needs-review` に戻しました」
     と明記し、**未対応スレッドの要点を箇条書き**（各スレッドの該当ファイル:行・コメント要旨）で列挙する。
     憶測を足さず、スレッド本文に書かれた指摘だけを写す。
   - この対象は**これで終了**（パケットは作らない）。複数対象なら次の対象へ進み、最後に
     「差し戻し N 件 / パケット生成 M 件」を要約する。
4. 未対応コメントが無ければ Step 3 へ進む。

> Gate A の意図: 未対応のレビュー指摘が残っている変更にマージ判断パケットを作るのは無駄であり、
> 人間に「もう見て良い」と誤信させる。門番として、まず実装者（ワーカー）へ突き返す。

### Step 3 — GATHER（1 対象ぶんの判断材料を集める）

各対象について gl.py と git で次を集める（無いものは「なし」と明示、捏造しない）:

| 材料 | 取得 |
|---|---|
| イシュー本文・ラベル・受け入れ条件 | `get-issue` / 本文から受け入れ条件（チェックリスト）を抽出 |
| 議論の経緯 | `get-comments` / `get-mr-discussions <mr> --unresolved` |
| 紐づく MR | 共通ヘルパ |
| 差分 | `git fetch` 後 `git diff origin/<target>...origin/<source>`（クローン済みリポジトリ）。無ければ GitLab changes API |
| CI 結果 | `get-mr-pipeline <mr_iid>` |

差分は**自分でファイルパス・内容からリスク分類**する（high/medium/low）。
例: 認証・DB・API・入力処理・権限・秘密情報・データ移行・後方互換に触れるものは high 寄り。

### Step 4 — REVIEW（agent-reviewer の観点で評価）

`agent-reviewer` の観点選択に従い perspective を選ぶ。変更が大きい/重要なら `agent-reviewer` を
**サブエージェントとして起動**し、その集約結果をこのスキルのパケット形式に**圧縮**する。
小さな変更は本インスタンスで直接評価してよい。

| 対象 | 観点 |
|---|---|
| high リスク差分（認証/DB/API/入力処理） | functional, ai-antipattern, security |
| 一般プロダクションコード | functional, ai-antipattern, architecture |
| テスト中心 | test |
| ドキュメント・仕様 | document |

詳細手順は `agent-reviewer/references/<perspective>.md` を参照。

### Step 5 — RENDER（出力先に合わせ、見やすさに全振り）

**パケットの中身は固定**（下記「パケットの構成」）。**整形と出力先は呼び出し側指定に従い、
インストール済みのドキュメント系スキルへ委譲**する。スキルの存在は `.github/skills/<name>/SKILL.md`
の有無で判定する。

| 出力先 / 要求 | 委譲先スキル | 成果物 |
|---|---|---|
| Obsidian Vault のパス | **obsidian-use** | 1 枚ノート（callout＋ウィキリンク、必要なら Canvas で関係を図示） |
| `.html` ／「HTML で」 | **spec-to-readable-html** | 要約・図表・ソーストレーサビリティ付きの読みやすい HTML |
| 図解が要る関係（依存・フロー・状態） | **mermaid-diagrammer** | 上記成果物に Mermaid 図を埋め込み |
| README/ガイド調の長文 | **technical-writer** | 整形済みドキュメント |
| 委譲先が未インストール / stdout 指定 | （フォールバック）素の Markdown | callout 構成の Markdown をそのまま出力先へ |

委譲時は「このパケット本文を、人間が 1 枚で読み切れるよう整形して <出力先> に書き出して」と
明示的に指示する。**HTML を選ぶ場合は折りたたみ（details）・色付き callout・目次で段階開示**を効かせる。

パケットには、人間が後で decision モードを呼べるよう**フロントマターに `issue` / `mr` / `decision:` 欄を空で用意**する
（人間が `approve|reject` を記入 → decision モードがそれを読む）。

---

## decision モードの実行プロトコル（人間の承認/否認の執行）

**入力**: 対象イシュー `<iid>`、`decision`（`approve` | `reject`）、reject 時は**ユーザーの自然文コメント**（差し戻し理由）。
これらは呼び出し時の引数、または packet 成果物のフロントマター（`decision:` / `confirmed_by:`）から読む。
**人間の明示決定が無い限り、このモードは何も実行しない**（憶測で approve/reject しない）。

### approve（承認 → マージ＋クローズ）

1. 共通ヘルパで紐づく MR を解決（無ければ執行不能 → 人へ確認して終了）。
2. **マージ可否を事前確認**（ここで弾けるものは API を叩く前に弾く）:
   - 未対応レビューコメント（共通ヘルパ）が**無い**こと。
   - MR がドラフトでない・コンフリクトが無いこと（`list-mrs` の MR オブジェクトの
     `detailed_merge_status` / `merge_status` を見る。`mergeable`/`can_be_merged` 以外は不可寄り）。
   - `require_ci_success` が true なら `get-mr-pipeline <mr>` が `success` であること。
3. **可**なら `merge-mr <mr_iid>`（`merge.squash` / `merge.remove_source_branch` をポリシーに従い付与）。
   - マージ成功 → `update-issue <iid> --state-event close` でイシューをクローズし、
     `add-comment <iid>` に「承認により !<mr_iid> をマージし、本イシューをクローズしました（承認者: <confirmed_by>）」を残す。
4. **不可**（Step 2 で弾けた、または `merge-mr` が非 2xx で失敗した）→ **マージしない**。
   - 共通ヘルパで `needs_review_label` へ差し戻す。
   - `add-comment <iid>` に「承認を受けたがマージできなかったため `needs-review` に戻しました」と、
     **不可理由を具体的に**（例: コンフリクト / CI 失敗 / ドラフト / 未対応レビューコメント / API エラー本文）記す。
   - 実装者が次に何をすればよいか（リベース・CI 修正・コメント対応）を 1〜3 点で示す。

> `merge-mr` はレース等で実行時にも失敗しうる。**非 2xx は必ず「不可」として差し戻し経路へ**回し、
> 「マージした」と誤って報告しない。

### reject（否認 → 差し戻し＋コメント生成）

1. 共通ヘルパで `needs_review_label` へ差し戻す（`ready_labels` を外す）。MR があれば任意で MR にもミラー（`add-mr-comment`）。
2. **ユーザーの自然文コメントを解釈**し、実装者（ワーカー）が直せる**差し戻しコメント**を生成して `add-comment <iid>` で投稿する。
   生成コメントの要件:
   - ユーザーの指摘を**具体的・実行可能**な是正項目に翻訳する（「ここを直す → 受け入れ条件/該当ファイルとの対応」）。
   - ユーザーが触れていない要求を**足さない**（門番が新しい仕様を作らない）。出典はユーザーコメント。
   - 該当箇所が分かるなら `path:line` や受け入れ条件番号を添える。
   - 末尾に「対応後、`ready_labels` を付け直して再申請してください」と再入場の導線を 1 行で示す。
3. ユーザーコメントが**空/曖昧で是正項目に翻訳できない**場合は、差し戻しを実行する前に
   「何を直せばよいか」を 1 問だけ確認する（憶測で差し戻しコメントを捏造しない）。

---

## パケットの構成（packet モードの出力の中身）

どの出力先でも、人間が**上から読むほど深くなる**よう段階開示する。最低限この 4 ブロック:

```markdown
> [!danger]+ 🔴 必ず確認すべき3点（人間が目視すべき急所）
> 1. <ファイル:行> <なぜ危険か。1 行で>
> 2. ...
> 3. ...

> [!question]- 🤔 受け入れ条件の達成評価（トレーサビリティ）
> - <条件text> → ✅根拠 `path:line` / ⚠️未達・証跡なし の別を明示
>   （diff・テスト・コメントから証跡を示す。憶測は「証跡なし」と書く。黙って✅にしない）

> [!info]- 🧪 観点別レビュー所見（信頼度つき）
> - functional: <所見> (信頼度 0.0–1.0)
> - security: ...
> - （対象に応じ agent-reviewer の観点を選ぶ。所見ゼロの観点は省略可）

> [!example]- 📂 リスク段階開示（自動チェック＋差分マップ）
> - CI: <status>  /  lint・SAST: <あれば>
> - high: <path> (+adds/-dels) … medium/low は畳む
```

### 信頼度の付与
各所見に **0.0–1.0 の信頼度**を付ける。人間の精読配分を決めるシグナル。
- 確証がある指摘のみ高信頼度。**自信のない指摘を高信頼度で出さない**（過検出は人間の時間を奪う）。
- 全体が「低リスク × 高信頼度 × 受け入れ条件充足」なら末尾に
  `> [!tip] 🟢 軽量承認候補: 急所に問題なし。スポットチェックで承認可と思われる（最終判断は人間）`。

---

## やってはいけないこと

- ❌ ステータス/ラベルをスキル側で**選別**条件として勝手に限定・追加する（対象選別は呼び出し側の責務）。
  ※ Gate A・否認・マージ不可での `needs-review` への差し戻しは、人間の決定/規約に基づく**執行**であり別。
- ❌ ポーリング・sleep・定期ループをスキル内で回す（間隔は呼び出し側の責務）。
- ❌ 人間の明示決定が無いのに承認/否認・マージ・クローズする（判断は人間、執行のみ）。
- ❌ `merge-mr` が失敗したのに「マージした」と報告する（非 2xx は必ず差し戻し経路へ）。
- ❌ 否認の差し戻しコメントに、ユーザーが述べていない要求を足す（門番が仕様を作らない）。
- ❌ 自信のない指摘を断定・高信頼度で出す（過検出は害）。
- ❌ raw データに無い事実を捏造する（特に受け入れ条件の達成判定）。

## 関連スキル

- `gitlab-idd` — `gl.py` の提供元（接続・トークン・MR/イシュー操作・`make-branch-name`）。
- `agent-reviewer` — 観点別レビューの本体（本スキルが再利用）。
- `obsidian-use` / `spec-to-readable-html` / `mermaid-diagrammer` / `technical-writer` — 出力整形の委譲先。
- `self-checking` — ワーカーが事前自己評価し、ここに来る前に指摘を減らす。
