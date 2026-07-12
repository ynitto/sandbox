# synth_verify / _first_command_line 調査結果

## 成果・1 経路

`run_loop` の前処理 `_run_setup` は、消化可能で `task.verify` が空のタスクに `ensure_verify(cfg, task)` を呼ぶ。`ensure_verify` は `accept` があり、再利用できる verify も template もなければ `synth_verify(cfg, task.title, accept, ...)` を呼ぶ。

`synth_verify` は `_synth_verify_prompt(...)` を LLM runner、通常は `_run_kiro_cli(..., purpose="verify")` に渡す。runner の戻り値 `out` は LLM 応答全体の文字列で、加工せず `_first_command_line(out)` に渡る。ここでは行ごとに ANSI 除去と「行全体が backtick で囲まれる場合」の外側 1 文字除去を行い、空行と `#` コメントを飛ばし、最初のそれ以外の行を即座に返す。返った `cand` は `_looks_like_shell_command` と `_verify_is_degenerate` を通過した場合だけ `synth_verify` の戻り値になる。`ensure_verify` はその値が truthy のときだけ `task.verify = cmd` と `verify_source=synth` を設定する。

例えば LLM 応答が ```` ```sh\npytest -q\n``` ```` なら、`_first_command_line` はフェンス開始行 ` ```sh ` を「最初の意味ある行」として返し、内側の `pytest -q` に到達しない。この候補は `sh -n` による構文検査で不採用になり、`attempts` 回の再合成がすべて同様なら `synth_verify` は例外を投げず空文字 `""` を返す。runner 自体が例外を投げた場合も catch され、同じく `""` になる。その結果 `ensure_verify` の代入ブロックはスキップされて `False` を返し、`_run_setup` では persist/journal もスキップされ、`task.verify` は空のまま残る。したがってこの経路の失敗形は「例外の上位伝播」ではなく「空文字を返し、verify 設定をスキップ」である。

また LLM が英語の前置きを付けた場合、その先頭行が `_first_command_line` の候補になる。シェルとして構文上有効な英文は `_looks_like_shell_command` を通る可能性があり、本来の後続コマンドではなく前置きが verify として保存されうる。

## 検証

- 追加: `_first_command_line` が空行・コメントを飛ばして直接コマンドを返すテスト、候補なしで空文字を返すテスト。
- 完了条件: `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
- 結果: exit 0、`2 passed, 512 deselected`。system Python には pytest がないため、既存の共有 `.venv` を PATH で利用した。

## 前提・未解決・範囲外

- 前提: 本タスクは「実装と呼び出し経路の調査」が担当範囲であり、フェンス/前置きの抽出ロジック修正は後続タスクの責務と解釈した。
- 未解決: コードフェンス内のコマンド抽出と、前置き行を候補にしない修正は未実施。それらの期待動作を固定する failing test も、担当範囲外の仕様決定を避けるため追加していない。
- 範囲外の発見: 英語散文は `sh -n` で構文上有効になりうるため、抽出修正では単に「最初の shell-valid 行」を選ぶだけでは前置き誤検出を防げない。
