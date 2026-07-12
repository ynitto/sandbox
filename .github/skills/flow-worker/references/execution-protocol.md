# 実行プロトコル — worker / verify / evaluator の規律

flow-worker が kiro-flow の各 LLM 呼び出しへ織り込む規律の解説。
gitlab-idd スキルの手順（worker-role.md / requester-review.md /
non-requester-review.md / project-dod.md）から GitLab イシュー・MR 操作を除き、
kiro-flow のインターフェースに合わせて再構成したもの。
**操作上の正文は `scripts/prompt.py` の定数**であり、本書はその設計意図を残す。

## 前提 — gitlab-idd との対応

gitlab-idd では「イシュー」が仕事の単位で、明確化・レビュー・差し戻しは
イシューコメントとラベルの往復で行う。kiro-flow では対応物が次のように変わる:

| gitlab-idd | kiro-flow |
|-----------|-----------|
| イシュー本文・受け入れ条件 | ノードの `goal`（＋run の元要求 `request`） |
| イシューコメント（成果報告） | ノードの `output` / `data` |
| ブランチ・MR | ワークスペース（kf/<run_id> ブランチへ kiro-flow が自動 commit/push） |
| `status:needs-clarification`（人に質問） | 質問不可 → **推測解釈を前提として成果に明記** |
| レビュアーの Request Changes | verify ノードの `{"ok": false, "issues": [...]}` |
| リクエスターのリオープン・差し戻し | evaluator の replan（作り直しタスク生成） |
| スコープ外タスクのイシュー起票 | evaluator の new_tasks（worker は報告のみ） |
| self-defer / assign ロック | 不要（claim/lease が kiro-flow 側で解決済み） |

## worker（work / generate / map）

1. **解釈の確定** — goal を受け入れ条件として読み、完了の定義を先に固定する。
   gitlab-idd の明確性チェックは「質問して 24h 待つ」だったが、kiro-flow のノードは
   同期実行で人に質問できない。そこで「最も妥当な推測解釈を選び、前提として成果に
   明記する」に変換した（gitlab-idd でも 24h 無回答時は同じ動きになる）。
2. **影響範囲の確認**（スカウトマップ相当）— ワークスペースがあるとき、編集前に
   変更対象・依存関係・リスク箇所を特定する。重複調査防止の投稿は不要（ノードは
   単独担当）だが、「調べてから最小の変更」という順序が本質なので残した。
3. **スコープ厳守** — goal と `workspace.path` の範囲外を変更しない。範囲外の発見は
   直さず報告に記す。gitlab-idd ではレビュアーが派生イシューを起票していた役割を、
   kiro-flow では evaluator が new_tasks で担う。
4. **自己検証**（実装ループ＋ project-dod 相当）— 完了宣言前に受け入れ条件と
   突き合わせ、テスト・リンタ・型チェックを実行可能なら実行する。agent-reviewer の
   多角レビューは verify ノード（別 LLM・独立検算）に外出しされているため、
   worker 側は「提出前セルフチェック」に絞る。機密情報の混入禁止は project-dod から。
5. **報告契約**（サマリーコメント相当）— 成果・検証結果・前提・未解決事項を
   自己完結で書く。後続ノードと verify はこの報告だけを入力に判断する。

generate は「並列の他候補と差別化する切り口の明示」を追加（fan-out の多様性確保）。
map は「与えられた 1 要素のみに適用」の既存契約を維持。

## 集約・選別系（classify / synthesize / filter / judge / reduce / split）

出力契約（形式）の厳守が最優先。追加した規律は
「入力を鵜呑みにしない」「判断に根拠を添える」の 2 点のみで、
既存の kind 別契約（classify の `class=`、split の JSON 配列、reduce の count 整合）は
kiro-flow パーサとの互換のため一言一句維持する。
filter に `{"kept": [...]}`、judge に `{"winner": "<dep id>"}` の末尾 JSON を追加した
（stub executor の構造化出力と揃え、後段が機械処理できるように）。

## verify

non-requester-review のステップ 3（agent-reviewer 評価＋結果集約）と
requester-review の判定基準表を 1 回の独立検算に圧縮した:

- **独立に再導出**: ワーカーの結論をなぞらない。実物（ファイル・diff）を確認し、
  テストを実行できるなら実行する。
- **チェック観点**: 受け入れ条件充足 / 集計整合 / 抜け漏れ・重複 / 抜き取り検査 /
  スコープ外混入。従来プロンプトの (1)〜(3) に、判定基準表由来の「受け入れ条件」
  「実装スコープ」を追加した形。
- **判定規律**: 重大のみ fail。minor は `(minor)` 付きで issues に残して pass 可。
  gitlab-idd の「軽微のみ → Phase 5 へ進む」と同じ閾値設計で、
  fail ループの空転（好みの指摘での無限差し戻し）を防ぐ。
- **指摘の粒度**: issues は再作業者がそのまま着手できる粒度で書く。
  evaluator が issues を作り直しタスクの goal へ転記する前提のため。

出力契約は従来と同一: `verify=pass|fail` ＋ `{"ok": bool, "issues": [...]}`。

## evaluator（continue / replan 判断）

requester-review のレビュー・リオープンフローの蒸留:

- **人フィードバック最優先** — kiro-flow の既存動作（human_feedback_from_results）を維持。
- **受け入れ評価** — 判定基準表を「機能充足 / verify pass / 範囲外成果なし」に圧縮。
- **差し戻しの具体化** — リオープンコメントの「不足箇所・修正方針を具体的に」を、
  作り直しタスクの goal へ verify issues を転記する規律に変換。
- **膨張禁止** — スコープ外起票の判断基準から「要求達成に必須のもののみ」を採用。
  改善アイデアは reason に記すに留める（無限にタスクが増える事故の防止）。
- **打ち切り** — kiro-flow のサーキットブレーカー（max_retries）を尊重し、
  達成不可能な条件では done を返す。

出力契約は従来と同一: `{"decision": "done"|"replan", "reason", "new_tasks": [...]}`。

## gitlab-idd から意図的に持ち込まなかったもの

- イシュー・ラベル・コメント・MR 操作全般（gl.py）— kiro-flow に対応物がない
- self-defer / self-review ロック / 放置アサイン — claim/lease が解決済み
- skill-selector / agent-reviewer / council-system の起動 — 1 ノード 1 呼び出しの
  実行モデルに合わないため、多角レビューは verify ノードとして planner が構成する
- LTM 保存・レトロスペクティブ — kiro-flow 本体の還元ループ（§18）の領分
