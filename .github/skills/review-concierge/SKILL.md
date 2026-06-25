---
name: review-concierge
description: gitlab-idd が status:review-ready まで進めた変更を、人間レビュアーが「1 枚見て決める」ためのマージ判断パケット(Obsidian ノート本文)に変換する。review-concierge デーモンの review_command から raw バンドル(JSON)を受け取り、agent-reviewer の観点で必読 3 点・受け入れ条件評価・リスク所見を生成する。「レビューパケットを作って」「マージ判断パケット」「review-concierge の本文を生成して」で発動。
metadata:
  version: 1.0.0
  tier: experimental
  category: review
  tags:
    - review
    - gitlab
    - obsidian
    - human-in-the-loop
---

# review-concierge — マージ判断パケット生成

`tools/review-concierge`（非 LLM デーモン）が検知した review-ready 変更について、
**人間レビュアーが 1 枚見て承認/差し戻しを決められる Obsidian ノート本文**を生成する薄いスキル。

このスキルは「散らばった情報を読む」レビューを「キュレーション済みの結論を確認する」レビューに変える。
**情報を集める処理はデーモンが済ませてある**。あなたの仕事は集約済みデータから *判断材料* を絞ることだ。

> マージの最終決定とマージ操作は **人間** が行う。このスキルは判断を代行しない。
> 「これは承認すべき」と書かず、「承認の可否を人間が判断するための材料」を提示する。

## パス解決

このSKILL.mdのディレクトリを `SKILL_DIR` とする。レビュー観点の詳細は
**`agent-reviewer` スキルの `references/`** を再利用する（重複定義しない）。

---

## 入力（stdin: raw バンドル JSON）

`review_command` には次の構造の JSON が stdin で渡される:

```json
{
  "issue": {"iid", "title", "description", "web_url", "labels", "author", "updated_at"},
  "comments": [{"author", "body"}],
  "mr": {"iid", "title", "web_url", "source_branch", "target_branch"} | null,
  "diff": {
    "files": [{"path", "risk": "high|medium|low", "adds", "dels", "diff"}],
    "adds", "dels", "overall_risk", "n_files"
  },
  "pipeline": {"status", "web_url"},
  "acceptance": [{"done": bool, "text"}],
  "config": {"ready_label"}
}
```

`diff.files` は既に **デーモンがパス・ヒューリスティックでリスク分類済み**。
あなたは差分の中身を読んで、その分類を **是正・深掘り**する（例: テストファイルでも危険な fixture があれば格上げ）。

## 出力（stdout: ノート本文＝callout 群のみ）

frontmatter や受け入れ条件・差分一覧・自動チェックの callout は **デーモンが付与する**。
あなたが返すのは **AI レビュー部分の本文だけ**（ヘッダ callout の直後に挿入される）。
Markdown の callout 構文で、次の 3 ブロックを必ず含めること:

```markdown
> [!danger]+ 🔴 必ず確認すべき3点（人間が目視すべき急所）
> 1. <ファイル:行> <なぜ危険か。1 行で>
> 2. ...
> 3. ...

> [!question]- 🤔 受け入れ条件の達成評価
> - <条件text> → ✅根拠 `path:line` / ⚠️未達・証跡なし の別を明示
> （diff・テスト・コメントから traceability を示す。憶測は「証跡なし」と書く）

> [!info]- 🧪 観点別レビュー所見（信頼度つき）
> - functional: <所見> (信頼度 0.0–1.0)
> - security: ...
> - （対象に応じ agent-reviewer の観点を選ぶ。所見ゼロの観点は省略可）
```

### 信頼度の付与（重要）
各所見に **0.0–1.0 の信頼度** を付ける。これは人間の精読配分を決めるシグナル。
- 確証がある指摘のみ高信頼度。**自信のない指摘を高信頼度で出さない**（過検出は人間の時間を奪う）。
- 全体として「低リスク × 高信頼度 × 受け入れ条件充足」なら、本文末尾に
  `> [!tip] 🟢 軽量承認候補: 急所に問題なし。スポットチェックで承認可と思われる（最終判断は人間）` を添える。

---

## 実行プロトコル

### Step 1: 観点の選択（agent-reviewer に委譲）

`diff.files` のパスと内容から対象種別を判定し、`agent-reviewer` の観点選択表に従って
perspectives を選ぶ。変更が大きい/重要な場合は `agent-reviewer` をサブエージェントとして起動し、
その集約結果を本スキルの出力形式に **圧縮**する。小さな変更は本インスタンスで直接評価してよい。

| 対象 | 観点 |
|---|---|
| 認証・DB・API・入力処理（diff に high リスク） | functional, ai-antipattern, security |
| 一般プロダクションコード | functional, ai-antipattern, architecture |
| テスト中心 | test |
| ドキュメント・仕様 | document |

詳細手順は `agent-reviewer/references/<perspective>.md` を参照。

### Step 2: 急所の抽出（必読 3 点）

全所見の中から **人間が必ず自分の目で確認すべき 3 点**だけに絞る。基準:
- 自動チェック（CI/lint/SAST）で捕まらない、人間の判断が要る論点を優先。
- high リスクファイル、受け入れ条件の未達、後方互換・データ移行・権限・秘密情報の扱い。
- 「読めば分かる些末」は必読に入れない（畳まれた差分側に委ねる）。

### Step 3: 受け入れ条件のトレーサビリティ

`acceptance` の各条件について、それを満たす **根拠（diff の該当箇所・テスト・実行ログ）** を対応づける。
根拠が見つからないものは "⚠️ 証跡なし" と明示する（黙って ✅ にしない）。

### Step 4: 出力

上記 3 ブロックを Markdown callout で stdout に出力する。**それ以外の説明文・前置きは出力しない**
（出力はそのままノートに挿入されるため）。

---

## やってはいけないこと

- ❌ 「承認します」「マージしてよい」と決定を下す（判断は人間）。
- ❌ 自信のない指摘を断定・高信頼度で出す（過検出は害）。
- ❌ frontmatter や受け入れ条件一覧・差分一覧を自分で出力する（デーモンが付与する。二重化禁止）。
- ❌ raw バンドルに無い事実を捏造する（特に受け入れ条件の達成判定）。

## 関連スキル

- `agent-reviewer` — 観点別レビューの本体（本スキルが再利用）。
- `gitlab-idd` — review-ready ラベルを付ける自律ワークフロー（無改修で連携）。
- `self-checking` — ワーカーが事前自己評価し、ここに来る前に指摘を減らす。
