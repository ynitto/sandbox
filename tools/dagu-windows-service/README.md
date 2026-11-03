# dagu-windows-service

[Dagu](https://github.com/dagucloud/dagu) を Windows のサービスとして登録する PowerShell スクリプト。
**プロキシ越しでしかインターネットに出られない PC** でも、そのまま入れて動かせる。

- Dagu 本体（`dagu start-all`。Web UI とスケジューラ）を自動起動のサービスにする。落ちたら 10 秒後に起こし直す
- 本体は GitHub の配布物を取り、`checksums.txt` でハッシュを確かめてから置く
- サービスの包みには [WinSW](https://github.com/winsw/winsw) を使う（Dagu 公式のインストーラと同じ置き方）
- Windows PowerShell 5.1 と PowerShell 7 のどちらでも動く

## 入れ方

管理者の PowerShell で実行する。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

```powershell
.\tools\dagu-windows-service\install-dagu-service.ps1
```

終わると `http://127.0.0.1:8080` で Web UI が開く。DAG は `C:\ProgramData\Dagu\dags\` に置く。

| 置き場所 | 中身 |
|---|---|
| `C:\Program Files\Dagu\` | `dagu.exe`・サービスの包み（`dagu-service.exe`）とその定義（`dagu-service.xml`） |
| `C:\ProgramData\Dagu\` | Dagu の設定・DAG・実行履歴・ログ（Dagu の `DAGU_HOME`） |
| `C:\ProgramData\Dagu\logs\service\` | サービスとしての標準出力・標準エラー（起動しないときはまずここ） |

手でコマンドを叩くときは、サービスと同じ `DAGU_HOME` を合わせる。

```powershell
$env:DAGU_HOME = 'C:\ProgramData\Dagu'; & 'C:\Program Files\Dagu\dagu.exe' status my-dag
```

もう一度実行すると上書きで更新になる（版上げ・プロキシの変更）。DAG と履歴は残る。前の定義は
`dagu-service.xml.<日時>.bak` として残す。

## プロキシ

プロキシは 2 か所に効かせる。

1. **このスクリプトのダウンロード**（Dagu 本体・`checksums.txt`・WinSW）
2. **サービスとして動く Dagu と、Dagu が流すステップ**（`git`・`curl`・`pip`・`npm` など）

サービスは既定で LocalSystem として動くので、ふだん使っているユーザーのプロキシ設定も環境変数も
見えない。そこで `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` をサービスの環境に書き込む。Dagu はステップに渡す
環境変数を絞るので、`DAGU_ENV_PASSTHROUGH` でこの 3 つをステップにも渡す。

### プロキシの決まり方

`-Proxy` を付けなければ、次の順に探して最初に見つかったものを使う。何を使ったかは実行時に表示する。

1. いまの PowerShell の環境変数 `HTTPS_PROXY` / `HTTP_PROXY`（と `NO_PROXY`）
2. インターネット オプションのプロキシ サーバー（実行したユーザーのもの。「例外」も読む）
3. WinHTTP のプロキシ（`netsh winhttp show proxy` で見えるもの）

決め打ちするなら `-Proxy`、使わないなら `-Proxy none`。

```powershell
.\install-dagu-service.ps1 -Proxy http://proxy.example.co.jp:8080 -ProxyBypass .example.co.jp,10.0.0.0/8
```

### プロキシを通さない宛先（NO_PROXY）

`localhost`・`127.0.0.1`・`::1` と、Dagu の待ち受けアドレスは必ず入れる。社内の GitLab やファイルサーバーなど、
直接つなぐ宛先は `-ProxyBypass` で足す。インターネット オプションの書き方は次のように書き換える。

| インターネット オプション | NO_PROXY |
|---|---|
| `*.example.co.jp` | `.example.co.jp` |
| `10.*` / `192.168.*` / `192.168.1.*` | `10.0.0.0/8` / `192.168.0.0/16` / `192.168.1.0/24` |
| `<local>`（ドットのないホスト名） | 表せないので外す。必要な名前は `-ProxyBypass` に足す |
| `intra*` のような途中の `*` | 表せないので外す（警告を出す） |

### 認証付きのプロキシ

ユーザー名とパスワードは `-ProxyCredential` で渡す（ダイアログで入力する）。

```powershell
.\install-dagu-service.ps1 -Proxy http://proxy.example.co.jp:8080 -ProxyCredential (Get-Credential)
```

- パスワードはサービスの定義（`dagu-service.xml`）にプロキシの URL として書く。このファイルは
  SYSTEM と Administrators だけが読めるようにしてある。パスワードを変えたら、もう一度実行して書き直す
- ステップにも同じ URL が渡る。DAG の中でプロキシの値をログに出さないこと
- 画面にはパスワードを `****` に伏せて出す
- `-ProxyUseDefaultCredentials` はログオン中の Windows 資格情報（NTLM / Kerberos）でプロキシに通る。ただし効くのは
  このスクリプトのダウンロードだけで、Dagu 本体は Windows 統合認証のプロキシを通れない。
  統合認証しか受け付けないプロキシなら、手元で認証を肩代わりする中継（[px](https://github.com/genotrance/px) など）を立て、
  `-Proxy http://127.0.0.1:3128` のようにそちらを指す

### 使えないもの

- **自動構成スクリプト（PAC）**: サービスでは使えない。PAC だけが設定されているときは警告を出して直結にするので、
  `-Proxy` でプロキシを指定する
- **SOCKS**: インターネット オプションの `socks=` は外す

### 社内の証明書（TLS の検査をするプロキシ）

Dagu 本体は Windows の証明書ストアを使うので、社内のルート証明書がコンピューターに入っていればそのまま通る。
ステップで動かす `git`・Python・Node.js などは自分の証明書の束を持つものがあるので、それぞれに設定する
（例: `git config --system http.sslBackend schannel`）。

## インターネットに出られない PC

配布物を別の PC で取ってきて、ファイルで渡す。`dagu_<版>_windows_amd64.tar.gz` と同じフォルダに
`checksums.txt` を置くとハッシュを確かめる。

```powershell
.\install-dagu-service.ps1 -DaguArchive .\dagu_2.0.0_windows_amd64.tar.gz -WinSWPath .\WinSW-x64.exe -Proxy none
```

- Dagu: <https://github.com/dagucloud/dagu/releases>
- WinSW: <https://github.com/winsw/winsw/releases>（既定 v2.12.0。32 ビット Windows なら `WinSW-x86.exe`）

## 確かめるだけ

`-DryRun` は何も変えず、決まったプロキシとサービスの定義を表示する。管理者でなくてよい。

```powershell
.\install-dagu-service.ps1 -DryRun
```

## 外す

```powershell
.\install-dagu-service.ps1 -Uninstall
```

サービスの登録だけを外す。`C:\Program Files\Dagu\` と `C:\ProgramData\Dagu\`（DAG・履歴）は残すので、要らなければ手で消す。

## 指定できるもの

| 指定 | 既定 | 意味 |
|---|---|---|
| `-Version` | `latest` | 入れる版（例 `v2.0.0`） |
| `-InstallDir` | `C:\Program Files\Dagu` | 本体とサービスの包みの置き場所 |
| `-DaguHome` | `C:\ProgramData\Dagu` | 設定・DAG・履歴の置き場所 |
| `-ServiceName` | `Dagu` | サービス名 |
| `-HostAddress` / `-Port` | `127.0.0.1` / `8080` | Web UI の待ち受け。他の PC から開くなら `0.0.0.0`（認証の設定も忘れずに） |
| `-Proxy` | `auto` | `auto`（探す）/ `none`（使わない）/ `http://host:port` |
| `-ProxyBypass` | — | プロキシを通さない宛先。カンマ区切り |
| `-ProxyCredential` | — | 認証付きプロキシのユーザーとパスワード |
| `-ProxyUseDefaultCredentials` | — | ダウンロードだけ Windows 資格情報でプロキシ認証する |
| `-EnvPassthrough` | — | プロキシのほかにステップへ渡す環境変数の名前。カンマ区切り |
| `-DaguArchive` / `-WinSWPath` | — | 手元の配布物を使う |
| `-WinSWVersion` | `v2.12.0` | 取ってくる WinSW の版 |
| `-DryRun` | — | 何も変えずに表示だけ |
| `-Uninstall` | — | サービスの登録を外す |

## 気をつけること

- サービスは LocalSystem で動く。ユーザーのフォルダにある CLI やその認証情報を DAG から使うなら、
  サービスの「ログオン」をそのユーザーに変える（`services.msc` → Dagu → ログオン）。プロキシの設定はそのまま効く
- プロキシを使うとき、Dagu の設定ファイル（`C:\ProgramData\Dagu\config.yaml`）の `env_passthrough` は効かなくなる
  （サービスの環境に書いた指定が置き換える）。ほかにステップへ渡したい環境変数は `-EnvPassthrough` で足す

  ```powershell
  .\install-dagu-service.ps1 -Proxy http://proxy:8080 -EnvPassthrough GITLAB_TOKEN,PYTHONUTF8
  ```
- 他の PC から Web UI を開けるようにするなら、Windows ファイアウォールでポートを開け、Dagu の認証を設定する

## テスト

Windows で実行する（CI でも Windows PowerShell 5.1 と PowerShell 7 の両方で回している）。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\dagu-windows-service\test\install-dagu-service.tests.ps1
```
