### GitLab Issue Hub Agent・changelog-collect・doc-lint の不具合修正

- GitLab Issue Hub Agent の直接入力の改行をそろえ、非常に長い保存期間でも期限を判定できるようにする。
- changelog-collect が集約先自身を取り込むことを防ぎ、同じ実行で追加した見出しをすべて重複判定に使う。
- doc-lint の評価器の診断を標準エラーに送り、JSON や SARIF の出力を保護する。
