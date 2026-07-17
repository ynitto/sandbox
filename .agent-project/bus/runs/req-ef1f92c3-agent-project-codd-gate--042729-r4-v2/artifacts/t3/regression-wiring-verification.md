# t3: regression結線の実装 — 実行検証と完了条件の構造分析

**切り口: 静的確認（t1）に加え、実際に codd-gate CLI を動かした実行検証と、
`mr.py`/`verify.py` の実行時契約コードから「完了条件grepが exit=0 にならない真因」を
アーキテクチャレベルで特定した。結論として regression 結線の実装自体は完了済みであり、
残る障害はコードでもファイル内容でもなく backlog タスク定義（`workspace: src`）側にある。**

## (a) 成果サマリー

### 1. regression_cmd の結線は実装・設定ともに完了済み（変更不要）

このタスク専用 worktree（control-plane, cwd=`.agent-project` root）で確認した内容:

- `.agent/agent-project.yaml`（agent-project が実際に読み込む正典パス。README の探索順
  `./.agent/` → `~/.agent/` に合致）と、root 直下の bare `agent-project.yaml`
  （コミット `a5dc830a`, 2026-07-17 の state sync で追加済み・git 管理下）の両方に、
  ```
  regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
  intake_cmd: 'codd-gate tasks --debt --repos repos.json --repo-dir src=.'
  ```
  が設定済み。`diff` を取ると **完全に一致（差分ゼロ）**。
- この cwd で完了条件と同一のコマンドを実行すると **exit=0**:
  ```
  $ grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml
  regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
  $ echo $?
  0
  ```
- 私自身はこの worktree でファイルを一切変更していない（`git status` は元から存在した差分のみ）。
  変更が不要という判断自体が本タスクの成果。

### 2. regression_cmd を実際に動かして動作を実証した（t1 の静的確認を補完）

t1 はユニットテスト（81件 pass）とコードリーディングで結線を確認したが、コマンド文字列が
実運用 cwd で実際に動くかまでは検証していなかった。今回、読み取り専用のローカル恒久チェックアウト
`/Users/nitto/Workspace/sandbox`（コミット `8d1b8bb9`）を `src` の実体とみなし、regression_cmd と
同一構文を実行した:

```
$ cd /Users/nitto/Workspace/sandbox
$ codd-gate verify --base <HEAD~3> \
    --repos <control-plane>/repos.json --repo-dir src=.
...
[AMBER] tools/agent-project/tests/test_agent_project.py 行7315 の参照 ... が解決できない
...
NG: ドリフトあり — `codd-gate tasks` で修復タスクを生成できる
```

`codd-gate` は PATH 上に解決可能（`/Users/nitto/.local/bin/codd-gate`, version 1.0.0）で、
`verify --base --repos --repo-dir` の各オプションは `codd-gate verify --help` の仕様と一致する。
コマンドは実際に差分ドリフトを検出して NG 判定を返す＝`mr.py:437-448` の
「done 確定前グローバル回帰ゲート」が実運用で機能することを実証した（読み取り専用操作のみ、
sandbox には一切書き込んでいない）。

### 3. 完了条件grepが exit=0 にならない真因はタスク定義の `workspace: src` 側にある

`backlog/agent-project-codd-gate--042729.md` を読むと:
```
- verify: `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml`
- workspace: src
- needs_reason: 繰り返し NG（retries=4）: workspace repo の clone 失敗
  （https://github.com/ynitto/sandbox@ap/agent-project-codd-gate--042729）:
  fatal: Remote branch ap/agent-project-codd-gate--042729 not found in upstream origin
```

`tools/agent-project/agent_project/verify.py:_task_verify_cwd`（122-174行目）を読むと、
verify（＝このタスクの完了条件）の実行 cwd は次の優先順位で決まる:
1. 明示 `verify_cwd`（無し）
2. **タスクの `- workspace:` が指す repo の一時 clone**（`workspace: src` によりこれが選ばれる）
3. workdir（control-plane 自身）

一方、`mr.py:437-459` のコメントは対照的な設計を明記している:

> 回帰検査は **常に git-bus ルート（workdir）** で走らせる。task.verify と違い
> `cfg.regression_cmd` はグローバル検査で、パスも差分基準も workdir を前提に書かれる。
> workspace タスクの vcwd（該当 repo の一時 clone）で走らせると codd-gate が repos.json を
> 解決できず…回帰ゲートが壊れる。

つまり `cfg.regression_cmd`（このタスクが実装すべき対象）は設計上「常に control-plane の
workdir で評価される」ものであり、**`workspace: src` を伴う本タスクの `task.verify`
（完了条件grepそのもの）だけが sandbox 側の一時 clone という別の場所で評価される**という
非対称が生じている。sandbox の一時 clone のルートには（ローカル恒久チェックアウトと同様）
`.agent/agent-project.yaml` のみが存在し bare `agent-project.yaml` は無い（本タスクの過去試行
`t9`/`t12` が同一事象を独立に再現済み・`bus/runs/.../artifacts/t12-fix-and-verify-canonical-codd-gate/report.md`
参照）。加えて `ap/agent-project-codd-gate--042729` ブランチが origin に存在しないため、
`_task_verify_cwd` の clone 自体が失敗し、needs（判断待ち）に落ちている。

`verify.py:145-159` には「task_branch が origin に無いと確認できた場合は target/base へ
フォールバックする」ロジックが実装済みだが、needs_reason に記録された実際のエラーは
素の clone 失敗（fallback 不発動）である。フォールバックが発動しなかった正確な理由
（`_remote_branch_exists` が `None` 判定になった等）までは今回特定できていない
— 範囲外の未解決事項として (c) に記す。

## (b) 検証内容と結果

| 検証項目 | 方法 | 結果 |
|---|---|---|
| regression_cmd の値（正典 `.agent/`） | `cat .agent/agent-project.yaml` | 正しい値で設定済み |
| regression_cmd の値（bare, root直下） | `cat agent-project.yaml` | `.agent/` と byte-identical |
| 完了条件grep（control-plane cwd） | `grep -E '...' agent-project.yaml; echo $?` | **exit=0** |
| 完了条件grep相当（sandbox 一時clone/恒久チェックアウト相当のcwd） | `grep -E '...' /Users/nitto/Workspace/sandbox/agent-project.yaml` | ファイル無し（`.agent/agent-project.yaml` のみ存在） — t9/t12 の既存知見と一致 |
| codd-gate CLI 解決 | `which codd-gate && codd-gate --version` | `/Users/nitto/.local/bin/codd-gate`, v1.0.0 |
| regression_cmd の実オプション構文 | `codd-gate verify --help` | `--base --repos --repo-dir` すべて存在、構文一致 |
| regression_cmd の実行動作 | sandbox 本体（読み取り専用）で同一コマンドを実行 | 正常終了・ドリフト検出（AMBER/GRAY）を報告し `NG` 判定 |
| `_HUMAN_OWNED_STATE_FILES` の保護範囲 | `grep -n _HUMAN_OWNED_STATE_FILES tools/agent-project/agent_project/state.py` | `("agent-flow.yaml", "agent-project.yaml")` — **basename マッチ**（フルパス不問） |
| このタスクでのファイル変更有無 | `git status --short` | 変更なし（元から存在した差分のみ、私による編集はゼロ） |

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- 「regression結線の実装」というタスク名の本体は `cfg.regression_cmd` の値そのものであり、
  これは既に control-plane 側（`.agent/agent-project.yaml` および bare 複製）に正しく実装済みと
  判断した。この worktree での完了条件grepが exit=0 であることをもって、実装面の完了条件は
  満たされているとみなした。
- backlog の `- verify:` / `- workspace:` フィールドの書き換えは、t1 が既に「t6（統合）の責務」
  と判断し、過去試行 t12 も「本タスクの裁量を超える設計判断」として見送った領域であるため、
  本タスクでも同様に**変更を行わなかった**（範囲を守る）。

**範囲外で見つけた問題（統合役の判断に委ねる — 是正案）**
- 本タスクの完了条件を実運用で exit=0 にするには、`workspace: src` を外すか
  `.agent-project` 自身を指すよう backlog を修正するのが筋が良い。理由: `mr.py` のコメントが
  `cfg.regression_cmd` は「常に workdir 前提」と明記しており、`workspace: src` は
  「sandbox のコードを変更する」タスク向けの指定であって、「control-plane 自身の設定ファイルを
  扱う」本タスクの性質とそもそも合っていない（t1が指摘した「DR-0005 でパス prefix が落ちた」
  という見立てより一段上位の、タスク定義そのもののミスマッチ）。
- `verify.py:145-159` の task_branch フォールバック（未push ブランチは target/base へ自動で倒す）
  が、本タスクの needs_reason 記録上は発動していないように見える。フォールバック条件
  （`_remote_branch_exists` が明確に `False` を返す必要があり、`None`＝判定不能では従来通り
  clone を試みてそのまま失敗する）が今回どちらに転んだかまでは未特定。エンジン側のバグの
  可能性と、単に ls-remote 自体が別要因で失敗した可能性の両方が残る。

**未解決事項**
- 上記の是正案（backlog の `workspace` 見直し）は人・統合役の意思決定が必要な操作であり、
  本タスクの worktree 内では実施しなかった。

## 機械可読な制約（他ノードへの伝播）

```json
{"constraints": [
  "cfg.regression_cmd/intake_cmd は agent-project 内部設計上『常に control-plane の workdir』で評価される（mr.py:437-451）。workspace 指定タスクの一時 clone 内で regression_cmd の結線を検証・修正しようとしない。",
  "backlog タスクの `- workspace:` は『別リポジトリのコードを変更する』タスクにのみ設定する。control-plane 自身の設定ファイル（agent-project.yaml 等）を扱うタスクに workspace を指定すると、verify.py:_task_verify_cwd が sandbox 側の一時 clone を検証 cwd に選び、そもそも対象ファイルが存在しない場所で完了条件が評価される。",
  "state.py の _HUMAN_OWNED_STATE_FILES はファイル名の basename でマッチする（フルパス不問）。`agent-project.yaml` という名前のファイルは配置場所に関わらず機械の自動 state 同期から保護対象になる。",
  "sandbox（src）リポジトリの一時 clone / 恒久チェックアウトのルートには `.agent/agent-project.yaml` のみが存在し、bare `agent-project.yaml` は存在しない。control-plane 側の bare 複製が存在することを前提にした検証を sandbox 側に対して行わない。"
]}
```
