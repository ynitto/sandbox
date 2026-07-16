# t9（t5差し替え）: 敵対的検証 — t2/t4/t8 の突き合わせと、実際の検証 cwd での実測

**差別化の切り口**: t2（検出層の実測）・t4（intake 実測 + `_task_verify_cwd` の実装確認）・t8（bare
`agent-project.yaml` の設置と grep 成功の報告）はいずれも高品質だが、**t8 が「検証実行ディレクトリ」
と呼んだ場所が、実際に `_task_verify_cwd` が使う場所と同一かどうかは誰も実測していなかった**。
t9 はそこを実際に再現して実測する。結論から言うと、**t8 の対応は無効（見せかけの成功）である**。

## (a) 成果サマリー — 結論

**完了条件grepは、実際の検証 cwd では exit=0 にならない。t8 の修正は無効。原因は「配置先」（と、
それを深掘りした結果判明した、より根深い「ワークスペース解決の矛盾」）であり、「欠落ファイル」
や「regression_cmd の書式不正」ではない。**

- t8 は bare `agent-project.yaml` を **この worktree（`.agent-project/` 直下、= agent-project の
  `cfg.workdir`）** に置いた。ここで grep を実行すると確かに exit=0 になる（実測済み・下記参照）。
- しかし `_task_verify_cwd`（`tools/agent-project/agent_project/verify.py:108`、参照リポジトリ内）
  の実装上、本タスク（backlog に `- workspace: src` あり）の `task.verify` は
  **`cfg.workdir` では実行されない**。`_workspace_spec_for` が spec を解決し、
  `tempfile.mkdtemp(prefix="agent-verify-")` 配下に **`https://github.com/ynitto/sandbox` を
  都度シャロークローン**した一時ディレクトリを cwd にして実行される。t8 が置いたファイルは
  この一時クローンには存在しないため、影響しない。
- さらに掘り下げると、クローン対象ブランチの解決自体が壊れている。`CONFIG_DEFAULTS` の
  `task_branch=True`・`task_branch_prefix='ap/'`（本プロジェクトの設定ファイル群に上書き無し
  ＝既定適用を確認済み）により、`_workspace_spec_for` は spec の `branch` を
  `ap/agent-project-codd-gate--042729` に強制上書きする。しかしこの run のブリーフィング自体が
  「src は参照のみ・push しない」と明記しており（`meta.json` の `request`/`references`）、
  実際どのワーカーも push していない。結果、`ap/agent-project-codd-gate--042729` ブランチは
  origin に存在せず（`git ls-remote --heads` で確認）、`_clone_repo_shallow` は
  「branch を明示した場合は既定へ無言フォールバックしない」仕様のため **clone 自体が失敗**する
  （実測: `git clone --depth 1 --branch ap/agent-project-codd-gate--042729 ...` → exit=128）。
  `_task_verify_cwd` はこれを `RuntimeError` として送出し、`_settle_task` はこれを
  `ok=False`（「workspace repo の clone 失敗」）として確定する — **grep のパターンにすら
  到達しない**。
- 仮にブランチ解決が `main` にフォールバックする設計だったとしても（=task_branch が無効化されて
  いた場合）、origin/main を実際にシャロークローンして確認した結果、bare `agent-project.yaml` は
  存在しない（`.agent/agent-project.yaml` のみ）。この経路でも grep は exit=2（file not found）
  で失敗する。

**つまり二重の理由で、実際の検証 cwd では grep は成功しようがない。** t8 の「成功」は、
本来使われないディレクトリで手動 grep した場合にのみ再現する見せかけの成功である。

## (b) 検証内容と結果（すべて本セッションで直接実行・実測）

| 検証項目 | 方法 | 結果 |
|---|---|---|
| t8 の配置場所での grep | `.agent-project/`（cfg.workdir）で `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml` | **exit=0**（t8 の報告どおり再現。しかしこれは実検証 cwd ではない） |
| `_task_verify_cwd` の実装確認 | `tools/agent-project/agent_project/verify.py:99-146` を直接読解 | `- workspace:` 指定タスクは `resolve_verify_cwd`（=workdir）ではなく、`_workspace_spec_for` が返す repo を都度シャロークローンした一時ディレクトリを cwd にする（108-146行目） |
| このタスクが workspace 指定か | `backlog/agent-project-codd-gate--042729.md:11` | `- workspace: src` を確認 |
| `- workspace: src` の解決先 | `repos.json` を読解 | `src` → `url: https://github.com/ynitto/sandbox, base: main` |
| task_branch 既定値の確認 | `python3 -c "import agent_project as ap; print(ap.CONFIG_DEFAULTS)"`（sandbox/tools/agent-project） | `task_branch=True`, `task_branch_prefix='ap/'`。本プロジェクトの `agent-flow.yaml`/`.agent/agent-project.yaml` に上書き設定なし（grep 該当なし）＝既定が有効 |
| 解決ブランチ名 | `task_branch_name` = `ap/<task.id>` | `ap/agent-project-codd-gate--042729` |
| そのブランチが origin に存在するか | `git ls-remote --heads https://github.com/ynitto/sandbox` | **不存在**（`ap/` prefix のブランチ自体ゼロ件） |
| 実際に該当ブランチをクローンできるか | `git clone --depth 1 --branch ap/agent-project-codd-gate--042729 https://github.com/ynitto/sandbox <tmp>` | **失敗（exit=128）**: `fatal: Remote branch ... not found in upstream origin` — `_clone_repo_shallow` の設計（分岐失敗を既定へフォールバックしない）と一致し、実運用でも `_task_verify_cwd` が `RuntimeError` を送出し `ok=False` になることを裏付け |
| フォールバック仮定（main）での grep 再現 | `git clone --depth 1 https://github.com/ynitto/sandbox <tmp>` → `grep ... agent-project.yaml` | ファイル不在（`ls`: No such file）、grep **exit=2** |
| regression_cmd/intake_cmd の wiring 判定関数 | `codd_gate_wiring.regression_wired`/`intake_wired` に `.agent/agent-project.yaml`（本 worktree の正典）の値を通す | 両方 **True**（t2/t4 と同結果を再現・独立確認） |
| ユニットテスト | `python3 -m unittest discover -s tests -k codd_gate`（sandbox/tools/agent-project） | **81 passed**（t1/t2/t4 と同数を再現） |
| 参照リポジトリの余計な変更混入チェック | `git status --porcelain` / `git diff --stat origin/main`（`/Users/nitto/Workspace/sandbox`） | ローカル HEAD は origin/main と同一コミット。差分は `.agent/agent-project.yaml`（repos.json パス）と `agent-dashboard` 関連ファイルのみで、いずれも t2 が既報告済みの**既存の未コミット差分**（t2/t4/t8 のいずれのタスクによる新規混入でもない）。t2/t4/t8/t9 のどの作業もこのリポジトリへの書き込みは発生していない |
| 本 worktree（`.agent-project`）側の変更範囲 | `git status --porcelain=v1` | t8 が追加したのは bare `agent-project.yaml` 1ファイルのみ。`tools/agent-project/*.py` 等のコードは無変更（t1〜t8 の「新規実装不要」方針と一致）。他の差分（bus/journal/flow-archive 等）はワークフロー基盤自身のブックキーピングで、本タスクの成果物ではない |

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- 「実際の検証 cwd」を `_task_verify_cwd`（コードの正典）の実装どおりに定義し、t8 の報告する
  「検証実行ディレクトリ」（=この worktree）とは区別した。両者が食い違うこと自体が本検証の
  核心的な発見。
- 参照リポジトリ（`https://github.com/ynitto/sandbox`）へは read-only 制約を厳守し、
  clone・grep 等の読み取り専用コマンドのみ `/tmp` 配下で実行、後始末（削除）まで実施。
  同リポジトリへの書き込み・commit・push は一切行っていない。
- `_task_verify_cwd` を直接 Python から呼び出す完全な動的確認（`build_config` への実引数構築）
  は CLI 引数の再現コストが高く誤設定リスクがあるため断念し、代わりに (i) コード読解と
  (ii) 同一 URL・同一ブランチ名での実クローン実験、の2系統の独立した証拠で結論を裏付けた。

**未解決事項（評価役・人の判断に委ねる）**
- t4 が提起した「DR-0005 は意図的か誤りか」は本検証でも未解決のまま。加えて本検証で新たに
  判明した**ワークスペース解決の矛盾**（backlog の `- workspace: src` が「push して検証する」
  前提の設計なのに、このタスクのブリーフィングでは同じ src を「参照のみ・push禁止」と明記して
  いる）は、grep パターンの経路以前の、より根本的な設計不整合であり、agent-project 側の
  `task_branch` 既定 or ワークスペース定義のどちらかを見直す必要がある。ワーカー権限では
  どちらの是正も選べない。
- 完了条件を実際に満たす手段は、(i) 人が DR-0005 を取り消し `.agent/agent-project.yaml` へ戻す、
  (ii) `- workspace: src` を外す/`verify_cwd` を明示指定し検証を control-plane 側で完結させる
  設計変更のいずれかであり、どちらも人の判断が必要。**ループ継続では解消できない**（環境側の
  設定・権限の問題であり、ワーカーの試行回数に依存しない）。

**範囲外で見つけた問題（報告のみ）**
- t8 が追加した bare `agent-project.yaml`（`.agent-project/agent-project.yaml`）は、実検証には
  効果がなく、かつ「正典は `.agent/agent-project.yaml` のみ」という既存の運用方針
  （二重管理を避ける）に反する孤立ファイルとして worktree に残っている。削除するか残すかは
  本タスクの範囲外（評価役の判断）とし、t9 では削除していない。
- 参照リポジトリのローカルチェックアウト（`/Users/nitto/Workspace/sandbox`）に残る
  未コミット差分（t2/t4既報）は今回も変化なし。verify は origin から都度クローンするため
  結果には影響しないが、誰の変更か不明なまま残存している点は変わらず未解決。

## 結論（完了条件との突き合わせ）

指定コマンド `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml`
は、**この worktree（`.agent-project/`）内では exit=0**（t8 の設置ファイルにより再現）。
しかし**実際に `_task_verify_cwd` が使う cwd（`ap/agent-project-codd-gate--042729` ブランチの
シャロークローン、実際にはブランチ不在によりクローン自体が失敗）では、grep どころか verify
コマンドの実行にすら到達しない**。したがって本タスクに課された完了条件は、現状の設計・制約
（read-only 参照リポジトリ、人の DR-0005 決定の尊重）のもとではワーカーの操作で満たすことが
できない。これは反復（retry）で解消する種類の失敗ではなく、人による設計判断（verify 文言 or
ワークスペース解決方式のいずれかの是正）を要する。
