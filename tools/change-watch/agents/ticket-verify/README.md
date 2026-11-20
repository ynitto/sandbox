# ticket-verify

GitLab と Redmine のチケット対応で使う共通のスモークテスト実行ヘルパーです。change-watch のエージェント登録に使う設定例を持たないため、インストーラのエージェント一覧には出ません。Python 3.9 以降の標準ライブラリで動きます。Dagu 例を使う場合は、運用者が `ticket_verify.py` を `~/.local/lib/ticket-verify/` にコピーし、DAG のパスを実環境に合わせます。

## 実行方法

対象ツールを実際に起動して結果を確認するコマンドを、UTF-8 の JSON ファイルに引数配列で用意します。以下では運用者が用意した `/opt/team/smoke-project-tool` が、対象ツールの動作を確かめて成否を終了コードで返します。

```json
["/opt/team/smoke-project-tool", "--check"]
```

```sh
export TICKET_VERIFY_ALLOW='[["/opt/team/smoke-project-tool","--check"]]'
python3 ticket_verify.py run --command-file verify-command.json --cwd /home/you/work/project \
  --out evidence.json --md-out evidence.md
python3 ticket_verify.py gate --evidence evidence.json
```

- `TICKET_VERIFY_ALLOW` は argv の接頭辞を並べた JSON 配列です。どれか一つと引数単位で前方一致するコマンドだけを実行します。未設定・空・形式不正・不一致なら起動しません。接頭辞の後ろには引数を付けられるため、運用者が管理する専用のスモークテストを指定し、任意の処理を実行できるシェルやインタープリターの許可は避けてください。
- シェルを介さず、標準入力を閉じ、`PATH`・`HOME`・ロケール・一時ディレクトリなどの限られた環境変数だけを子プロセスに渡します。`TICKET_VERIFY_TIMEOUT_SEC` は既定 120 秒、有効範囲は 1〜600 秒です。整数以外は既定値を使います。
- トークン・パスワード・API キーなどを示す名前の環境変数の値は、証拠のコマンドと出力からマスクします。検証コマンドには資格情報を指定せず、作業ディレクトリやホームから資格情報を読まないテストを用意してください。実行権限とファイルアクセスは Dagu の実行ユーザーに従います。
- 実行ごとに新しい出力先を用意し、証拠ファイルはワークフロー側で管理します。GitLab のエージェント CLI には対応に必要な権限を与え、チケットへの書き込み権限は投稿ステップに委ねます。

## 証拠と判定

`run` は終了コード 0 かつ時間内に終了した場合にだけ `verified: true` とし、終了コード 0 を返します。成功時の Markdown には「検証証拠（スモークテスト）」見出し、コマンド、終了コード、出力が入り、完了文の直後に追加できます。

証拠 JSON の version は 1 です。`command` はマスク済みの argv、`exit_code` は終了コード（取得できなければ `null`）、`timed_out` は時間切れの有無、`ran_at` は検証処理の開始時刻（UTC）、`verified` は検証の成否、`output` は標準出力・標準エラーをまとめてマスクした末尾 40 行・4000 字です。

時間切れ、非 0 終了、起動失敗、有効な許可リストとの不一致では `verified: false` の JSON を書き、終了コード 1 を返します。許可リストの未設定・空・形式不正、出力先のシンボリックリンク、パスの衝突などの設定エラーでは 2 を返します。出力先のシンボリックリンクはリンク自体を削除して実行を拒否します。失敗時は Markdown を生成しません。

`gate` は標準出力に `{"complete": true, "reason": "検証済み"}` のような JSON を 1 行で出します。証拠 JSON の形式と整合性を確認し、`version: 1`、`verified: true`、`exit_code: 0`、`timed_out: false` が揃った場合に完了可と判定します。証拠が見つからない、読めない、またはこれらの条件を満たさない場合は `complete: false` を返します。正常に判定した場合のプロセス終了コードは、判定結果にかかわらず 0 です。チケットへの完了報告には `complete: true` と証拠 Markdown が揃っていることを確認し、証拠が揃わない場合は差し戻し文に「〔仮説（未検証）〕」を付けます。

証拠の信頼境界は、ワークフローの一時ディレクトリと Dagu の実行ユーザーです。証拠の改ざんや使い回しの検知は `gate` の対象外です。使い回しを防ぐため、`run` は開始時に既存の証拠 JSON と Markdown を削除してから設定を検証します。

`TICKET_VERIFY_MAX_EVIDENCE_AGE_HOURS` に正の有限な時間数（例 `0.5`）を設定すると、`gate` はタイムゾーン付きの `ran_at` から経過時間を確認します。上限を超えた証拠、未来の時刻、時刻の欠落や形式不正、上限の設定不正は `complete: false` になります。未設定時は鮮度を確認しません。
