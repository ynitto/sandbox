# t2: 実行環境の macOS 固有事実 — 採取結果

採取環境: Darwin 25.5.0 (arm64, RELEASE_ARM64_T8132) / `uname -a`:
```
Darwin Mac 25.5.0 Darwin Kernel Version 25.5.0: Mon Apr 27 20:41:26 PDT 2026; root:xnu-12377.121.6~2/RELEASE_ARM64_T8132 arm64
```

## 1. `/tmp` は `/private/tmp` へのシンボリックリンク

```
$ ls -ld /tmp
lrwxr-xr-x@ 1 root  wheel  11 May 21 17:57 /tmp -> private/tmp

$ readlink /tmp
private/tmp

$ python3 -c "import os; print(os.path.realpath('/tmp'))"
/private/tmp
```

**含意**: `tempfile.mkdtemp()` 等で得たパスは `/var/folders/.../T/...`（`TMPDIR` 経由）または `/tmp/...` 表記のことがあり、
`os.path.realpath()` を通すと `/private/...` 側に正規化される。文字列比較やパス prefix 判定を素の文字列で行うと、
同一ディレクトリを指しているのに不一致と誤判定しうる。Linux では `/tmp` は多くの場合実ディレクトリでこの二重表記問題は起きない。

## 2. git のバージョンとデフォルトブランチ名

```
$ git --version
git version 2.50.1 (Apple Git-155)
```

- `git config --get init.defaultBranch`（このユーザーの実効設定）: **`main`**
  - `--show-origin` で確認すると、出所は **ユーザーの `~/.gitconfig`（global）ではなく、
    Xcode Command Line Tools が同梱するシステム設定ファイル**:
    `file:/Library/Developer/CommandLineTools/usr/share/git-core/gitconfig` → `init.defaultBranch=main`
  - `git config --system --get init.defaultBranch` は空（システムスコープの `/etc/gitconfig` 等には無い。
    CLT 同梱の gitconfig は `git config --system` が探す標準パスとは別に、Apple Git バイナリに
    ハードコードされた「組み込み system config」として読み込まれるため、`--show-origin` でしか出所が見えない点に注意）。
- **`init.defaultBranch` が一切設定されていない状態での git 自身の既定値**（`HOME`・`GIT_CONFIG_GLOBAL`・
  `GIT_CONFIG_NOSYSTEM=1` で全設定を遮断して検証）: **`master`**

**含意**: 「macOS だから `main`」ではなく、**Xcode Command Line Tools がインストールされた macOS だから `main`**
というのが正確な因果。CLT 未導入の macOS、あるいは `GIT_CONFIG_NOSYSTEM=1` や `HOME` を隔離するテスト実行環境
（このリポジトリの `test_kiro_flow.py` は `os.chdir(tempfile.mkdtemp(...))` はするが `HOME` は変えていない）では
git 本来の既定値である `master` に戻りうる。ブランチ名をハードコードするコードはこの前提差で環境依存の失敗を起こす。

## 3. `sed` / `readlink` / `stat` は BSD 系（GNU ではない）

```
$ type -a sed readlink stat
sed is /usr/bin/sed
readlink is /usr/bin/readlink
stat is /usr/bin/stat
```

- `sed --version` / `readlink --version` / `stat --version` はいずれも `illegal option -- -` で失敗
  （BSD 版は `--version` を認識しない。GNU sed/readlink/stat なら version 文字列を返す）。
- `stat -c "%n" /tmp` は `illegal option -- c` で失敗。BSD stat は `-f <format>` 形式のみ対応（GNU の `-c` は非対応）。
- `readlink -f /tmp` はこの macOS の BSD readlink では **動作する**（`/private/tmp` を返す）。
  ただし `-f` の解釈は GNU 版と完全互換ではない古い BSD 系実装もあるため、`-f` の存在に依存しすぎない方が安全。
- `sed -i` はインプレース編集に**拡張子引数が必須**（GNU は省略可）。引数省略時は誤動作/エラーになる:
  ```
  $ sed -i 's/hello/bye/' file        # BSD: エラー（次の引数をバックアップ拡張子として食われる）
  $ sed -i '' 's/hello/bye/' file     # BSD: 空文字列を拡張子として明示すれば動作
  ```
- Homebrew の `coreutils` フォーミュラ自体は `/opt/homebrew/opt/coreutils` に存在するが、
  `g` プレフィックス版（`gsed`/`greadlink`/`gstat`）は `PATH` 上に見つからず、`gnubin` も `PATH` に含まれていない。
  → この環境では `sed`/`readlink`/`stat` は無条件に `/usr/bin` の BSD 版に解決される。

**含意**: 自己修復ロジックや補助スクリプトが `sed -i 's/.../.../ ' file`（拡張子省略）や `stat -c` を使っていれば、
このような素の macOS 環境で確実に失敗する。GNU/BSD 両対応にするなら Python 標準ライブラリ
（`re`, `os.stat`, `pathlib`）で書き換えるのが最も確実。

## 4. umask とファイルモードの既定値

```
$ umask
022

$ touch plainfile && mkdir plaindir
$ stat -f "%Sp %Lp" plainfile
-rw-r--r-- 644
$ stat -f "%Sp %Lp" plaindir
drwxr-xr-x 755
```

- umask `022` により、通常ファイルの既定モードは `666 & ~022 = 644`、ディレクトリは `777 & ~022 = 755`。
- **git の loose object ファイルは umask に関わらず `0444`（読み取り専用）で作成される**。
  これは t1 の調査で判明した実際の障害原因と直結する事実: `test_kiro_flow.py` の `_zero_loose_objects`
  ヘルパーが `open(p, "wb").close()` で loose object を 0 バイトへ切り詰めようとすると、
  書き込み権限が無いため `PermissionError` になる（同ヘルパーは既に `os.chmod(p, 0o644)` を
  切り詰め前に追加済みで対処されている — 詳細は `../t1/prior_fix_commit_0cf9c59.diff` 参照）。
  Linux 環境でも git の loose object 権限は同じ 0444 だが、root 実行や既存 CI イメージの umask 次第で
  顕在化タイミングが異なることがある点は留意。
- `mktemp` / `mktemp -d`（`mkstemp(2)` 経由）は umask に関係なく常に `0600` / `0700` で作成する
  （umask 由来の 644/755 とは別枠。テストコードが一時ファイル権限を umask から類推していると誤る）。

## 検証内容と結果

- 上記すべてのコマンドをこのタスクの実行環境（採取対象と同一の worworkstation マシン）で実際に実行し、
  出力をそのまま記載した（伝聞・推測ではなく実測）。
- 参考として、本 run の完了条件コマンドを対象ワークスペースで実行し、現状を確認した:
  ```
  $ cd <workspace>/sandbox && python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q
  900 passed in 120.30s (exit code 0)
  ```
  t1 の調査（`../t1/SUMMARY.md`）が報告している通り、対象の git 自己修復テスト4件は同一作業ブランチの
  先行コミット（`0cf9c59`, `5681a20`）で既に修正済みであり、スイート全体は現時点で green。

## 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 本タスクの scope は「macOS 固有事実の採取」のみであり、コード修正・テスト修正は行っていない
  （実施済み修正のレビューや追加修正は t4 以降・t9〜t14 の役割と判断し、範囲外とした）。
- **前提**: 「umask とファイルモードの既定値」は、(a) OS 標準の umask 由来の既定モード、(b) git の loose
  object が umask に関係なく 0444 になる事実、(c) `mktemp` が umask を無視して 0600/0700 にする事実、の
  3点を含めて報告した。t1 の実際の失敗原因（git loose object の 0444）に直結する情報のため厚めに記載した。
- **未解決事項**: `init.defaultBranch=main` が「ユーザー個人の設定」ではなく「Xcode Command Line Tools が
  同梱するシステム gitconfig」由来である点は、他の macOS 実行環境（CLT バージョン差・CLT 未導入環境）でも
  同じ値になるとは限らない。この run の実行環境固有の観測であり、一般化するなら複数マシンでの追試が望ましい
  （本タスクの scope 外のため実施していない）。
- **範囲外で見つけた問題**: 本タスクの調査中に、t1 の成果物から「対象の4件の失敗は既に同一ブランチの
  先行コミットで修正済みで、完了条件のテストスイートは現時点で 900 passed / exit 0」という事実を把握した。
  自分でも完了条件コマンドを実行し同じ結果（900 passed, exit 0）を確認済み。追加の根本原因分析・修正が
  本当に必要かどうかの判断（t4 以降のタスクを続行するか、run 全体を早期終了すべきか）は評価役・後続タスクの
  判断に委ねる。
