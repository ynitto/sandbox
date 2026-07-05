---
name: kiro-projects
description: kiro-projects（自律バックログ消化ループ）を外部から操作するスキル。いま稼働中のプロセスが監視しているプロジェクト（フォルダ）を発見し（WSL/Windows のパス差を吸収）、バックログへのタスク投入・人の判断待ち（needs）の確認と指示・タスクの軌道修正（revise。実行中でも内容/依存/優先度の修正とフィードバック注入）・成果物（納品書）の検収・プロジェクト目標（charter）の確認と承認を CLI とファイル操作で支援する。「バックログに積んで」「kiro-projects にタスクを投げて」「判断待ちを確認して」「needs を見せて」「承認して」「保留して」「優先度を上げて」「タスクを直して」「やり方を変えさせて」「依存を付けて」「成果物を確認して」「納品物を見せて」「ループを回して/常駐させて」「稼働中のループに繋いで」「プロジェクトの目標を回して」「charter を確認して」などで発動する。kiro-projects の運用が含まれる場合に優先して選択する。
metadata:
  version: 1.2.0
  tier: experimental
  category: operations
  tags:
    - kiro-projects
    - backlog
    - loop-engineering
    - human-in-the-loop
    - operations
---

# kiro-projects — 自律ループの外部操作

`kiro-projects`（`backlog/` を優先順位付け→実行→`verify` ゲート→収束させる制御層）を、**人間の運用側**
から操作するスキル。ループ本体は `kiro-projects run` が担い（`<project>/charter.md` があれば run が自動で
目標駆動になる＝専用 project コマンドは無い）。本スキルが担うのは、その**外部からのアクション**である:

| モード | 発動フレーズ | やること |
|--------|------------|---------|
| **投入（enqueue）** | 「バックログに積んで」「タスクを投げて」「○○をやらせて」 | `enqueue`（完了条件は `--verify`／書けなければ `--accept`/`--verify-template`）。プロジェクトは `--project` |
| **判断（decide）** | 「判断待ちを確認して」「needs を見せて」「承認して」「保留して」「優先度を上げて/下げて」 | `needs` 確認 → フィードバック記入 or `approve`/`hold`/`reprioritize` |
| **軌道修正（revise）** | 「タスクを直して」「やり方を変えさせて」「依存を付けて/外して」「いまやってるのを○○の方式でやり直させて」 | `revise <id>` でフィールド置換＋`--feedback` 注入（実行中でも現在の試行を確定せず積み直す） |
| **検収（deliver）** | 「成果物を確認して」「納品物を見せて」「何が完了した?」 | `DELIVERY.md` と `archive/<id>.md` の納品書を読む |
| **目標（charter）** | 「プロジェクトの目標を回して」「charter を確認して」「収束したか」 | `charter.md` 確認・`run`（charter 駆動）起動・milestone の承認/続行 |
| （補助）**起動/状態** | 「回して」「常駐させて」「状態を見せて」 | ループ起動（常駐含む）・順位確認・stats/journal 確認 |
| （補助）**方針** | 「prod は手動に」「これを最優先に固定」 | `policy.md` に `deny`/`pin`/`defer`/`offload`/`gate`/`protect` を記述 |

ループの**自律消化そのもの**を依頼されたら、それは kiro-projects 本体の仕事なので「起動/状態」モードで
`run` を起こす（charter があれば run が自動で目標駆動になる）。本スキルは投入・判断・検収・目標確認という**人の接点**に徹する。

---

## 構成（プロジェクト > バックログ）と CLI 解決

- `python3`（標準ライブラリのみ）。`kiro-flow` / `kiro-cli` はループ実行時のみ必要（投入・判断・検収だけなら不要）。
- **プロジェクトが最上位コンテナ**。実体は `<container>/projects/<name>/`（既定 `./.kiro-projects/projects/default/`）に
  `backlog/`・`needs/`・`decisions/`・`archive/`・`charter.md`・`policy.md`・`DELIVERY.md` などが**プロジェクト毎**に集約される。
  **複数プロジェクトが併存**し、操作対象は **`--project <name>`**（既定 `default`）で選ぶ。
- CLI を解決する（PATH 優先、無ければリポジトリ内のスクリプト）:

```bash
KA="$(command -v kiro-projects || echo 'python3 tools/kiro-projects/kiro-projects.py')"
$KA --help
```

- **作用先は cwd を当てにしない。** 操作対象は **いま稼働しているプロセスが監視しているプロジェクト**でなければ
  意味がない。必ず下記「稼働インスタンスへの接続」で実体を発見し、CLI には **`--root <container> --project <name>`** を付ける。
  > ⚠ 発見レコードの `root`（= `<container>/projects/<name>`）を `--root` に渡してはいけない（`projects/<name>` が
  > 二重に付く）。**`--root` にはレコードの `container`、`--project` にはレコードの `project`** を渡す。
- タスク書式の正典は `tools/kiro-projects/backlog.md.example`、charter は `charter.md.example`、運用詳細は同 `README.md`。

---

## 稼働インスタンスへの接続（WSL / Windows）

**最重要**: 投入・needs 記入・検収・目標確認は、**稼働中プロセスが実際に見ているプロジェクト**に対して行う。
`run`（特に `--watch`）中のプロセスは監視対象を `~/.kiro-projects/instances/<pid>.json`
（`$KIRO_PROJECTS_HOME` で変更可）に登録している。

### 手順

1. **実行環境を判定する**（スキル＝あなたが今どこで動いているか）:
   - WSL/Linux: `/proc/version` に `microsoft` を含む、または `$WSL_DISTRO_NAME` あり → **WSL**。
   - それ以外で `wsl.exe` が使える → **Windows**（プロセスは WSL 側の可能性が高い）。

2. **稼働インスタンスを発見する**（`instances --json` で取得）:

   ```bash
   $KA instances --json                                    # 自分も WSL/プロセスと同じ OS の場合
   wsl.exe -d <distro> -- bash -lc 'kiro-projects instances --json'   # 自分は Windows・プロセスは WSL
   ```

   出力は配列。各レコードに `pid` / **`container`**（`--root` に渡す値）/ **`project`**（`--project` に渡す名）/
   `root`（= `<container>/projects/<project>`・参照用）/ `backlog` / `needs` / `decisions` / `archive` / `policy` /
   `delivery` / `journal`（いずれも直接読み書きできる絶対パス）/ `runtime`（`linux`/`wsl`/`windows`/`darwin`）/
   `wsl_distro` / WSL なら `root_windows` / `host` が入る。**複数プロジェクトが回っていれば複数レコード**になる。

   **別ホストも横断したい**ときは共有レジストリ（複数ホストから見える1ディレクトリ）を指す:
   ```bash
   $KA instances --json --registry /mnt/shared/kiro-registry   # env KIRO_PROJECTS_REGISTRY でも可
   ```
   別ホストのレコードは `host` が異なり `@host(remote)` と表示される。**別ホストの操作（stop 等）はそのホスト上で行う**
   （リモート PID へシグナルは送れない）。読み書きは共有ファイルシステム越しに可能。

3. **対象を選ぶ**: 生存インスタンスが 1 つならそれ。複数（プロジェクト違い）あれば **`project` 名（と root）を提示して
   選ばせる**。0 件なら「稼働中の kiro-projects が無い」ことを伝え、起動するか対象プロジェクトを確認する。

### CLI の組み立てと読み書きの原則（境界をまたがない）

発見したレコードから、CLI コマンドは必ず **`--root <container> --project <project>`** で組む。ファイルは
レコードの絶対パス（`backlog`/`needs`/`archive` …）を直接読み書きする。**プロセスと同じ OS 側で操作する**のが最も確実:

```bash
# 発見レコードから（プロセスと同じ OS の場合）
CONT=<レコードの container>; PROJ=<レコードの project>
$KA needs --root "$CONT" --project "$PROJ"
$KA enqueue --root "$CONT" --project "$PROJ" --title "…" --verify '…'

# Windows 側のスキルから WSL 側を操作する（推奨パターン）
DISTRO=<wsl_distro>
wsl.exe -d "$DISTRO" -- bash -lc "kiro-projects needs --root '$CONT' --project '$PROJ'"
wsl.exe -d "$DISTRO" -- bash -lc "cat > '<レコードの backlog>/T42.md'" < /tmp/T42.md   # ファイル直接投入
```

どうしても **Windows 側のツールで直接** 読み書きするときだけパスを変換する: レコードの `root_windows`、または WSL 内で
`wslpath -w <wslパス>`（→Windows）/ `wslpath -u '<winパス>'`（→WSL）。`/mnt/c/...` ↔ `C:\...` は `wslpath` が変換する。

> あなたが WSL・プロセスも同一ディストロなら変換は不要。`container`/`project` をそのまま渡し、ファイルも直接編集する。

---

> **以降のモードの前提**: まず接続で `container` / `project` / 各パスを発見しておく。以下の例の
> `<CONT>`＝container、`<PROJ>`＝project に読み替え、CLI には `--root <CONT> --project <PROJ>` を付ける。
> プロセスが WSL で自分が Windows なら各コマンドを `wsl.exe -d <distro> -- bash -lc '…'` で包む。

## モード1: バックログ投入（enqueue）

**最短は `enqueue` コマンド**（検証済みで `backlog/<id>.md` を生成。id 自動）。完了条件は `--verify` が最良だが、
**verify を書けないときは `--accept`（自然言語→実行時に合成）か `--verify-template`（決定的展開）**で代替できる:

```bash
$KA enqueue --root <CONT> --project <PROJ> --title "利用規約に最終更新日を表示" \
  --verify 'grep -q 最終更新 web/terms.html' --priority 1
# verify を書けないとき:
$KA enqueue --root <CONT> --project <PROJ> --title "概要を追加" --accept "README に ## 概要 の見出しがある"
$KA enqueue --root <CONT> --project <PROJ> --title "規約に最終更新" \
  --verify-template 'file-contains :: web/terms.html :: 最終更新'
echo '{"title":"X","verify":"make test","after":"T1"}' | $KA enqueue --root <CONT> --project <PROJ> --json
cp task.md <レコードの backlog の親>/inbox/   # inbox ドロップ口に置くだけでも run/watch が取り込む
```

外部ソース（GitHub issue 抽出・メール 等）からの取り込みは `--json` 形式に整形して `enqueue --json` へパイプする。
未指定プロジェクトは `default` を作成して積む。ファイルを直接書く場合は**レコードの `backlog` パス**へ
`<id>.md`（1ファイル＝1タスク。id はファイル名の stem。正典は `backlog.md.example`）を書く:

```bash
cat > <レコードの backlog>/T42.md <<'MD'
## T42: 利用規約ページに最終更新日を表示する
- status: ready
- source: human
- priority: 1
- verify: `grep -q "最終更新" web/terms.html`
- retries: 0
MD
```

フィールド: `status`（`ready`/`inbox`/`draft`/`blocked`）・`priority`（整数・大ほど高）・`verify`（**done の唯一の根拠**）・
`after`（依存 DAG）・`review: human`（検収ゲート）・`level`/`track`（タスク単位の自律度）・`expect: changes`（偽 done 対策）・
`followup`（done 時に派生生成）。

### 鉄則（投入時に必ず守る）

1. **完了条件を必ず持たせる。** `--verify`（最良）か `--accept`/`--verify-template`。いずれも無いと triage で人へ戻る
   （`accept`/`verify_template` があれば最終的に concrete な verify に変換され、done は verify のみが根拠の鉄則は不変）。
2. **曖昧で人間判断が要るタスクは積まず、先にユーザーに確認する。** ループは「機械検証できる作業」の箱。シェルで
   検証できないものは `--review human` で人承認（検収ゲート）に回す。
3. 書きかけは `status: draft` にして消化・誤発火を防ぐ（書き終えたら `ready` に上げる）。

投入後の確認（消化しない）: `$KA triage --root <CONT> --project <PROJ>`。

---

## モード2: 人の判断（decide）

タスクが判断待ちになると `needs/<id>.md`（通知＋**フィードバック記入欄**）が生成される。**プロジェクト毎に閉じる**
（別プロジェクトの判断は混ざらない）。

```bash
$KA needs --root <CONT> --project <PROJ>     # blocked / 検収待ち / acceptance 未定義 を一覧
```

`needs/<id>.md` を読んで文脈（失敗内容・必要な判断）を把握し、ユーザーに要約して**指示を仰ぐ**（判断を勝手に決めない）。

**(a) フィードバック往復**: `needs/<id>.md` の「## Decision Outcome」欄（MADR 互換。旧「## フィードバック」も可）に方針を書き、確定チェックを `- [x]` にして保存。
次パス（`--watch` なら次 poll）で拾われ次の実行に反映される。**`[x]` の時だけ確定**（書きかけ保存では発火しない）。

**(b) サブコマンドで明示操作**（決定は `decisions/<id>.md` に記録）:

```bash
$KA approve <id> --root <CONT> --project <PROJ> --reason "テスト側の期待値を修正して再実行"  # 承認して積み直し
$KA hold    <id> --root <CONT> --project <PROJ> --reason "本番反映は手動"                    # 保留（policy deny 追加）
$KA reprioritize <id> --pin   --root <CONT> --project <PROJ> --reason "今日中に必要"          # 最優先
$KA reprioritize <id> --defer --root <CONT> --project <PROJ> --reason "後回し"
```

## モード2.5: 能動の軌道修正（revise）

needs は**ループが人へ回した時**の受動の口。対して revise は、**人が気づいた時点で**タスクの内容・
依存・優先度を修正し、指示（feedback）を次の実行へ必ず届ける能動の口。典型は「実行中の作業の方向が
違う」と気づいた時（例: ローカルサーバで e2e を始めた →「実サーバに配備して実施」へ即修正）。

```bash
# 実行中でも軌道修正: 現在の試行は確定されず（done にならず）、修正内容で積み直される
$KA revise <id> --root <CONT> --project <PROJ> \
  --feedback "e2e はローカルサーバでなく実サーバに配備して実施すること" --reason "軌道修正"

# フィールドの置換（'' / none で削除。after の循環は拒否される）
$KA revise <id> --after "T1,T2" --priority 5 --root <CONT> --project <PROJ> --reason "依存と優先度を整理"
$KA revise <id> --verify "curl -fsS https://staging.example.com/health" --root <CONT> --project <PROJ> \
  --reason "検証を実サーバ基準に変更"
```

- 置換できるフィールド: `--title` `--priority` `--verify` `--accept` `--after` `--note` `--level` `--track`。
- 効き方: `ready` 等は即時反映 ／ `blocked`/`review` は反映して ready に積み直し（needs は消える）／
  `doing`（実行中）は反映を予約し**現在の試行の結果を確定しない**（verify も done もせず積み直し）。
- CLI を実行できない環境では `commands/<name>.json` のドロップでも同じ
  （`{"command": "revise", "id": "<id>", "feedback": "...", "after": "...", ...}`）。
- 決定は DR（`action: revise`）に残り、feedback は `- learn:` として類似案件の学習材料にもなる。
- ユーザーの意図が「このタスクを二度とやらせない」なら revise でなく `hold`、「順序だけ」なら
  `reprioritize`。内容ややり方を変えるのが revise。

### 検収待ち（review）の承認 — verify=PASS でも人の承認が要る案件

`needs` に「## 検収待ち（verify=PASS・承認で done 確定）」が並ぶことがある。`- review: human` か policy の `gate:` で
承認ゲート対象になった案件で、verify は通っているが **done 未確定**。この `approve` は**done 確定（納品書＋archive）**になる
（積み直しではない）。成果参照を確認しユーザーに可否を仰いでから `approve <id> … --reason "本番OK"`。差し戻すなら
needs に方針を書いて `[x]`。高リスク・不可逆・質的レビュー案件は投入時に `- review: human` か policy `gate:` を提案する。

---

## モード3: 成果物の検収（deliver）

done タスクは `archive/<id>.md` へ退避され検収用**納品書**が付く。一覧は `DELIVERY.md`（いずれもレコードの絶対パス）:

```bash
sed -n '1,40p' <レコードの delivery>            # 受領書一覧（id・タイトル・検収・成果参照・完了）
sed -n '1,60p' <レコードの archive>/T42.md      # 個票の「## 納品書」（verify=PASS・成果参照）
```

**成果参照**（PR/MR URL → commit SHA → `git log -1`）を辿って実際の変更を確認し、「何が・どこで・どう検証されて
完了したか」を要約する。**自己申告で「完了」と報告しない**——納品書の verify=PASS と成果参照を根拠に提示する。

---

## モード4: プロジェクト目標（charter）

**人が書く目標（charter）からバックログを起こし、達成を評価して改善し続ける**上位ループ。**専用コマンドは無く、
`<project>/charter.md` があれば `run` が自動でこの三相（plan→execute→evaluate）に入る**（プロセスは `run` に一本化）。

```bash
sed -n '1,80p' <レコードの root>/charter.md     # 目標/制約/前提/成果物/acceptance(受入 verify)/links を確認
$KA run --root <CONT> --project <PROJ> --executor kiro          # charter あり→plan→execute→evaluate（収束で人へ）
$KA run --root <CONT> --project <PROJ> --watch                  # 目標を満たすまで回り続ける常駐（charter 更新も待つ）
```

- **done の唯一の根拠は `acceptance`（=受入 verify）全 PASS**。charter は `charter.md.example` を正典に人が書く（スキルは
  下書きを提案してよいが、目標・受入条件はユーザーに確認する）。**検証コマンドを書けない受入条件は自然文でも可**
  （`- accept: <自然言語>` か散文の箇条書き）。run 時にエージェントが決定的 verify へ合成し、合成できなければ人へ回る。
- **収束候補（milestone）は `needs/<project>.md`** に出る。ユーザーに概況（acceptance の PASS 数・改善状況）を要約し、
  次のいずれかを仰ぐ:
  - **完了として受領** → `$KA approve <project> --root <CONT> --project <PROJ> --reason "受領"`（最終納品書）。
  - **次フェーズへ続行** → `charter.md` の goal/acceptance を更新して再実行。
  - **方向修正** → needs に方針 ＋ `policy.md` 編集。
- **横展開**: 別プロジェクトの定義・判断を活かしたいときは charter の `## links` に他プロジェクト名を足すよう提案する。

---

## 補助モード

### 起動 / 常駐 / 状態

```bash
$KA --project <PROJ>                          # = run --watch（常駐。backlog 投入を待つ）
$KA run --root <CONT> --project <PROJ> --executor kiro                     # 単発消化（charter あれば目標駆動）
$KA run --planner none --flow-planner stub --executor stub                # kiro-cli 無しで挙動確認
$KA stats   --root <CONT> --project <PROJ>                                # 計測（スループット・自動化率・人対応待ち）
sed -n '1,30p' <レコードの journal>                                       # 機械のサイクルログ
```

常駐の**起動/停止/再起動**は lifecycle コマンドで（プロセスを直接 kill しない）。プロジェクトは `--project` で選ぶ:

```bash
$KA start   --root <CONT> --project <PROJ>    # detached 常駐起動（重複監視は拒否。設定は --config か .kiro/ に寄せる）
$KA stop    --root <CONT> --project <PROJ>    # graceful 停止（SIGTERM→居残りは SIGKILL）。--pid / --all も可
$KA restart --root <CONT> --project <PROJ>    # 同じプロジェクトを止めてから起動し直す
$KA instances                                 # どの project を誰(pid)が監視中か先に確認
```

「状態を見せて」には `stats` の値で答える。「回して」で `run`/`project` を起こしたら、停止後は**判断待ち（blocked）の
有無と停止理由を報告**する（勝手に done 扱いしない）。終了コード: `0`=完走で判断待ち無し / `1`=判断待ちあり / `2`=予算停止。
巻き込み事故を防ぎたい案件では `run --regression-cmd "<共通スモーク>"` を提案する。

**設定ファイル**で既定を恒久化できる（`CLI > 設定ファイル > 既定`）。`./.kiro/kiro-projects.yaml`（or `~/.kiro/…`）に
置くと自動検出。サンプルは `tools/kiro-projects/kiro-projects.yaml.example`。executor/planner/poll/予算/level/throttle 等の
スカラ＋真偽フラグを書ける（個別パス・`--project` は CLI 専用）。PyYAML 無しなら同キーの JSON（`kiro-projects.json`）。

**自律裁定は既定 on**: verify 失敗を人へ送る前に kiro-cli が「積み直して解けるか／人が要るか」を一次裁定する
（人の policy/承認は飛ばさない・kiro-cli 不在は必ず人へ）。**人の判断を機械に任せたくない**と言われたら
`--no-auto-adjudicate`（または設定 `auto_adjudicate: false`）で無効化する。

### 方針（policy.md・per-project）

優先順位・実行先・安全ゲートを人間が上書きする面（**人 policy ＞ エージェント提案**）。レコードの `policy` パスへ書く:

```yaml
deny:    prod       # 自動実行しない（実行前に止める）／ pin: T3（最優先）／ defer: cleanup（後回し）
offload: heavy      # 分散環境へ（--git-bus 設定時）
gate:    release    # verify PASS でも done 前に人の承認（検収ゲート・タスク一致）
protect: auth/**    # act が触ったら done せず承認へ（パス一致の safety denylist。.env/**/secrets/** 等を推奨）
```

---

## ガードレール（このスキルの一線）

- **操作対象を取り違えない。** 発見レコードの **`container` を `--root`・`project` を `--project`** に渡す（`root` を `--root` に
  渡さない＝二重ネスト）。複数プロジェクトが回り得るので、対象が曖昧ならユーザーに確認する。
- **投入時は必ず `verify` を付ける。** 機械検証できないタスクは積まず、ユーザーに確認する。
- **判断を代行しない。** `needs`・milestone の内容はユーザーに要約し、方針を確認してから `approve`/`hold`/feedback に落とす。
- **done は verify（プロジェクトは acceptance 全 PASS）でしか確定しない。** 検収は自己申告でなく納品書（verify=PASS・成果参照）を根拠にする。
- ループの自律消化・目標評価のロジック自体は kiro-projects 本体の責務。本スキルはその外周（投入・判断・検収・目標確認）に徹する。
