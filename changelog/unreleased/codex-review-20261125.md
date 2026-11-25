### devkit と agent-dots の不具合修正

- devkit の `http` は、301・302・303 のリダイレクトをたどるときも HEAD リクエストを維持する。
- agent-dots は、旧形式の見直し報告に初出日時がなければ記録し、壊れた報告があっても `review` と `status` を続ける。
