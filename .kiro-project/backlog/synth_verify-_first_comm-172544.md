## synth_verify-_first_comm-172544: synth_verify の _first_command_line がコードフェンス内のコマンドを拾えず、LLM が前置きを付けると verify 合成が失敗する問題を修正する
- status: proposed
- source: enqueue
- priority: 5
- verify: `python3 -m pytest tools/kiro-project/tests -q -k first_command_line                                                       [100%] 15 passed, 564 deselected in 0.23s）`
- retries: 0
- note: 再現: acceptance/verify の自然文（accept:）を synth_verify が LLM に投げると、cwd が実リポジトリの場合 claude はツールで調査した上で『…を確認できたので、コマンドを確定します。』という日本語の前置き＋コードフェンス付きでコマンドを返す。_first_command_line は先頭の非空・非コメント行で確定するため前置きの散文を拾い、_looks_like_shell_command が全角句読点を検出して不採用にする。attempts=2 の両方が同じ形で弾かれ、空文字＝未合成になり、charter の acceptance では no-acceptance となって backlog へ分解される前に人へ差し戻される（実害: バックログが一切生成されない）。
- assess: c=2 r=2 a=1
