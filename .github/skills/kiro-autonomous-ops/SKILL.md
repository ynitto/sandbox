---
name: kiro-autonomous-ops
description: kiro-autonomous（自律バックログ消化ループ）を外部から操作するスキル。バックログへのタスク投入・人の判断待ち（needs）の確認と指示・成果物（納品書）の検収を、CLI とファイル操作で支援する。「バックログに積んで」「kiro-autonomous にタスクを投げて」「判断待ちを確認して」「needs を見せて」「承認して」「保留して」「優先度を上げて」「成果物を確認して」「納品物を見せて」「ループを回して/常駐させて」などで発動する。kiro-autonomous の運用が含まれる場合に優先して選択する。
metadata:
  version: 1.0.0
  tier: experimental
  category: operations
  tags:
    - kiro-autonomous
    - backlog
    - loop-engineering
    - human-in-the-loop
    - operations
---

# kiro-autonomous-ops — 自律ループの外部操作

`kiro-autonomous`（`backlog/` を優先順位付け→実行→`verify` ゲート→収束させる制御層）を、**人間の運用側**
から操作するスキル。ループ本体（自律消化）は `kiro-autonomous run` が回す。本スキルが担うのは、その
**外部からの3つのアクション**である:

| モード | 発動フレーズ | やること |
|--------|------------|---------|
| **投入（enqueue）** | 「バックログに積んで」「タスクを投げて」「○○を kiro-autonomous にやらせて」 | `backlog/<id>.md` を1ファイル作成（**必ず実行可能な `verify` 付き**） |
| **判断（decide）** | 「判断待ちを確認して」「needs を見せて」「承認して」「保留して」「優先度を上げて/下げて」 | `needs/` を確認し、フィードバック記入 or `approve`/`hold`/`reprioritize` |
| **検収（deliver）** | 「成果物を確認して」「納品物を見せて」「何が完了した?」 | `DELIVERY.md` と `archive/<id>.md` の納品書を読む |
| （補助）**起動/状態** | 「回して」「常駐させて」「状態を見せて」 | ループ起動（常駐含む）・順位確認・journal 確認 |
| （補助）**方針** | 「prod は手動に」「これを最優先に固定」 | `policy.md` に `deny`/`pin`/`defer`/`offload` を記述 |

ループの**自律消化そのもの**（タスクを実際に走らせる）を依頼されたら、それは kiro-autonomous 本体の仕事
なので「起動/状態」モードで `run` を起こす。本スキルは投入・判断・検収という**人の接点**を引き受ける。

---

## 前提条件と CLI 解決

- `python3`（標準ライブラリのみ）。`kiro-flow` / `kiro-cli` はループ実行時のみ必要（投入・判断・検収だけなら不要）。
- CLI を解決する（PATH 優先、無ければリポジトリ内のスクリプト）:

```bash
KA="$(command -v kiro-autonomous || echo 'python3 tools/kiro-autonomous/kiro-autonomous.py')"
$KA --help
```

- **作用先（root）**: 既定で **cwd の `./.kiro-autonomous/` 配下**に集約される（`backlog/`・`needs/`・
  `archive/`・`policy.md`・`DELIVERY.md` など）。ループを別ディレクトリで回している場合は `--root <path>`
  を全コマンドに付ける。**どのディレクトリのループを操作するか不明なら、まずユーザーに確認する。**
- タスク書式の正典は `tools/kiro-autonomous/backlog.md.example`、運用詳細は同 `README.md`。

---

## モード1: バックログ投入（enqueue）

`backlog/<id>.md`（**1ファイル＝1タスク。id はファイル名の stem**）を作る。書式の正典は
`tools/kiro-autonomous/backlog.md.example`（規約コメント付き雛形）。これを写経して1ファイルを書く:

```bash
mkdir -p .kiro-autonomous/backlog
cat > .kiro-autonomous/backlog/T42.md <<'MD'
## T42: 利用規約ページに最終更新日を表示する
- status: ready
- source: human
- priority: 1
- verify: `grep -q "最終更新" web/terms.html`
- retries: 0
- note: 既存の見出し直下に
MD
```

フィールド:
- `status` … `ready`=実行待ち / `inbox`=triage 待ち / `draft`=書きかけ（消化対象外）/ `blocked`=判断待ち。
- `priority` … 整数・大きいほど高優先（省略時 0）。
- `verify` … **終了コード 0 を PASS とみなすシェルコマンド。done 確定の唯一の根拠**。

### 鉄則（投入時に必ず守る）

1. **`verify` を必ず付ける。** 機械的に成否を判定できないタスクは積まない（書けない＝分解が粗い兆候）。
   一旦 `inbox` で出しても、verify が無ければ triage で `ready` に上がらず人へ戻る。
2. **曖昧で人間判断が要るタスクは積まず、先にユーザーに確認する。** ループは「機械検証できる作業」の箱。
3. 書きかけは `status: draft` にして消化・誤発火を防ぐ（書き終えたら `ready` に上げる）。

投入後の確認（順位付けのドライラン。消化はしない）:

```bash
$KA triage                 # inbox→ready 昇格と現在の優先順位を表示
```

---

## モード2: 人の判断（decide）

タスクが判断待ちになると `needs/<id>.md`（通知＋**フィードバック記入欄**）が生成される。

### 2-1. 何が待っているか確認

```bash
$KA needs                  # blocked / acceptance 未定義の一覧を表示
```

`needs/<id>.md` を読んで文脈（失敗内容・必要な判断）を把握し、ユーザーに要約して**指示を仰ぐ**。
判断を勝手に決めず、方針はユーザーに確認してから反映する。

### 2-2. 指示を返す（2通り）

**(a) フィードバック往復**（自由記述で方針を渡し、ブロック解除して積み直す）— `needs/<id>.md` の
「## フィードバック」欄に方針を書き、確定チェックを `- [x]` にして保存する。次パス（`--watch` なら次 poll）で
拾われ、内容が次の実行に反映される。**`[x]` にした時だけ確定**するので、書きかけ保存では発火しない。

**(b) サブコマンドで明示操作**（決定は `decisions/<id>.md` に記録される）:

```bash
$KA approve <id> --reason "テスト側の期待値を修正して再実行"   # 承認して積み直し
$KA hold    <id> --reason "本番反映は手動。自動実行しない"      # 保留（policy に deny 追加）
$KA reprioritize <id> --pin   --reason "今日中に必要"          # 最優先に固定
$KA reprioritize <id> --defer --reason "急がないので後回し"     # 後回し
```

`approve` で内容修正の指示を渡したい時は `--reason` に具体策を書く（フィードバック欄と同じ役割）。

---

## モード3: 成果物の検収（deliver）

done になったタスクは `archive/<id>.md` へ退避され、検収用の**納品書**が付く。一覧は `DELIVERY.md`。

```bash
sed -n '1,40p' .kiro-autonomous/DELIVERY.md            # 受領書一覧（id・タイトル・検収・成果参照・完了）
ls .kiro-autonomous/archive                            # 完了タスク一覧
sed -n '1,60p' .kiro-autonomous/archive/T42.md         # 個票の「## 納品書」（verify=PASS・成果参照）
```

**成果参照**（PR/MR URL → commit SHA → `git log -1`）を辿って実際の変更を確認し、ユーザーに
「何が・どこで・どう検証されて完了したか」を要約する。検証は `verify` の終了コード 0 が唯一の根拠なので、
**自己申告で「完了」と報告しない**——納品書の verify=PASS と成果参照を根拠に提示する。

---

## 補助モード

### 起動 / 常駐 / 状態

```bash
$KA                              # = run --watch（常駐。backlog 投入を待ち続ける）
$KA run --executor kiro          # 単発で backlog を消化（drained/budget で終了）
$KA run --planner none --flow-planner stub --executor stub   # kiro-cli 無しで挙動確認
$KA triage                       # 消化せず順位だけ表示
sed -n '1,30p' .kiro-autonomous/journal.md             # 機械のサイクルログ
```

「回して」と言われて `run` を起動したら、停止後は**判断待ち（blocked）の有無と停止理由（drained/budget）
を報告**する（勝手に done 扱いしない）。終了コード: `0`=完走で判断待ち無し / `1`=判断待ちあり / `2`=予算停止。

### 方針（policy.md）

優先順位・実行先を人間が上書きする面。**precedence は人間 policy ＞ エージェント提案**（人が必ず勝つ）。

```yaml
# .kiro-autonomous/policy.md
deny:    prod       # "prod" を含むタスクは自動実行しない（人の判断待ち）
pin:     T3         # T3 を最優先
defer:   cleanup    # "cleanup" を含むタスクは後回し
offload: heavy      # "heavy" を含むタスクは分散環境へ（--git-bus 設定時）
```

---

## ガードレール（このスキルの一線）

- **投入時は必ず `verify` を付ける。** 機械検証できないタスクは積まず、ユーザーに確認する。
- **判断を代行しない。** `needs` の内容はユーザーに要約し、方針を確認してから `approve`/`hold`/feedback に落とす。
- **done は verify でしか確定しない。** 検収はループの自己申告でなく納品書（verify=PASS・成果参照）を根拠にする。
- **操作対象の root を取り違えない。** 複数ディレクトリでループが回り得るときは `--root` を明示し、不明なら確認する。
- ループの自律消化のロジック自体は kiro-autonomous 本体の責務。本スキルはその外周（投入・判断・検収）に徹する。
