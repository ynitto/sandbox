# マルチリポジトリ・ルーティング設計（kiro-project × kiro-flow）

大規模・複数リポジトリのプロジェクトを自律的に回すための「タスク → コミット先リポジトリ」の
ルーティング設計。**判断は制御層（kiro-project）に集約し、執行は実行層（kiro-flow）が担保する。**

## 役割分担

| レイヤ | 役割 | リポジトリの扱い |
|---|---|---|
| **kiro-project**（制御・ルーティング層） | バックログの優先順位付け・verify ゲート・決定記録 | タスクを**ちょうど1つの書込先ワークスペース**へルーティングし `--workspace` で渡す。参照リポジトリは `--reference` で構造化伝搬する |
| **kiro-flow**（実行層） | タスク分解・worker 実行・bus 同期 | 渡された**唯一のワークスペース**を clone し、作業ブランチ `kf/<run-id>` を作って worker へ渡す。変更があれば commit/push する |

## 基本原則

1. **1 run（=バックログ単位）= 1 ワークスペース（唯一の書込先）。** kiro-flow の入口で 1 つに固定。
   複数リポジトリへまたがる変更は、kiro-project が **repo 別タスクへ分割**し `after`（依存）で順序付ける。
2. **リポジトリの同一性は (url, path, base)。** 同 URL でも path（モノレポのフォルダ）や base（作業ブランチ）が
   違えば別ワークスペース。
3. **書き込みは kiro-flow が掌握。** エージェントは作業ツリーを編集するだけ。kiro-flow が作業ブランチへ commit し、
   分散 worker は同じ `kf/<run-id>` へ push（rebase リトライで統合）。
4. **書かないなら何もしない。** 変更ゼロのグラフ（調査タスク等）はブランチを push しない。
5. **参照リポジトリは clone せず構造化伝搬。** 読むだけのリポジトリは owns を持たず、kiro-flow へ
   `--reference`（url/path/base/desc）として渡す。kiro-flow はそれをエージェントのプロンプトと
   **gitlab イシュー本文の『## 参照リポジトリ』節**に描画する（要求本文へ畳むと、分解後の各ノード/
   イシューに届かないため）。

## ルーティングの決まり方（kiro-project）

`resolve_workspace(cfg, task, policy)` が次の順で**ちょうど1つ**の書込先を決める（上が優先）。決定はタスク md の
`- workspace:` / `- routed_by:` に書き戻され、毎サイクル LLM を呼ばず安定・監査可能になる。

1. タスクの **`- workspace: <name>`**（人/過去ルーティングの明示）
2. `policy.md` の **`route: <パターン> -> <name>`**（決定論ルール。パターンは id/タイトルの部分一致）
3. charter `## repos` の **`owns:`**（担当パスのグロブ）× タスクの `- paths:` ヒントの一致（決定論推定）
4. **auto-route エージェント**（`route_planner: agent` のとき、charter の desc/owns から LLM が1つ推定）
5. **`default_workspace`** 設定 / 書込先候補が1つだけならそれ

決まらなければ書込先なし＝**読み取り専用 run**。

### plan フェーズ（charter → バックログ生成）での明示

charter を分解してタスクを生成する plan/review フェーズでは、**各タスクに書込先 `workspace` を必ず明示する**
（`assign_plan_workspace`）。書込先は **verify コマンドが操作するパスの `owns` を持つリポジトリ**として
決定論的に確定し（プランナーが付けた owns 持ちの workspace 指定は尊重）、それ以外の charter repo・
プランナーが挙げた repo はすべて **参照（`refs`）** に振り分ける。これにより、生成直後のタスクが
「書込先が未確定のまま」になることを防ぎ、後段の route 層は明示済みの workspace をそのまま使う。

## charter `## repos`（書込先 vs 参照）

```markdown
## repos
- app = git@example.com:team/app.git
  - owns: apps/app/**, services/api/**   # owns 有り → 書込先（ワークスペース）候補
  - base: main
  - target: develop
- core-lib = git@example.com:team/core-lib.git
  - desc: 型定義の参照元                  # owns 無し → 参照リポジトリ（読むだけ・--reference で伝搬）
  - base: main
```

- **owns を書く → ワークスペース候補**（ルーティングの宛先になる）。
- **owns を書かない → 参照リポジトリ**（書込先にしない・clone しない）。

## kiro-flow のワークスペース・ライフサイクル

```
--workspace {url,path,base,target} を1つ受け取る（入口で >1 は扱わない）
  worker: ワークスペースを (url,path,base) 単位で clone
          作業ブランチ kf/<run-id> を base から作成 → エージェントへ渡す
  エージェント: 作業ツリーを編集（path 配下のみ）。commit/push はしない
  kiro-flow: 変更があれば kf/<run-id> へ commit → push（rebase リトライで分散 worker を統合）
             変更が無ければ何もしない（読み取り専用グラフ）
  デリバリ（branch/commit/target）を result に記録 → 消費側が追跡
```

## gitlab executor（人手委譲）

executor が `gitlab` のときは、書込をエージェントでなく人へ委譲する。このとき**起票先 GitLab プロジェクトを
ワークスペース URL から解決**する（SSH/https 両形対応）。`--workspace` が無ければ設定 `gitlab.repo_url` を
フォールバックに使う。生成される MR のターゲットはワークスペースの `target` ブランチ。

## 関連設定

- kiro-project: `route_planner`（kiro/none）, `default_workspace`, `policy.md` の `route:`,
  charter `## repos` の `owns:`、タスクの `- workspace:` / `- paths:` / `- refs:` / `- routed_by:`
- kiro-flow: `--workspace`（CLI/submit、run 毎に1つ）
