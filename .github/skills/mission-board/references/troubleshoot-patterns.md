# Troubleshoot Patterns

troubleshoot サブコマンドが参照する調査コマンド集。このファイルをプロジェクト固有の内容に差し替えることで、ドメインに合わせた診断手順をカスタマイズできる。

> **カスタマイズ方法**: このファイルを `.github/skills/mission-board/references/troubleshoot-patterns.md` に配置するとプロジェクト固有の内容が優先される。

---

## 調査深度の基準

| 層 | 内容 | コマンド例（Linux/macOS） | コマンド例（Windows/PowerShell） |
| -- | ---- | ------------------------- | -------------------------------- |
| 第1層 | 高レベルAPI | `systemctl status`, `nc -zv` | `Get-Service`, `Test-NetConnection` |
| 第2層 | 設定ファイル | `cat /etc/...`, `sysctl` | `Get-ItemProperty`, `Get-SmbServerConfiguration` |
| 第3層 | カーネル・ドライバー | `dmesg`, `lsmod` | `sc.exe query` |
| 第4層 | プロトコルレベル | `tcpdump`, `ss -tulnp` | RAW TCP socket |

権限不足時は代替コマンドに切り替える。同じコマンドを繰り返さない。

---

## 典型パターン（ネットワーク / インフラ）

| カテゴリ | Linux/macOS | Windows/PowerShell |
| -------- | ----------- | ------------------ |
| 疎通 | `ping`, `traceroute` | `ping`, `Test-NetConnection` |
| ポート | `nc -zv <host> <port>`, `ss -tulnp` | `Test-NetConnection -Port <n>` |
| DNS | `dig`, `nslookup` | `nslookup`, `nbtstat -A` |
| SMB/共有 | `smbclient -L` | `net view`, `Get-SmbShare` |
| サービス | `systemctl status`, `ps aux` | `Get-Service`, `sc.exe query` |
| FW | `iptables -L`, `ufw status` | `Get-NetFirewallRule` |
| ログ | `journalctl`, `tail /var/log/...` | `Get-EventLog`, `Get-WinEvent` |

---

## 典型パターン（ソフトウェア開発）

> このセクションはカスタマイズ例。プロジェクトに応じて内容を差し替える。

| カテゴリ | コマンド例 |
| -------- | ---------- |
| テスト失敗 | `npm test`, `pytest -v`, `go test ./...` |
| ビルドエラー | `npm run build`, `make`, `cargo build` |
| 依存関係 | `npm ls`, `pip check`, `cargo tree` |
| ログ確認 | `tail -f app.log`, `docker logs <container>` |
| プロセス | `ps aux \| grep <name>`, `lsof -i :<port>` |
