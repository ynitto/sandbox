### reverse-spec と GitLab エージェントの不具合修正

- reverse-spec の差分検査で新たに除外したファイルを検出し、無効な単位の種類は最終検査で報告する。
- GitLab CI triage は、不正なページ番号を一覧の終端として扱わず、取得エラーを報告する。
- GitLab merge followup は、MR 説明文の HTML コメント内にある Issue 参照を下書きの対象から外す。
