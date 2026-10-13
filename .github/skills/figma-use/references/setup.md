# 配置と使い方（PowerShell / WSL2）

## エージェントごとの配置

このリポジトリの `install.py` がスキルを各エージェントのスキル置き場へコピーする
（置き場の対応はリポジトリの README の「AGENT_HOME パス対応表」と同じ）。

| エージェント | 導入 | スキルの置き場（`{skill_home}`） | connections.yaml（グローバル） |
|---|---|---|---|
| GitHub Copilot | `python install.py` | `~/.copilot/skills`（ワークスペースは `.github/skills`） | `~/.copilot/connections.yaml`（ワークスペースは `.github/connections.yaml`） |
| Claude Code | `python install.py --agent claude` | `~/.claude/skills`（ワークスペースは `.claude/skills`） | `~/.claude/connections.yaml` |
| Kiro | `python install.py --agent kiro` | `~/.kiro/skills` | `~/.kiro/connections.yaml` |
| OpenAI Codex | `python install.py --agent codex` | `~/.codex/skills` | `~/.codex/connections.yaml` |
| opencode | `python install.py --agent opencode` | `~/.config/opencode/skills` | `~/.config/opencode/connections.yaml` |

`figma-use` は全スキル導入（`--all-skills`）か、git-skill-manager で個別に取得する。手で置くときは
`figma-use/` フォルダごと `{skill_home}/` にコピーする（`SKILL.md` と `scripts/`・`references/` を同じ形で置く）。
CLI は置き場所から connections.yaml を探すので、フォルダの中身を分けて置かない。

Windows では `~` を `$env:USERPROFILE` に読み替える。WSL2 の中のエージェントは WSL 側のホーム（`/home/<user>`）を使い、
Windows 側の置き場とは別になる。

## 認証情報の設定

### PowerShell（Windows 11）

```powershell
# このセッションだけ（画面に表示せず入力）
$sec = Read-Host 'Figma token' -AsSecureString
$env:FIGMA_TOKEN = [System.Net.NetworkCredential]::new('', $sec).Password
```

恒久的にするなら、Windows の「環境変数を編集」画面でユーザー環境変数 `FIGMA_TOKEN` を作る。
connections.yaml には `token: ${FIGMA_TOKEN}` と書き、値は書かない。

### WSL2 Ubuntu

```bash
read -rsp 'Figma token: ' FIGMA_TOKEN && export FIGMA_TOKEN && echo
```

恒久的にするなら、Git 管理外のファイル（例: `~/.config/figma-use.env`、権限 600）に書いて `~/.bashrc` から読む。

## 最初に実行するコマンド

### PowerShell

```powershell
$F = "$env:USERPROFILE\.claude\skills\figma-use\scripts\figma_cli.mjs"
node $F doctor
node $F doctor --network 'https://www.figma.com/design/<FILE_KEY>/<NAME>?node-id=1-2'
node $F inspect 'https://www.figma.com/design/<FILE_KEY>/<NAME>'
node $F export 'https://www.figma.com/design/<FILE_KEY>/<NAME>?node-id=1-2' --out figma-output
```

URL は単一引用符で囲む（`&` や `;` をシェルに解釈させない）。

### WSL2 Ubuntu

```bash
F=~/.claude/skills/figma-use/scripts/figma_cli.mjs
node "$F" doctor
node "$F" doctor --network 'https://www.figma.com/design/<FILE_KEY>/<NAME>?node-id=1-2'
node "$F" inspect 'https://www.figma.com/design/<FILE_KEY>/<NAME>'
node "$F" export 'https://www.figma.com/design/<FILE_KEY>/<NAME>?node-id=1-2' --out figma-output
```

`/mnt/c/...`（Windows 側のフォルダ）へも出力できるが、ファイル数が多いと遅い。WSL 側に出力してから必要な物を移す。
ファイル名は Windows で開ける形にそろえてあるので、どちらで作っても両方で開ける。

## プロキシのある環境

環境変数 `http_proxy` / `https_proxy`（`no_proxy` も）が設定されていれば、CLI はそのプロキシを使う。
名前の大文字小文字は区別しない（`HTTPS_PROXY`・`https_proxy`・`Https_Proxy` のどれでもよい）。
同じ名前が複数の綴りで設定されていれば大文字の綴りを優先する。`doctor` がプロキシの行に使う先を表示する
（`user:pass@` のような認証情報は表示しない）。

Node.js の fetch は、そのままでは環境変数のプロキシを使わない。CLI はプロキシの指定を見つけると、
綴りを大文字にそろえ `NODE_USE_ENV_PROXY=1` を付けて自分自身を起動し直す（利用者が設定する必要はない）。
Node 24 のこの機能は 24.5 以降にある。

プロキシが TLS を中継して独自の証明書を使うなら、その CA 証明書を `NODE_EXTRA_CA_CERTS` に指定する。

```powershell
$env:https_proxy = 'http://proxy.example.co.jp:8080'
$env:NODE_EXTRA_CA_CERTS = 'C:\path\to\corp-ca.pem'   # 必要なときだけ
```

```bash
export https_proxy=http://proxy.example.co.jp:8080
export NODE_EXTRA_CA_CERTS=/path/to/corp-ca.pem       # 必要なときだけ
```

## キャッシュと出力の置き場

- キャッシュの既定: Windows `%LOCALAPPDATA%\figma-use\cache`、Linux・WSL `~/.cache/figma-use`
  （`$XDG_CACHE_HOME` があればその下）。`--cache-dir` か環境変数 `FIGMA_USE_CACHE_DIR` で変える。`doctor` が場所を表示する
- 出力（`--out`）をリポジトリの中に置くなら `.gitignore` に入れるかを決める。成果物にトークンは入らないが、
  デザインの画像や文言は入る
