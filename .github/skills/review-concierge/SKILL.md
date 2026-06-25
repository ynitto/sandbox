---
name: review-concierge
description: GitLab の変更（MR/イシュー）を、人間レビュアーが「1 枚見て承認/差し戻しを決める」ためのマージ判断パケットに変換する。どのステータス・ラベルを対象にするか、どこへ出力するか、どの間隔で回すかは**すべて呼び出し側が指定**する。スキル自身はステータスを限定せず、間隔も持たない。出力先（Obsidian Vault / HTML ファイル / Markdown / stdout）に応じて、インストール済みのドキュメント系スキル（obsidian-use・spec-to-readable-html・mermaid-diagrammer 等）を使い、人間の認知を最大限助ける見やすい成果物を作る。「レビューパケットを作って」「マージ判断パケット」「このMRを1枚にまとめて」「review-concierge で出して」で発動。
metadata:
  version: 2.0.0
  tier: experimental
  category: review
  tags:
    - review
    - gitlab
    - human-in-the-loop
    - documentation
---

# review-concierge — マージ判断パケット生成

GitLab の変更を、**人間レビュアーが 1 枚見て承認/差し戻しを決められる成果物**に変える
エージェント駆動スキル。散らばった情報（イシュー本文・コメント・差分・CI・受け入れ条件）を
読みに行くレビューを、**キュレーション済みの結論を確認する**レビューに変える。

> マージの最終決定とマージ操作は **人間** が行う。このスキルは判断を代行しない。
> 「承認すべき」と書かず、「人間が承認の可否を判断するための材料」を提示する。

## 設計の前提（重要）

このスキルは**部品**であり、運用ポリシーを内蔵しない。次は **呼び出し側が決める**:

- **対象の限定（ステータス/ラベル/担当）** — スキルは `status:review-ready` 等を一切ハードコードしない。
  呼び出し側がセレクタ（gl.py の `--label` / `--state` / `--assignee`、または明示の IID 群）を渡す。
- **間隔・スケジューリング** — このスキルはポーリングもループもしない。定期実行は呼び出し側
  （`/loop`・cron・kiro-loop 等）の責務。スキルは「いま渡された対象を 1 回処理する」だけ。
- **出力先** — 成果物をどこへ置くか（Obsidian Vault のパス / `.html` ファイル / Markdown / stdout）。

呼び出し側がこれらを指定しない場合は、対象・出力先を 1 問だけ確認してから走る（憶測で status を絞らない）。

## パス解決

- このSKILL.mdのディレクトリを `SKILL_DIR` とする。
- GitLab 操作は `gitlab-idd` スキルの **`scripts/gl.py`** を再利用する（接続情報・トークンも共有）。
  パスは `find` で `.github/skills/gitlab-idd/scripts/gl.py` を探す。`GITLAB_TOKEN` が必要。
- レビュー観点の詳細は **`agent-reviewer` スキルの `references/`** を再利用する（重複定義しない）。
- 出力の整形は、インストール済みなら下表のドキュメント系スキルに委譲する（自前で凝った整形を書かない）。

---

## 実行プロトコル

### Step 1 — SELECT（呼び出し側のセレクタで対象を取得。status は絞らない）

呼び出し側が渡したセレクタをそのまま gl.py に流す。例:

```bash
# 例: 呼び出し側が「label=ready の opened イシュー」と指定した場合
python3 $GL/gl.py list-issues --label ready --state opened
# 例: 「opened な MR 全部」
python3 $GL/gl.py list-mrs --state opened
# 例: 明示の IID 群が渡されたら list は飛ばしてそれを使う
```

セレクタが無ければ **絞らずに opened を対象**とし、件数が多ければ呼び出し側に範囲を確認する。
スキル側の独断でラベル/ステータスを足したり引いたりしない。

### Step 2 — GATHER（1 対象ぶんの判断材料を集める）

各対象について gl.py と git で次を集める（無いものは「なし」と明示、捏造しない）:

| 材料 | 取得 |
|---|---|
| イシュー本文・ラベル・受け入れ条件 | `get-issue` / 本文から受け入れ条件（チェックリスト）を抽出 |
| 議論の経緯 | `get-comments` / `get-mr-discussions --unresolved` |
| 紐づく MR | `list-mrs --source-branch-prefix feature/issue-<iid>` |
| 差分 | `git fetch` 後 `git diff origin/<target>...origin/<source>`（クローン済みリポジトリ）。無ければ GitLab changes API |
| CI 結果 | `get-mr-pipeline <mr_iid>` |

差分は**自分でファイルパス・内容からリスク分類**する（high/medium/low）。
例: 認証・DB・API・入力処理・権限・秘密情報・データ移行・後方互換に触れるものは high 寄り。

### Step 3 — REVIEW（agent-reviewer の観点で評価）

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

### Step 4 — RENDER（出力先に合わせ、見やすさに全振り）

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

### Step 5 — WRITEBACK（任意・呼び出し側がモード指定した時のみ）

人間が成果物に**明示的に記入した決定**（例: フロントマターの `decision: approve|reject` と
`confirmed_by: <名前>`）を読み、gl.py で GitLab へ反映する。**人間の記入が無い限り何もしない。**

- `approve` → `update-issue --add-labels <承認ラベル> --remove-labels <レビューラベル>` ＋ `add-comment`。
  呼び出し側が「承認でマージまで」を許可している場合のみ `merge-mr <mr_iid>`。
- `reject` → `add-mr-comment` で理由を残し、必要なら `update-issue --state-event reopen`。

承認ラベル・マージ可否は**呼び出し側が渡すポリシー**に従う。スキルが勝手にマージしない。

---

## パケットの構成（出力の中身）

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

- ❌ ステータス/ラベルをスキル側で勝手に限定・追加する（呼び出し側の責務）。
- ❌ ポーリング・sleep・定期ループをスキル内で回す（間隔は呼び出し側の責務）。
- ❌ 「承認します」「マージしてよい」と決定を下す（判断は人間）。
- ❌ 自信のない指摘を断定・高信頼度で出す（過検出は害）。
- ❌ raw データに無い事実を捏造する（特に受け入れ条件の達成判定）。
- ❌ 人間の明示記入が無いのに writeback／マージする。

## 関連スキル

- `gitlab-idd` — `gl.py` の提供元（接続・トークン・MR/イシュー操作）。
- `agent-reviewer` — 観点別レビューの本体（本スキルが再利用）。
- `obsidian-use` / `spec-to-readable-html` / `mermaid-diagrammer` / `technical-writer` — 出力整形の委譲先。
- `self-checking` — ワーカーが事前自己評価し、ここに来る前に指摘を減らす。
