# kiro-autonomous — プロジェクトを最上位コンテナにする（複数プロジェクト構成・設計メモ）

- 日付: 2026-06-21
- 位置づけ: [project-loop 設計メモ](2026-06-21-kiro-autonomous-project-loop-design.md) の構造拡張。
  charter 駆動ループ（`project`）を **1 ディレクトリ＝1 プロジェクト**へ一般化し、**複数プロジェクトの併存**と
  **per-project の needs/decisions**、**charter リンクによる横展開**を入れる。
- 状態: **実装済み（MVP）**。`tools/kiro-autonomous/kiro-autonomous.py` に `--project` / `projects/<name>/`
  レイアウト / charter `## links` を追加。テストは `TestMultiProject`（5 件）＋既存改修。
- 方針合意（本メモの前提）:
  1. **全て per-project**（backlog/needs/decisions/archive/charter/policy/journal/DELIVERY/project.json を
     `projects/<name>/` 配下に集約）。横断は instances レジストリと charter リンクのみ。
  2. **新レイアウトのみ**（flat 互換は持たない。常に `projects/<name>/`）。
  3. charter リンクは**リンク先の定義（charter）＋判断（decisions の learn）をエージェント文脈に取り込む**（横断 recall）。

---

## 0. 背景

現状は `<root>=.kiro-autonomous/` 直下に backlog/needs/decisions/… を**フラットに 1 セット**だけ持つ＝
**1 root = 1 プロジェクト**。複数の目標（charter）を並行で回せず、needs/decisions も「どのプロジェクトの判断か」を
区別しない。本メモは **プロジェクト > バックログ** の階層にし、`projects/<name>/` を一級コンテナにする。

```
.kiro-autonomous/                    ← コンテナ（--root。projects/ を束ねるだけ）
  projects/
    default/                         ← 1 プロジェクト（--project。未指定はこれ）
      charter.md  project.json  policy.md  journal.md  DELIVERY.md  run-log.jsonl
      backlog/  needs/  decisions/  archive/  inbox/  claims/  autonomy/  bus/
    payments-api/                    ← もう 1 つのプロジェクト（併存可）
      charter.md  …（同じ一式）
```

`instances` レジストリ（`~/.kiro-autonomous/instances/`）は従来どおり**グローバル**で、各プロジェクト root を
監視先として登録する＝複数プロジェクト・複数ホストを横断発見できる（既存の仕組みがそのまま効く）。

---

## A. プロジェクト選択（`--project`・未指定は default を作成）

- 全サブコマンドに **`--project <name>`**（既定 `default`）。effective root = `<root>/projects/<safe(name)>/`。
- **`enqueue` でも `--project` を指定**してそのプロジェクトへ積む。**未指定なら default プロジェクトを作成**して積む
  （`ensure_dirs` が backlog/needs/decisions を作るので「未指定時は作成」を満たす）。
- ディレクトリ名は unicode を保つ FS セーフ化（`/ \ : * ? " < > |`・制御文字を `_` 化、前後 `.` 除去）。
  日本語プロジェクト名も使える。
- 実装は **build_config の root 計算を 1 段深くするだけ**: 全 per-project パスは `backlog.parent`（=project root）
  から派生しているため、root を `projects/<name>/` にすれば backlog/needs/decisions/archive/charter/policy/
  journal/DELIVERY/project.json/autonomy/bus が**自動的にプロジェクト配下へ移る**（Config 構造・既存ロジックは不変）。

```bash
kiro-autonomous enqueue --title "X を直す" --verify '…'                  # default プロジェクトへ（無ければ作成）
kiro-autonomous enqueue --project payments-api --title "…" --verify '…'  # 別プロジェクトへ
kiro-autonomous project --project payments-api                          # そのプロジェクトの charter ループ
kiro-autonomous needs   --project payments-api                          # per-project の判断待ち
kiro-autonomous instances                                              # 稼働中の全プロジェクト root を横断一覧
```

---

## B. needs / decisions の per-project 化

per-project root 化に伴い、**needs/ と decisions/ は自動的にプロジェクト配下**になる（A の root 派生の帰結）。

- 判断待ち（`needs/<id>.md`）・決定記録（`decisions/<id>.md`）・検収ゲート・自律裁定・DR 学習は、**その
  プロジェクトの中だけ**で完結する。別プロジェクトの判断が混ざらない。
- `approve`/`hold`/`reprioritize` も `--project` でプロジェクトを選んで操作する。
- milestone（プロジェクト収束）の id は **プロジェクト名（`--project`）** を一次採用し、未設定（テスト等で
  Config を直接構築した場合）は charter の `# Charter: <name>` から導出（後方互換）。
  `_project_id(cfg, charter) = cfg.project_name or slug(charter.name) or "project"`。

---

## C. charter リンクによる横展開（定義＋判断の取り込み）

charter.md に **`## links`** セクションを設け、他プロジェクトを参照できる。リンク先の**定義（charter）と
判断（decisions の `- learn:`）**を act ワーカーの文脈に取り込む（横断 recall）。

```markdown
## links
- shared-conventions      # projects/shared-conventions を参照（共通規約プロジェクト）
- ../infra-rules          # 相対パスでも可
```

- **解決**: 名前なら `<container>/projects/<safe(link)>`、`/`・`..` を含むならプロジェクト root からの相対パス。
- **定義の取込**: `charter_context(cfg)` が現プロジェクト定義に続けて、リンク先の goal/constraints を
  「リンク: <name>」として要約付与（有界）。
- **判断の取込**: `build_request` が `linked_learnings_context(cfg)` で、リンク先 decisions の `- learn:` 行
  （＝再利用可能な人の判断）を digest として付与（末尾優先・有界）。
- いずれも**依頼文字列の組み立てのみ**＝決定的・有界。リンクが無ければ従来どおり空（疎結合）。
- ltm-use（横断長期記憶）が「実績で昇格した学習」をプロジェクト跨ぎで効かせるのに対し、charter リンクは
  「**人が明示した参照先**」の定義/判断を確実に引く（明示 opt-in・予測可能）。

---

## D. 維持する不変条件 / 後方互換

- **done は verify（acceptance）でのみ確定**・**必ず有限停止**・**人の policy 優先**・**stdlib のみ・決定的**は不変。
- 変更は **(1) root を 1 段深くする (2) charter に links を足す (3) 文脈注入にリンク先を加える** の 3 点で、
  per-project ロジック本体（run_loop/評価/ゲート）には触れない。
- **Config を直接構築するコード（テスト等）は不変**: nesting は CLI（build_config）でのみ起こり、明示パス指定は
  従来どおり効く。`project_name` 未設定なら milestone id は charter 名から導出（既存 project テストが不変）。
- flat 互換は持たない（方針）。ドキュメント・`charter.md.example` を新レイアウト前提に更新する。

---

## E. 追加/変更する I/F

| 種別 | 追加・変更 | 既定 | 意味 |
|------|-----------|------|------|
| CLI（全 sub） | `--project <name>` | `default` | 操作対象プロジェクト（未指定は default を作成） |
| レイアウト | `<root>/projects/<name>/…` | — | per-project の一式（root を 1 段深く） |
| Config | `project_name: str` | `""` | 選択中プロジェクト名（milestone id の一次ソース） |
| charter | `## links` | （無） | 横展開リンク（定義＋判断を取込） |
| 関数 | `parse_charter` に `links` | — | links を抽出 |
| 関数 | `resolve_linked_projects(cfg, charter)` | — | リンクを project root へ解決 |
| 関数 | `charter_context` にリンク定義 / `linked_learnings_context` | — | 定義＋判断の横断注入 |
| 関数 | `_project_id(cfg, charter)` | — | project_name 優先・charter 名フォールバック |

---

## F. テスト想定

- `--project` で per-project root が `projects/<name>/` に解決され、enqueue が別プロジェクトへ積み分かれる。
- 未指定で default プロジェクトが作成される。
- needs/decisions/approve が per-project に閉じる（別プロジェクトの判断が混ざらない）。
- FS セーフ化（日本語名・記号）でディレクトリが衝突なく作られる。
- charter `## links` の解決（名前/相対パス）と、`charter_context` がリンク先定義を取り込む。
- `linked_learnings_context` がリンク先 decisions の learn を取り込む（有界）。
- 既存の project テスト（Config 直接構築・project_name 未設定）が**不変**で通る。

---

## G. 非目標（将来拡張）

- プロジェクト跨ぎの優先度調整・ポートフォリオスケジューラ（まずは各プロジェクト独立で回す）。
- charter リンクの循環・推移解決（MVP は 1 階層・自己/重複は無視）。
- flat→projects 自動移行（方針: 新レイアウトのみ）。
