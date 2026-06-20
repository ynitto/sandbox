# kiro-autonomous — 運用・外部操作レイヤ設計書

> 作成日: 2026-06-19 ／ 対象ブランチ: `claude/determined-cray-dthvbi`
> 母体設計: [`2026-06-16-kiro-autonomous-mvp-design.md`](2026-06-16-kiro-autonomous-mvp-design.md)（正準ループ本体）
> 関連ファイル: `tools/kiro-autonomous/kiro-autonomous.py`,
> `tools/kiro-autonomous/{README.md, backlog.md.example, kiro-autonomous.yaml.example}`,
> `tools/kiro-autonomous/tests/test_kiro_autonomous.py`, `.github/skills/kiro-autonomous/`
>
> 本書は MVP（自律ループ本体）の上に、**人と OS が触る「外部操作レイヤ」**として段階的に積んだ
> 5 つの設計をまとめた差分設計書。本体の不変条件（verify ゲート・有限停止・人の policy 優先）は
> 一切変えず、その**外周＝起動／接続／操作／設定**を一級にすることが狙い。

---

## 0. 全体像 — 2 つの面

kiro-autonomous は「自律的に回る内側」と「人・OS が触る外側」の二面で捉えると整理できる。

```
            ┌─────────────────────────── 外部操作レイヤ（本書）───────────────────────────┐
   人・OS →  │  常駐起動    操作スキル          稼働インスタンス発見       設定ファイル        │
            │ (§2 watch)  (§3 skill)        (§4 instances/WSL)      (§5 config)         │
            └───────────────┬───────────────────────┬───────────────────────┬───────────┘
                            │ 投入/判断/検収          │ root 発見              │ 既定値
                            ▼                        ▼                        ▼
   ┌──────────────────── 自律ループ本体（MVP 設計書）─────────────────────────────────┐
   │  backlog/ を優先順位付け → kiro-flow で act → verify ゲート → done は archive/ へ   │
   │  退避・NG は積み直す → drained/budget で停止。人の判断は needs/・decisions/ で往復  │
   └────────────────────────────────────────────────────────────────────────────────┘
```

| § | 追加 | 目的（誰の何を楽にするか） |
|---|------|--------------------------|
| 1 | 規約のテンプレ集約 | タスク書式の知識を「作成時に必ず開く 1 ファイル」に一元化（自動適用依存を排除） |
| 2 | 省略時 `run --watch` 既定化 | PC 起動時に常駐させ backlog 投入を待つ使い方を一級に |
| 3 | 操作スキル `kiro-autonomous` | 投入・判断・検収という**人の接点**を AI スキルから駆動 |
| 4 | 稼働インスタンスのレジストリ | 「いま稼働中のプロセスが見ているフォルダ」を発見し WSL/Windows をまたいで読み書き |
| 5 | 設定ファイル（YAML/JSON） | 環境ごと・常駐ごとに決まる値を集約し、毎回のフラグ列を不要に |
| 6 | 自律裁定（kiro-cli 門番） | 人の判断(needs)へ送る前に「積み直して解けるか」を機械裁定し承認負荷を軽減（既定 on・安全側フォールバック） |
| 7 | 検収ゲート（承認 on done） | verify=PASS でも高リスク案件は done 前に人の承認を要する（`- review: human` / policy `gate:`・既定なし） |

設計原則は一貫して **「内側のロジックは触らず、外周を足す」**。各機能は決定的なファイル操作と
argparse の範囲で完結し、本体の鉄則（後述 §9）を破らない（自律裁定だけは opt-in でエージェントを使うが、
有限性と人 policy 優先は保つ）。

---

## 1. タスク書式規約のテンプレ集約

### 背景
当初、backlog 編集時に AI へ自動適用される規約ファイル
`.github/instructions/kiro-autonomous.instructions.md`（`applyTo: "**/backlog/*.md"`）が存在し、
`install.py` が各エージェント領域へ配布していた。だが本体ロジックはこのファイルを読まず、
**書式の正典が 2 つ（instructions とテンプレ）に割れる**状態だった。

### 決定
- instructions ファイルを**削除**し、書式規約を `tools/kiro-autonomous/backlog.md.example`
  （タスク作成時に必ずコピーする雛形）へ集約。テンプレ先頭のコメントを正典化した。
- テンプレに `status` 値（`inbox`/`draft`/`ready`/`doing`/`done`/`blocked`）、各フィールド定義、
  鉄則（verify 必須・曖昧なら積まない・有限停止）を内包。
- 参照は README と本設計書へ集約。`install.py` はディレクトリ走査のためコード変更不要。

### 効果
書式の知識が「タスクを書く瞬間に目に入る場所」に 1 つだけ残る。自動適用ミドルウェアへの依存も消えた。

---

## 2. 省略時 `run --watch` 既定化（常駐起動）

### 背景
「PC 起動時に立ち上げっぱなしにして backlog 投入を待つ」常駐運用を一級にしたい。

### 決定
`main()` で **argv の先頭が既知サブコマンド/ヘルプでなければ `["run", "--watch", *argv]` に補完**する。

```python
_subcommands = {"run","triage","needs","promote","rot","approve","hold","reprioritize","instances"}
if not argv or (argv[0] not in _subcommands and argv[0] not in ("-h","--help")):
    argv = ["run", "--watch", *argv]
```

- `kiro-autonomous` → `run --watch`（常駐）。`kiro-autonomous --poll 10` のようにフラグだけでも常駐。
- **明示 `run` は不変**（`--watch` を勝手に付けない）。`needs` 等の他サブコマンドも従来どおり。
- idle 中はエージェント（kiro-cli/flow）を起動しない既存性質をそのまま使うので、待機は安価。

### OS 自動起動
README に **systemd ユーザーユニット / macOS launchd / Windows タスクスケジューラ**の登録例を同梱。
常駐は backlog を置く作業ディレクトリで起動する（`--root` は cwd 相対）か `--root /abs` を渡す。

---

## 3. 操作スキル `kiro-autonomous`（外部アクションの担い手）

`.github/skills/kiro-autonomous/`（`SKILL.md` / `meta.yaml` / `eval.json`）。ループ本体が**自律消化**を
回すのに対し、スキルはその**外周＝人の接点**を引き受ける。既存 CLI を駆動するだけで**コード重複なし**。

| モード | 操作 | 実体 |
|--------|------|------|
| **投入 enqueue** | バックログにタスクを積む | `backlog/<id>.md` を 1 ファイル作成（**verify 必須**） |
| **判断 decide** | 人の判断待ちを確認し指示 | `needs` 確認 → ユーザーに要約 → フィードバック欄記入 or `approve`/`hold`/`reprioritize` |
| **検収 deliver** | 成果物を確認 | `DELIVERY.md` と `archive/<id>.md` の納品書（verify=PASS・成果参照） |
| 補助 | 起動/状態・方針 | 常駐/単発の `run`、`triage`、`policy.md` 編集、設定ファイル案内 |

ガードレール: **判断を代行しない**（方針はユーザーに確認）／**done は verify でしか確定させない**／
**操作対象 root を取り違えない**（§4 で発見した root に `--root` を付ける）。

---

## 4. 稼働インスタンスのレジストリと WSL/Windows 橋渡し

### 課題
スキルの投入・判断・検収は、**いま稼働中のプロセスが実際に見ているフォルダ**に対して行わなければ
意味がない。さらに **プロセスは WSL・操作側は Windows/WSL** という構成が多く、パスが食い違う。

### 設計 — 発見可能なランタイムレジストリ
`run`（特に `--watch`）中、共通 home に自分の監視対象を JSON で登録し、終了時に消す。

- **置き場**: `resolve_state_home()` = `$KIRO_AUTONOMOUS_HOME` → `~/.kiro-autonomous`、その
  `instances/<pid>.json`。
- **登録/解除**: `cmd_run` の先頭で `register_instance`、`finally` で削除。死活は PID（`os.kill(pid,0)`）
  で判定し、一覧時に死んだレコードを prune するので、SIGKILL 等で残っても自己修復する。
- **発見口**: `kiro-autonomous instances [--json]`（共通設定不要の独立コマンド）。

レコード schema（`instance_record`）:

```jsonc
{
  "pid": 12345, "watch": true,
  "root": "/home/user/work/.kiro-autonomous",          // プロセス側 OS の絶対パス
  "backlog": "…/backlog", "needs": "…/needs", "archive": "…/archive",
  "policy": "…/policy.md", "delivery": "…/DELIVERY.md", "journal": "…/journal.md",
  "workdir": "…", "host": "…", "python": "…", "started_iso": "2026-06-19T…",
  "runtime": "wsl",            // linux | wsl | windows | darwin
  "wsl_distro": "Ubuntu",
  "root_windows": "\\\\wsl.localhost\\Ubuntu\\home\\user\\work\\.kiro-autonomous"  // wsl のみ・best-effort
}
```

### 環境判定とパス変換
- `detect_runtime()`: `/proc/version` に `microsoft` あり or `$WSL_DISTRO_NAME` あり → **wsl**。
  以下 `sys.platform` で windows/darwin/linux。
- `to_windows_path()`: `wslpath -w`（無ければ `None`）。WSL レコードには `root_windows` を best-effort で併記。

### スキル側の接続原則（SKILL.md）
1. `instances --json` で root を発見（Windows 側なら `wsl.exe -d <distro> -- … instances --json`）。
2. **境界をまたがない**のが最確実 = プロセスと同じ OS 側で読み書きする（WSL なら `wsl.exe` 経由で
   CLI もファイル編集も WSL 内で）。
3. どうしても Windows から直接触るときだけ `root_windows` / `wslpath` / `\\wsl.localhost\<distro>\` で変換。
4. 自分も同一 WSL なら変換不要 — 発見した `root` をそのまま `--root` に渡す。

### 常駐ライフサイクル（start / stop / restart）（§11 で実装）
発見（レジストリ）の上に、常駐プロセスの**明示操作**を一級コマンドとして載せた。スキル/人が「起動・停止・
再起動」を直接呼べる（従来は発見と読み書きまで）。

- **`start`**: `run --watch` を `start_new_session` で切り離して起動（detached）。ログは
  `~/.kiro-autonomous/logs/<root>.log`。起動後にレジストリ出現を確認して pid を報告。**重複監視は既定で拒否**
  （同 root が稼働中なら `--force`/`restart` へ誘導）。実行時設定は**設定ファイルに寄せる**思想で、`start` 自身は
  個別 run フラグを取らず `--root`/`--config` のみ（§5 の延長）。
- **`stop`**: `--root`/`--pid`/`--all` で選び、**SIGTERM →（居残りのみ）SIGKILL**。daemon 側は cfg.watch 時のみ
  SIGTERM を `KeyboardInterrupt` 化し、既存の `finally` で registry を掃除して graceful 終了する。**自分自身の
  PID は決して止めない**安全ガード、ゾンビは `waitpid(WNOHANG)` で回収。
- **`restart`**: 同 root を停止してから `start`。
- いずれもレジストリの上の薄い操作で、本体ロジック・不変条件には触れない。Windows ネイティブは SIGTERM が
  限定的なため stop はベストエフォート。

---

## 5. 設定ファイル（YAML 任意 / JSON フォールバック）

### 背景
常駐運用で毎回 `--executor kiro --poll 10 --location …` と並べるのは煩雑。環境ごとに決まる値を集約したい。

### 設計（kiro-flow と同一の流儀）
- **優先順位**: `CLI > 設定ファイル > 組み込み既定`。
- **探索順**: `--config` 明示 → `./.kiro/kiro-autonomous.{yaml,yml,json}` → `~/.kiro/…`
  （kiro-flow と同じ `.kiro` ディレクトリを共有）。
- **形式**: YAML は **PyYAML 必須**、無ければ JSON にフォールバック（`.yaml` 指定時はエラーで誘導）。
- **実装**: スカラ既定を `CONFIG_DEFAULTS` に集約し、対象 CLI 引数の `default` を `None` 化、
  `resolve_config(args)` が「CLI 未指定（None）のキーだけ config→既定で埋める」。これで
  「ユーザーが既定値を明示したか」の曖昧さを排除（kiro-flow の None-default 方式を踏襲）。

```python
def resolve_config(args):
    path = _find_config(getattr(args, "config", None))
    cfg = _load_config_file(path) if path else {}
    for key, dflt in CONFIG_DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, cfg.get(key, dflt))
```

- **対象キー（スカラ）**: `executor` `planner` `flow_planner` `location` `model` `root` `workdir`
  `poll` `debounce` `pace` `max_cycles` `max_seconds` `max_retries` `max_iterations`
  `verify_timeout` `act_timeout` `git_bus` `git_branch` `git_subdir` `kiro_flow` `notify_cmd`
  `actor` `learn_threshold` `promote_threshold` `ltm_home` `rot_age_days`。
- **真偽フラグも対応**（§11 で実装）: `watch` `once` `dry_run` `rot` `ltm` `regression_revert`（既定
  false）・`do_archive` `learn` `cleanup`（既定 true）・`auto_adjudicate`（既定 true）。CLI は
  `argparse.BooleanOptionalAction`（`--flag`/`--no-flag`、未指定=None）で三値化し、`resolve_config` が
  CLI 未指定のみ config→既定 で埋める＝CLI > config > 既定。`--archive` はパス用なので退避可否の config
  キーは `do_archive`。
- **CLI 専用**: 個別パス上書き（`--backlog` 等）と実行限定フラグ（`--json` `--fix` `--pin`/`--defer`）。
- サンプル: `kiro-autonomous.yaml.example`（コメント付き）。

常駐は systemd の `ExecStart` を `kiro-autonomous` だけにして、調整はこのファイルで完結できる。

---

## 6. 自律裁定 — 人の判断(needs)へ送る前の kiro-cli 門番

### 課題
人の判断・承認の負荷を減らしたい。verify 失敗の中には「明確な追加指示があればループ内で解ける」ものと
「人の意思決定が要る」ものが混在する。前者を**人へ送る前に**機械側で捌ければ、`needs` の量を絞れる。

### 設計 — エスカレーション直前のフック
ループ内で人へ回す唯一の経路（`run_loop` の verify 失敗 → `_escalate`）に門番を挟む。

```
verify 失敗 ─▶ retries 超過 ─▶ ① DR 学習（決定的・kiro-cli 不要）
                               └─ 効かなければ ─▶ ② 自律裁定（kiro-cli）
                                                   ├ requeue → ready に戻し guidance を注入（needs 作らない）
                                                   └ escalate / 不能 → ③ 人へ（_block→needs）
```

- `adjudicate_escalation(cfg, task, reason)`: kiro-cli に「`requeue`（積み直す価値あり）か `escalate`
  （人が要る）か」を JSON で判断させる。**判断は厳しめ**（少しでも意思決定・承認・リスクが絡めば escalate）。
  例外・不正出力・kiro-cli 不在は**必ず escalate にフォールバック**（人を飛ばさない安全側）。
- **判断材料の拡充（§11 で実装）**: 失敗理由だけでなく、`adjudication_context` が
  **decisions/<id>.md（過去の人の判断・auto-adjudicate 履歴）・journal の当該 ID 行（これまでの試行）・
  task の feedback/note** を決定的に集めて（LLM 不要・末尾優先で有界化）プロンプトへ添える。
  「過去に積み直して解けていないなら escalate」を明示し、**的外れな requeue や同じ失敗での再裁定ループを抑制**
  する。文脈が空（初回など）なら添えない＝従来どおり。
- `requeue` なら `status: ready` に戻し、`guidance` を次 act の feedback として注入、決定記録に
  `auto-adjudicate` を残す。`needs` は作らない。

### on/off と有限性
- `auto_adjudicate`（既定 **on**）。CLI は三値 `--auto-adjudicate` / `--no-auto-adjudicate`、設定ファイルでも
  `auto_adjudicate: true/false`（§5 の None-default 方式で `CLI > config > 既定`）。既定 on でも安全側に倒れる
  （kiro-cli 不在・判断不能は必ず人へ）ため、無効化が必要な場合のみ `--no-auto-adjudicate`。
- `adjudicate_max`（既定 1）で**1 タスクあたりの裁定回数を制限**＝必ず有限回で人へ落ちる（不変条件②を保全）。

### 不変条件との整合（重要）
- **人の policy は飛ばさない**: 裁定はループ内の verify 失敗のみが対象。`policy.deny` / `hold` / `rot` による
  判断待ちは `_escalate` を通らず**裁定対象外**（人の上書きが常に勝つ原則を維持）。
- **verify 未定義は対象外**: verify を持たないタスクはループでは done にできない（不変条件①）。よって裁定せず必ず人へ。
- **DR 学習を優先**: 決定的に解けるならエージェントを起こさない。裁定は学習が効かない時の二次ゲート。

---

## 7. 検収ゲート — verify=PASS でも人の承認を要する（§6 の対称）

### 課題
§6 が**失敗側**の人ゲートを減らすのに対し、**成功側**には別の問題がある。verify は機械的合否でしかなく、
PASS でも人の承認が要る場面が実在する: ①弱い/騙せる verify（偽陽性）②不可逆・高リスク（本番反映・課金・
削除）③質的受け入れ（UX・セキュリティ・文面）④verify が見ない巻き込み事故。現状は done が即確定し、
納品書(`DELIVERY.md`/`archive`)は**非ブロッキングな事後レビュー**でしかなかった。

### 設計 — done 確定の手前で止める opt-in ゲート
verify PASS 後・archive 前にフックし、**ゲート対象なら done を確定せず承認待ち `review` へ**。

- **対象指定（2 系統・既定はゲート無し）**:
  - タスク単位 `backlog/<id>.md` の `- review: human`（`human`/`manual`/`required` 等）。
  - policy 単位 `policy.md` の `gate: <パターン>`（`deny` 等と同じ ID/タイトル部分一致）。
- **`review` 状態**: 新ステータス。consumable でないので再消化されず、`needs/<id>.md`（検収待ち）を生成。
  成果参照・verify 結果は `gate_ref`/`gate_ts`/`gate_vmsg` として task に保持（承認時の納品書に使う）。
- **承認 = done 確定**: `approve <id>` が `review` を検知し、`append_delivery`＋`archive_task` で確定（再実行しない）。
  通常の `approve`（`blocked` → `ready` 再実行）とは分岐。
- **差し戻し**: `needs` にフィードバックを書いて `[x]` → `review` → `ready` で再実行（`ingest_feedback`）。
- 非 watch の終了コードは `review` が残ると `1`（`blocked` と同じ人の対応待ち扱い）。

### deny との違い（止める位置）
`deny` は**実行前**に止める（自動実行しない）。`gate` は**実行・verify は通すが done 確定前**に止める。
高リスクでも「走らせて結果は見たい、ただし反映の承認は人が握る」を表現できる。

### 不変条件との整合
既定はゲート無し（verify を信頼）＝人の判断最小化の哲学を保つ。ゲートは opt-in の**追加の人ゲート**であり、
done を verify 以外で確定させる訳ではない（承認は「verify 済みの成果を受領するか」の判断）。

---

## 8. Loop Engineering の中核機能（計測・自己生成・依存・回帰ゲート・コスト予算・取り込み口・並列消費・パス保護・自己監査・自律度・原子的クレーム）

§1–7 が「外周」なら、ここは**ループそのものを engineering する**ための中核。"engineer" は計測・自走・
順序・安全の 4 軸が要る（安全は回帰ゲートとコスト予算の二枚）。いずれも本体の不変条件（§9）を保ったまま
追加した。

### 8.1 計測・レポート（`stats`）
ループを調整するにはまず計測。`compute_stats` が **archive・decisions・DELIVERY・backlog** から決定的に
KPI を集計する（LLM 不要）: 完了数・納品数・status 別 backlog・人対応待ち（blocked+review）・**自動化率**
（auto-resolve＋auto-adjudicate ÷ 自動＋人）・**一発 done 率**（retry 0）・retry 累計。`stats [--json]`。
これが無いと §6 の裁定や閾値調整の良し悪しも測れない。

### 8.2 タスクの自己生成（followup）— backlog の自走
完了タスクから派生タスクを生み、人の投入に依存せずループが仕事を継ぎ足す。2 経路: 静的（タスクの
`- followup: <title> :: <verify>`）／動的（act 出力の `@followup ...` 行）。`spawn_followups` が
`backlog/<parent>-fN.md`（`source: followup`）を作り、verify があれば `ready`（同 run で自走）、無ければ
`inbox`（→人）。**`max_spawn`（既定 20）で 1 run の生成数を上限**＝暴走しない。`spawn-followup` を決定記録に残す。

### 8.3 タスク依存（DAG・`- after:`）
`- after: T1, T2` の依存が **done（=backlog から退避）になるまで消化対象に入らない**。`prioritize` が
`ready_after_deps` で依存未達を除外。依存が blocked/review で止まれば従属も待つ。平坦な priority＋古さに
トポロジカル順序を重ねる。

### 8.4 回帰ゲート（done 確定前のグローバル検査）
per-task の `verify` は通っても別所を壊す（巻き込み事故）。`regression_cmd` を与えると **verify PASS 後・
done 確定前**に共通検査を走らせ、失敗したら done にせず人へ（blocked）。`--regression-revert` は未コミットの
作業ツリー変更のみ best-effort で戻す（コミット/push 済みは対象外・既定 off）。verify が「個票の合否」なら
回帰ゲートは「全体の健全性」を見る二段目。

### 8.5 コスト予算（トークン/金額の有限停止）
無人運用の安全＝**暴走課金を止める**こと。`max_tokens`/`max_cost`（既定 0＝無制限）を予算条件として
`max_cycles`/`max_seconds` と並べ、超えたら `reason=cost`（exit 2）で停止する。per-task の計上は
`parse_cost` が **act 出力の `@cost tokens=… usd=…` 行**を加算（決定的・LLM 不要。エージェントが吐かなければ
0）。done 時に納品書へ `- cost:` を残すので `compute_stats`（§8.1）が archive 横断で累計トークン/金額を
出す＝予算とのズレを後から検証できる。「有限停止」不変条件（§9-2）を金額軸へ拡張した一枚。

### 8.6 取り込み口の多様化（enqueue / inbox）（§11 で実装）
backlog への投入経路を一級化し、**入口を増やしてもコアは stdlib のみ・ネットワーク非依存・決定的**を保つ。
外部ソース（webhook/メール/GitHub issue 抽出 …）は**薄いアダプタで取り込み口へ流し込む**設計＝本体に
ネットワーク連携を持ち込まない（不変条件④/⑤を守る）。

- **`enqueue`**: CLI フラグ or stdin/JSON（1件/配列）から `task_from_spec` で検証して `backlog/<id>.md` を生成。
  `title` 必須・id は自動一意化・未知キーも保持。**`status` 未指定なら verify 有→`ready` / 無→`inbox`**。
- **`inbox/` ドロップ口**: `<root>/inbox/` に置かれた `.json`（obj/配列）/`.md`（タスク形式）を `ingest_inbox`
  が run/watch の各パス冒頭で backlog へ取り込み、元ファイルを消す。`has_work` も inbox を見て watch を起こす。
- **鉄則の保全**: verify を持たない投入は必ず `inbox`＝人の triage 行き（done は verify でしか確定しない①）。

### 8.7 別ホストの発見（共有レジストリ）（§11 で実装）
インスタンス・レジストリ（§4）はローカル home のみで、別ホストの稼働は見えなかった。**共有レジストリ**
（NFS / 同期フォルダ / git バスのチェックアウト等、複数ホストから見える1ディレクトリ。`--registry` /
`KIRO_AUTONOMOUS_REGISTRY`）へも各ホストがレコードを書き、`instances` がローカル＋共有を横断する。
**core は決定的なファイル操作のみ・ネットワークは共有先の仕組みが担う**＝不変条件④/⑤を破らない。

- **ホスト別の生死判定**: 自ホストは PID（`os.kill(pid,0)`）、別ホストは PID が無意味なので **heartbeat の
  鮮度（`ttl`＝`max(90s, poll×3)`）**。watch が各パス/idle で heartbeat を更新し、鮮度切れは一覧から落ちる。
- **掃除の非対称性**: 自ホストの死レコードは即削除、別ホストは長期 grace（24h）超のみ削除＝共有先での競合を避ける。
- **停止は自ホストのみ**: `stop`/`restart`/`select_instances` は別ホストを対象にしない（リモート PID へ
  シグナルは送れない）。レコードは衝突回避のため `instances/<host>-<pid>.json`。

### 8.8 並列消費（kiro-flow の worker 並列へ寄せる）（§11 で実装）
`prioritize` が返す order は依存（`after`）解決済み＝**互いに独立**。`--concurrency N`（既定 1）で先頭から
最大 N 件を **daemon/remote へ並行 submit**（`ThreadPoolExecutor`）し、実体の並列実行は **kiro-flow の
worker** に委ねる。kiro-autonomous 自身に並列実行器は持たせない（隔離はワーカ側）。

- **並列は重い部分だけ**: `_act_batch` の submit/待機のみ並行。verify と done/archive/decisions/派生生成
  などローカル状態の変更は**逐次**のまま＝workdir・決定記録の競合を避け、§9 の不変条件をそのまま守る。
- **local は逐次**: `_submit_bound` が daemon/remote のときだけバッチ化。`local`（単発 run）実行は1件ずつ。
- **有限停止の保全**: 1サイクル=1タスクの計上は不変。`_select_batch` はバッチ幅を**残サイクル予算**でも
  抑え、`max_cycles`/予算/`--once`（=幅1）をそのまま効かせる。`--concurrency 1` は完全な逐次（従来同値）。

### 8.9 パス保護ゲート（safety denylist）（[Loop Engineering](https://github.com/cobusgreyling/loop-engineering) 由来）
[Loop Engineering の safety.md](https://github.com/cobusgreyling/loop-engineering/blob/main/docs/safety.md) は「`.env`/`secrets`/`auth`/
`payments`/`**/migrations/**`/infra は人の承認なしに自動編集させない」を無人運用の最低ラインとする。kiro-autonomous の
`policy.md` deny は**タスク選択**に効くだけで、act が**実際に触ったファイル**は見ていなかった。そこを埋める二段目の安全ゲート。

- **判定位置**: 回帰ゲート（§8.4）と同じ done 確定前。verify=PASS かつ非回帰でも、act が `policy.md` の
  `protect: <glob>` に一致するファイルを変更していたら **done せず検収待ち(review)** へ落とし、`approve` で done 確定。
- **対象の違い**: `gate`(§7) はタスク（ID/タイトル）一致、`protect` は**変更されたパス**一致。glob は自前の
  `_glob_to_regex`（`*`=非スラッシュ / `**`=スラッシュ含む・`**/` は 0 階層許容）で `.env`/`**/secrets/**`/`auth/**` 等を表現。
- **変更検出**: `git_change_baseline`(act 前 HEAD＋dirty) → `changed_paths_since`(act 後の新規 dirty＋コミット差分)。
  git でない／remote・daemon オフロードは workdir に出ないため best-effort で対象外（実行先側で守る）。
- **不変条件との関係**: 「done は verify でのみ確定」(§9-1) を**さらに厳しく**する方向の追加で、緩めない。
  `protect` 未設定なら git 呼び出しもせずゼロオーバーヘッド（従来同値）。並列時はバッチ基準の和集合で保守的に判定。

### 8.10 Loop Readiness セルフ監査（`audit`）（[Loop Engineering](https://github.com/cobusgreyling/loop-engineering) 由来）
[Loop Design Checklist](https://github.com/cobusgreyling/loop-engineering/blob/main/docs/loop-design-checklist.md) と
[Quick Red Flags](https://github.com/cobusgreyling/loop-engineering/blob/main/docs/failure-modes.md) を、backlog/policy/config/state から
**決定的に採点**するコマンド（loop-audit 相当）。「いまどの自律度で無人運用してよいか」を機械判定する。

- **出力**: レベル `L0 Draft→L1 Report→L2 Assisted→L3 Unattended`・スコア 0–100・チェック一覧・赤旗・提案。`--json`/`--strict`。
- **チェック（重み付き）**: verify 健全（鉄則・25）／有限停止（max_cycles）／verifier 独立（決定的 verify）／
  状態観測（decisions・journal）／リトライ上限／needs エスカレーション先／コスト予算（max_tokens|max_cost）／
  **パス保護**（§8.9 の `protect`・15）／`--rot` 掃除。各レベルの必須チェックが揃うと昇格。
- **赤旗**: 「verify 無し ready タスク」（critical）・「watch なのに予算/保護未設定」（warn）・rot 検知・リトライ上限間際。
- **L3 の門**: verify 健全＋コスト予算＋保護デニーリスト＋掃除が揃い、critical 赤旗が無いときのみ。`--strict` は
  スコア<40 か critical 赤旗で exit 2（CI ゲート）。エージェント不要・stdlib のみで、本体の不変条件は読むだけ（変更しない）。

### 8.11 自律度の段階導入（`--level`）（[Loop Engineering](https://github.com/cobusgreyling/loop-engineering) 由来）
[Loop Engineering の phased rollout](https://github.com/cobusgreyling/loop-engineering/blob/main/docs/operating-loops.md)
「**L1 report → L2 assisted → L3 unattended** を一足飛びにしない」を `--level` で一級化。§8.10 の `audit` が
「いま何レベルに値するか」を採点する側、`--level` は「実際に動かす自律度」を選ぶ側で対になる。

- **report**: 消化ループに入らず、`prioritize` の結果（実行予定の順序）を `plan` として報告して正常終了（exit 0）。
  act しないので backlog を一切変えない安全な下見。`while cfg.level != "report"` の1行ガードで実現。
- **assisted**: act はするが verify=PASS を**自動 done にせず全件 review** へ（`needs_human_review or protect_hits
  or assisted`）。`approve` で done／フィードバックで差し戻し。§7 の検収ゲート機構をそのまま再利用。
- **unattended**（既定）: 現行。既存ゲート（protect/gate/regression）を通れば自動 done。**既定なので従来挙動は不変**。
- 各レベルとも有限停止・予算・verify ゲートは不変。`--level` は「どこまで自動で確定させるか」だけを段階化する。

### 8.12 原子的クレーム（二重実行防止）（[Loop Engineering](https://github.com/cobusgreyling/loop-engineering) 由来）
[multi-loop](https://github.com/cobusgreyling/loop-engineering/blob/main/docs/multi-loop.md) の「`acting_on` で衝突を防ぐ」「[Parallel Collision](https://github.com/cobusgreyling/loop-engineering/blob/main/docs/failure-modes.md)」対策。
`--concurrency` や**同一 backlog を複数プロセス/ホストで回す**と、`status=doing` の persist だけでは取り合い（二重実行）が起きる。

- **原子的取得**: 実行前に `<root>/claims/<id>.lock` を `os.open(O_CREAT|O_EXCL)` で確保できた者だけが回す。
  `_act_batch` はクレームできたタスクだけ act し、取れなかったものは `unavailable` に入れてこの run では触らない。
- **stale view の再検証**: ロック取得は「同時実行」を防ぐが、こちらの in-memory ビューが古い場合に備え、取得後に
  disk を `parse_task` で再確認。既に archive/削除・非 consumable（review/blocked）なら**実行せずロックも解放**。
  これが無いと「A が完了・解放した直後に B が同じタスクを再実行」する取りこぼしが起きる（実測で確認・修正済み）。
- **失踪と解放**: owner クラッシュ時は TTL（`act_timeout+verify_timeout+60`）超で奪取。正常時は done/review/blocked/
  積み直しのいずれでも `release_claim` で即解放。先日の**別ホスト発見（§8.7・共有レジストリ）**と組で安全な多重稼働を担保。

---

## 9. 維持した不変条件（外周を足しても破らないもの）

1. **done は verify の終了コード 0 でしか確定しない。** 投入・スキル・設定のどれも自己申告 done を作れない。
2. **必ず有限回で止まる。** `drained` / `budget`（cycles・time）/ `cost`（tokens・usd）。`--watch` でも
   idle はエージェント非起動。
3. **人の policy ＞ エージェント提案。** 設定ファイルは「既定」レイヤであり、人の `policy.md`
   （deny/pin/defer/offload）と決定記録の優先関係には介入しない。
4. **標準ライブラリのみ・pip 依存なし**（PyYAML は任意の上乗せ。無ければ JSON）。
5. **決定的なファイル操作で完結**。レジストリ・設定読込・発見はいずれも LLM/エージェントを起動しない。

---

## 10. テスト

`tools/kiro-autonomous/tests/test_kiro_autonomous.py`（**計 125 件**）。本書の追加分:

| 領域 | 検証 |
|------|------|
| 省略時既定（§2） | 無引数/フラグのみ→`run --watch`、明示 `run` は watch 無し、他サブコマンド不変 |
| インスタンス（§4） | 登録→発見・主要パスの充足・死活 prune・run 後の後始末・`instances --json` smoke・**lifecycle（select の pid/root/全件一致・stop が実プロセスを停止し登録掃除・対象無しは 1・start→登録→重複拒否→stop）** |
| 設定ファイル（§5） | JSON/YAML 読込・CLI 上書き・組み込み既定・不在 `--config` のエラー・**真偽フラグの config 反映と CLI 上書き・既定** |
| 自律裁定（§6） | requeue/escalate/不正出力フォールバックの単体・cap 内で積み直し→人へ・off は kiro 未呼び出し・verify 未定義は対象外・**文脈収集(journal/decisions/feedback/note)とプロンプト注入** |
| 検収ゲート（§7） | review 単体判定・PASS でも review 保留→approve で done 確定・policy.gate・ゲート無しは即 done・差し戻し再実行 |
| 中核機能（§8） | stats 集計・followup 静的生成(ready/inbox)・max_spawn=0 無効・依存除外と解決後 done・依存未完で停止・回帰失敗で blocked・回帰成功で done |
| コスト予算（§8.5） | parse_cost のマーカ加算・max_tokens 超過で `cost` 停止(exit 2)・max_cost 超過で停止・stats の archive 横断コスト集計 |
| 取り込み口（§8.6） | spec 検証(title 必須/status 既定)・フィールドと未知キー保持・id 一意化・inbox(json/md) 取り込みと消去・run_loop が inbox を消化・enqueue コマンド |
| 別ホスト発見（§8.7） | heartbeat/ttl レコード・共有先への登録と heartbeat 更新・別ホストの生存(鮮度)/停止判定・select は自ホストのみ・複数ディレクトリ集約と重複排除・--registry/env 解釈 |
| 並列消費（§8.8） | submit_bound 判定・batch 幅と残予算/once/local の制限・act が実際に並行実行・location 伝播・dry-run は act 非呼出・once は1件 |
| パス保護（§8.9） | glob マッチャ(denylist 一式)・changed_paths_since(dirty＋コミット差分)・保護パス変更→review・保護外→done・protect 未設定は no-op |
| 自己監査（§8.10） | 弱構成→L0/critical 赤旗・強構成→L3/100・cost_budget/protect シグナルの切替・--strict の exit(2/0)・backlog 無しでも動く |
| 自律度（§8.11） | report=act せず plan 報告・exit0／assisted=act するが全件 review・exit1／unattended(既定)=自動 done で従来不変 |
| 原子的クレーム（§8.12） | 1人目取得・2人目排除・解放で再取得・stale 奪取・取得後 disk 再検証で弾く・保持中は skip・run 後は全解放 |

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-autonomous/tests
```

---

## 11. 今後の拡張余地（非目標として明示）

- 既製の外部アダプタ同梱（GitHub issue / メール → `enqueue --json`）— 現状は取り込み口（§8.6）まで。アダプタ本体は範囲外。
- ローカル並列実行器（daemon 無しで N 並列）— 現状の並列消費（§8.8）は kiro-flow の worker 並列に委ねる（local 単発 run は逐次）。
- 回帰ゲートの本格ロールバック（コミット/push 済み変更の revert・PR クローズ）— 現状は未コミット変更の戻しのみ。

これらは本体の不変条件を保ったまま、同じ「外周を足す」方針で段階追加できる。
