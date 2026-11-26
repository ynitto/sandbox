### Goalie と change-watch の不具合修正

- Goalie の Copilot フックは、`.dot/` が未移行の目標で移行案内付きの拒否結果を返す。
- change-watch の eval は、実行対象にケースが0件のものがある場合に失敗の終了コードを返す。
- GitLab MR の IaC 検査は、差分内に Unicode 行区切り文字が含まれていても正しい指摘行を報告する。
