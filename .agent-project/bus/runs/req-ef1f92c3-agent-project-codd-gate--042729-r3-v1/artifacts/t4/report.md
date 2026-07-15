# t4: repos.json による対象リポジトリ解決の確認

## (a) 成果・サマリー

**結論: 現行の `regression_cmd`/`intake_cmd`（`.agent/agent-project.yaml:30-31`）は repos.json を正しく参照できていない。** 実行時 cwd と `--repos` に埋め込まれたパスが二重に `.agent-project/` を含み、ファイルが見つからず即失敗する。実機の `codd-gate` バイナリで実際にこの2つの値を叩き比べ、失敗と成功を両方とも再現した（詳細は (b)）。

- `.agent/agent-project.yaml:30-31` の現行値:
  ```yaml
  regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
  intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'
  ```
- これらは `agent_project/mr.py:437-438`（`_settle_task` の回帰ゲート）から `run_verify(cfg.regression_cmd, vcwd, ...)` として実行される。`vcwd` は `_task_verify_cwd`（`agent_project/verify.py:103`）が返す値で、このプロジェクトの全タスクは charter `## repos` が空（`owns` を持つリポジトリ未宣言）のため `- workspace:` を持たず、`_workspace_spec_for` は常に `None` → `resolve_verify_cwd(cfg)` = **`cfg.workdir`** にフォールバックする（`verify.py:94-100, 117-141`）。
- `cfg.workdir` は `configfile.py:236-248` の `root`（`.agent/agent-project.yaml:4` の `root: .agent-project`）を `_redirect_root_to_state_worktree`（`state.py:98`）で状態 worktree へリダイレクトした**その先**で、実機では既に `/Users/nitto/Workspace/sandbox-agent-state/.agent-project`（＝このタスクの作業 cwd そのもの）に一致する。
- つまり `regression_cmd`/`intake_cmd` は **cwd が既に `.agent-project` であるところへ、さらに `.agent-project/repos.json` という相対パスを重ねている**。実際に存在する `repos.json` はその二重ネストの外、`cfg.workdir` 直下（`/Users/nitto/Workspace/sandbox-agent-state/.agent-project/repos.json`）にある。
- さらに、`--repo-dir NAME=DIR`（codd-gate がレジストリの各エントリに対応するローカル checkout を見つけるための引数。`tools/agent-project/codd_gate_routing.py` が生成器を持つ）が `regression_cmd`/`intake_cmd` のどちらにも一切含まれていない。付けないと `--repos` 解決後も「スキャン可能な repo がありません」で止まる。

## (b) 検証内容と結果

### 1. 実機 codd-gate への直接プローブ（cwd: `/Users/nitto/Workspace/sandbox-agent-state/.agent-project` — 実際の regression_cmd 実行 cwd と同一）

```
$ cat repos.json
{
  "src": {
    "base": "main",
    "url": "https://github.com/ynitto/sandbox"
  }
}

$ codd-gate verify --base HEAD --repos .agent-project/repos.json   # 現行 regression_cmd の --repos 値そのまま
[codd-gate] エラー: repos レジストリが見つかりません: .agent-project/repos.json
exit=2

$ codd-gate verify --base HEAD --repos repos.json --repo-dir src=.   # cfg.workdir 基準の正しい相対パス + --repo-dir
差分: src HEAD..作業ツリー（80 ファイル）
  [GREEN] ...（略。AMBER 所見多数=ドキュメント参照切れの実検出。repos.json 解決自体は成功）
exit=1（drift 所見ありの非0。「レジストリが見つかりません」は出ていない＝解決成功の証跡）
```

全ログは `verification_log.txt` に保存（このプローブに加え (2) の再現も含む）。**現行値は即エラー、修正候補は実際にスキャンを走らせて実データ（隣接タスク t1-t3 の report.md への参照切れ）を検出するところまで到達した** — これは「repos.json 解決が正しく参照される」を実データで裏取りした最有力の証跡。

### 2. コードパスの独立再現（`repro_repos_json_resolution.py`。隔離した `/tmp` の使い捨て git リポジトリのみを使用、実リポジトリには一切書き込んでいない）

`root: .agent-project` という実環境と同じレイアウトで `agent_project.configfile.build_config` を呼び、`cfg.workdir` と `repo_registry_path(cfg)` が常に同一ディレクトリを指すこと、そして `codd_gate_wiring.recommend_regression_cmd`（自動検出が使う推奨コマンド生成器）が repos.json 発見時に生成する値が `--repos ./repos.json`（**`.agent-project/` プレフィックス無し**）になることを確認した:

```
cfg.workdir        = .../ap_repro_t4-agent-state/.agent-project
repo_registry_path = .../ap_repro_t4-agent-state/.agent-project/repos.json
regression_cmd (自動推奨) = codd-gate verify --base "$KIRO_BASE_REV" --repos ./repos.json
intake_cmd (自動推奨)     = codd-gate tasks --debt --repos ./repos.json
```

これは (1) の実機結果と完全に整合する。**系統自身の自動検出ロジック（`codd_gate_wiring.py`）自体が、現行の手書き値とは異なる（かつ正しい）相対パスを算出することを示している。**

### 3. 実行プロセスの裏取り

稼働中の agent-project 本体プロセス（`ps` で確認: `PID 1704`、`python3 /Users/nitto/.local/bin/agent-project`）の cwd は `lsof` で `/Users/nitto/Workspace/sandbox` と確認。CLI 引数の明示指定は無し（`--verify-cwd` 等の上書きなし）。設定ファイルの `root: .agent-project` がそのまま解決され、状態 worktree（`/Users/nitto/Workspace/sandbox-agent-state/.agent-project` — 既存・このタスクの作業 cwd と同一）へリダイレクトされる経路と矛盾しない。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- 完了条件（`.agent/agent-project.yaml` への grep）は t1/t2/t3 により既に成立済みと確認されているため、本タスクの主眼は「repos.json 解決の正しさの確認と証跡化」であり、`.agent/agent-project.yaml` への書き込みは行っていない（そのファイルの結線内容そのものは t2/t3 の担当領域であり、書き換えは「三つの約束」の範囲順守に反すると判断した）。
- `- workspace:` 未指定タスクの回帰ゲート実行 cwd は `cfg.workdir` になるという経路を、実機プロセスの cwd・状態 worktree の実在パス・このタスク自身の作業 cwd の三点一致で裏取りした。charter に `owns` 付き repos が今後追加されタスクへ `- workspace:` が付与されるようになれば、その回帰ゲートは対象 repo のクローンルートで実行されるため本問題の前提が変わる点は明記しておく。

**未解決事項（範囲外）**
- `.agent/agent-project.yaml:30-31` の実際の修正（`--repos .agent-project/repos.json` → `--repos repos.json --repo-dir src=.` 相当への置換、または手書き2行を削除して `build_config` の自動検出に委ねる）は行っていない。t2/t3 は「既存記述は正しい」と報告済みだが、本タスクの実測はそれと矛盾する結果を示した。この食い違いの裁定と該当タスクの作り直しは t5（敵対的検証ゲート）の担当と判断した。
- `codd_gate_routing.py`（`--repos`/`--repo-dir` の組み立て関数、s6 仕様・単体テスト済み）は `agent_project/` パッケージのどこからも呼ばれておらず（`grep` で非参照を確認済み）、`regression_cmd`/`intake_cmd` の自動生成（`codd_gate_wiring.recommend_*`）も `--repo-dir` を一切生成しない。「対象リポジトリ解決」を安定させるには `--repo-dir` の自動注入も必要になるが、これは配線ロジックの拡張であり本タスクの確認スコープを超える。
- `repos.json` の `"src"` エントリ（`base: main`, `url: https://github.com/ynitto/sandbox`）がいつ・どのタスクで生成されたかは追えていない（`_meta.generated_from` マーカーが無く、charter の `## repos` も空のままなので `export_repo_registry` による自動生成物ではなく手書き/別経路の可能性が高い）。charter 側に対応する `## repos` エントリが無いままだと、次に `export_repo_registry` が走った際にこのファイルが消される可能性がある（`charter.py:424` の「charter から repos が消えたら生成物も消す」分岐は生成物判定＝`_meta` 有無で入るため今回は該当しないが、経路が不明な点は留意）。

**範囲外で見つけた問題**
- 上記の通り、`--repos` パスの二重ネストと `--repo-dir` 欠落の2点は regression_cmd/intake_cmd 自体の不備であり、t2/t3 の成果物の妥当性に関わる。両タスクへのフィードバックとして t5/t6 に引き継ぐ。

## 成果物

- `bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v1/artifacts/t4/report.md`（本ファイル）
- `bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v1/artifacts/t4/repro_repos_json_resolution.py`（隔離環境での再現スクリプト）
- `bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v1/artifacts/t4/verification_log.txt`（(1)(2) の実行ログ）
