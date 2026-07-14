# docs/designs 棚卸し — 歴史的経緯カテゴリ + 完全ファイル一覧（網羅性検証の基準）

対象リポジトリ: `/Users/nitto/Workspace/sandbox`（charter が指す実装リポジトリ。本 worktree
`sandbox-agent-state` 上には `docs/designs` が存在しないため、t1 の前例と同様に `ls -la` で
実在確認したうえで各ファイルを参照読みした。書き込みは行っていない）

## 1. 歴史的経緯カテゴリ — 対象ファイル

「歴史的」の判定基準: 現在の運用中システムの振る舞いを説明する設計書ではなく、
(i) 名称変更・移行など既に完了した決定の対応表、または
(ii) 複数案を比較した末に不採用となった／別案に置き換わった設計、または
(iii) 後継バージョンの設計書に前提として明示的に置き換えられた旧バージョン設計、
のいずれかに該当し、現行の実装・運用を理解するには後継ドキュメントを読むべきもの。

| ファイル名 | 要旨（1〜2文） | 対象読者 | 歴史的とみなす根拠 |
|---|---|---|---|
| `agent-tools-rename-design.md` | 旧 `kiro-*` 系統（kiro-project/kiro-flow/kiro-projects-viewer/kiro-loop）を `agent-*` へクローン移行・改称する方針、新旧名称対応表（ディレクトリ・パッケージ・設定・env・ブランチ接頭辞等）、非目標を定めた設計書。 | 移行作業を行った実装者／新旧名称対応やパス変遷の経緯を確認する開発者 | 内容そのものが「何を何から何へ改称したか」という**完了済み移行の対応表**であり、現行システムの振る舞いを規定するものではない。文書中でも「移行完了後は旧系統を削除」「新設計書ヘッダには由来を履歴として残す」と明記し、自らを移行履歴として位置づけている。agent-project-design.md 等の現行系統設計は既にこの移行を経た後の状態を記述しており、本書はその**経緯（why 過去にこの名前だったか）を追うための記録**として残る。 |
| `ltm-use-v4-design.md` | ltm-use に記憶クラスタリング・類似記憶推薦（TF-IDF ベースのハイブリッドランキング、auto-tagging）を追加する設計。ステータスは Draft、前提は ltm-use v3.0.0。 | ltm-use の設計変遷を追う実装者／v5 以降の設計判断の背景を知りたい開発者 | 同ディレクトリの後継 `ltm-use-v5-brain-design.md`（作成日 2026-03-12、本書より4日後）が「前提: ltm-use v4.0.0」と明記しており、本書が定義した v4 設計は既に実装・確立された前提として v5 に引き継がれ、設計の主戦場は v5（脳構造インスパイア設計）に移っている。現行の記憶層設計を理解するには v5 を読むべきで、本書は**バージョン系列上すでに次段階に置き換わった経緯**として位置づく。 |
| `selfhost-forge-comparison.md` | 「上流 GitLab をマスターに保ちつつアクセス負荷を下げる」ためのローカル構成案（案A: ローカル GitLab CE／案B: ローカル Gitea+移植／案C: コードのみローカル分離／案D: GitLab Geo／案E: 読み取りキャッシュ）を比較し、推奨（案C、例外条件下では案A）を示した比較資料。 | セルフホスト方式の選定根拠を確認する運用者・アーキテクト／なぜ現行構成（案A採用）に至ったかを知りたい開発者 | 文書の性質が「複数案の比較検討」であり、現行運用中の1システムの振る舞いを記述するものではない。実際に採用されたのは `plan-a-local-gitlab-design.md`（該当ファイルのヘッダに「関連: selfhost-forge-comparison.md（案の比較・案A採用）」と明記）であり、本書はその**意思決定に至った比較検討の記録**。現行構成を知るには plan-a-local-gitlab-design.md を読むべきで、本書は決定の経緯資料として残る。 |
| `gitea-gitlab-sync-design.md` | LAN 内 Gitea を Issue/MR の管理面にしつつコードは GitLab と同期する構成（`selfhost-forge-comparison.md` の案B に相当）の設計正典。reconcile daemon による fast-forward 限定の双方向同期を規定する。 | セルフホスト構成の代替案を確認する運用者・アーキテクト（Gitea 案を再検討する際の参考） | `selfhost-forge-comparison.md` の比較表で案Bとして評価されたが、推奨は案C、実際の採用は案A（`plan-a-local-gitlab-design.md`）であり、本書の構成（Gitea ベース）は**採用されなかった代替案**。ヘッダにも「新規インフラ構成。実装は未着手」とあり、実装に進んだ形跡がない。現行のセルフホスト構成を知るには plan-a-local-gitlab-design.md を読むべきで、本書は不採用案の検討記録として残る。 |

### 検討したが歴史的カテゴリから除外したファイル（根拠）

- `node-federation-design.md` — タイトルに「✅ 実装済み」とあり、本文冒頭で「このドキュメントは元々の
  設計仕様書として残しつつ、実装完了状態を反映している。各セクションは現行実装の仕様リファレンス
  としても参照できる」と明記。設計書由来だが**自己申告で現行の仕様参照ドキュメントを兼ねる**ため、
  歴史的経緯カテゴリではなく現行カテゴリ（外部連携・インフラ）側の対象と判断した。
- `plan-a-local-gitlab-design.md` — 「本書は案Aの設計正典」と明記され、比較検討の結果**採用された
  現行構成**を記述する。歴史的経緯側ではなく現行カテゴリの対象。
- `gitlab-agent-sns-design.md` — 「段階的な意思決定（v1〜v5）の到達点を整理した確定版」と明記され、
  内部に過去の意思決定過程を含むものの、文書自体は現行の Moltbook 設計を記述する確定版。現行カテゴリの対象。
- `kiro-loop-*-design.md`（4件: adaptive-interval / agent-messaging / event-hook / gitlab-webhook）と
  その対 `agent-loop-*-design.md` — `agent-tools-rename-design.md` の改称対象に含まれる同名重複組であり、
  どちらが現行でどちらが改名残骸かの判定は t2 のタスク範囲（「一貫性ゲート・ループ拡張」カテゴリ）と
  明示的に重複するため、二重計上を避けて本タスクの表には含めなかった。t2 の判定結果を歴史的カテゴリの
  最終候補に合流させるかどうかは gate タスクでの突合せに委ねる。

## 2. docs/designs 配下の全 *.md 完全一覧（README.md 自身を除く／網羅性検証の基準）

実行コマンド: `cd /Users/nitto/Workspace/sandbox/docs/designs && ls -1 *.md | grep -v '^README\.md$' | sort`

```
agent-cli-plugin-design.md
agent-flow-design.md
agent-flow-retry-inheritance-design.md
agent-loop-adaptive-interval-design.md
agent-loop-agent-messaging-design.md
agent-loop-event-hook-design.md
agent-loop-gitlab-webhook-design.md
agent-project-design.md
agent-tools-rename-design.md
codd-gate-design.md
git-gitlab-circuit-breaker-pattern.md
git-worktree-cache-pattern.md
gitea-gitlab-sync-design.md
gitlab-agent-sns-design.md
kiro-loop-adaptive-interval-design.md
kiro-loop-agent-messaging-design.md
kiro-loop-event-hook-design.md
kiro-loop-gitlab-webhook-design.md
ltm-use-v4-design.md
ltm-use-v5-brain-design.md
node-federation-design.md
plan-a-local-gitlab-design.md
selfhost-forge-comparison.md
```

合計 23 ファイル（`README.md` は現時点で未作成のため一覧に含まれない＝本 run の最終成果物として
`docs/designs/README.md` が新規作成される想定）。

## 3. 検証内容と結果

- `ls -la /Users/nitto/Workspace/sandbox/docs/designs/` で全ファイルの実在を確認し、上記完全一覧は
  推測ではなく `ls` 実行結果そのものから作成した。
- 歴史的カテゴリの4件は、各ファイルの本文（冒頭ヘッダ・関連リンク・ステータス欄・比較表・推奨セクション）
  を実際に読み、他ファイルからの「関連」「前提」「由来」リンクによって後継／採用されたドキュメントが
  別に存在することを裏付けたうえで判定した（推測で「歴史的」と分類していない）。
  - `agent-tools-rename-design.md` 本文（改称対応表・非目標節）を確認。
  - `ltm-use-v4-design.md` と `ltm-use-v5-brain-design.md` の両ヘッダの「前提」記述を突合。
  - `selfhost-forge-comparison.md` の比較表・推奨節（§5）と `plan-a-local-gitlab-design.md` ヘッダの
    相互参照を突合。
  - `gitea-gitlab-sync-design.md` ヘッダの実装状況注記と `selfhost-forge-comparison.md` の案B評価を突合。
- 本タスクの完了条件（`docs/designs/README.md` の存在・4大設計書への言及）は本タスク（t4: 棚卸しと
  完全ファイル一覧の出力）ではなく、この run 全体（synth タスクによる README.md 新規作成）に対する
  検証コマンドと判断した。t1 の判断を踏襲し、本タスクでは README.md 自体の作成・配置は行っていない。

## 4. 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**:
- 本 worktree（`sandbox-agent-state`）に `docs/designs` が存在しないため、t1 と同様に charter が指す
  実装リポジトリ `/Users/nitto/Workspace/sandbox`（同一 git リポジトリの別 worktree）の
  `docs/designs/` を参照読み専用で確認した。書き込みは行っていない。
- 「歴史的」の判定は、ドキュメント自身の記述（ステータス・前提・関連リンク・採用/不採用の明記）に
  基づく一次情報からのみ行い、ファイル名やタイムスタンプの新旧だけでは判定しなかった
  （例: `node-federation-design.md` はタイムスタンプが古めでも自己申告で現行を兼ねるため除外）。
- `kiro-loop-*` / `agent-loop-*` の重複4組は t2 の担当領域と明示的に重複するため、本タスクの表からは
  意図的に除外した。これらの中に「歴史的」に該当するものが含まれる可能性はあるが、その判定と扱い
  （両方掲載＋注記）は t2 の goal で明示されており、gate タスクでの突合せに委ねる。

**未解決事項**:
- `agent-flow-retry-inheritance-design.md` は「旧 kiro-flow 系統から改称した設計。旧設計書は移行完了後に
  削除済み」という改称由来の記述を持つが、本書自体は改称後に更新され続けている現行の詳細設計（t1が
  「衛星ドキュメント」と位置づけ）であり、歴史的カテゴリには含めなかった。README 執筆時にこの判断で
  問題ないか gate での確認を推奨する。

**範囲外で見つけた問題**:
- なし（本タスクのスコープ内で完結）。
