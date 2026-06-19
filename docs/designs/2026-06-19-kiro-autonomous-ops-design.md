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
argparse の範囲で完結し、本体の鉄則（後述 §8）を破らない（自律裁定だけは opt-in でエージェントを使うが、
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
- **CLI 専用**: 真偽フラグ（`--watch` `--ltm` `--no-learn` `--no-archive` `--no-cleanup` `--rot`
  `--dry-run` `--once`）と個別パス上書き（`--backlog` 等）は store_true/曖昧性のため対象外。
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

## 8. 維持した不変条件（外周を足しても破らないもの）

1. **done は verify の終了コード 0 でしか確定しない。** 投入・スキル・設定のどれも自己申告 done を作れない。
2. **必ず有限回で止まる。** `drained` か `budget`。`--watch` でも idle はエージェント非起動。
3. **人の policy ＞ エージェント提案。** 設定ファイルは「既定」レイヤであり、人の `policy.md`
   （deny/pin/defer/offload）と決定記録の優先関係には介入しない。
4. **標準ライブラリのみ・pip 依存なし**（PyYAML は任意の上乗せ。無ければ JSON）。
5. **決定的なファイル操作で完結**。レジストリ・設定読込・発見はいずれも LLM/エージェントを起動しない。

---

## 9. テスト

`tools/kiro-autonomous/tests/test_kiro_autonomous.py`（**計 66 件**）。本書の追加分:

| 領域 | 検証 |
|------|------|
| 省略時既定（§2） | 無引数/フラグのみ→`run --watch`、明示 `run` は watch 無し、他サブコマンド不変 |
| インスタンス（§4） | 登録→発見・主要パスの充足・死活 prune・run 後の後始末・`instances --json` smoke |
| 設定ファイル（§5） | JSON/YAML 読込・CLI 上書き・組み込み既定・不在 `--config` のエラー |
| 自律裁定（§6） | requeue/escalate/不正出力フォールバックの単体・cap 内で積み直し→人へ・off は kiro 未呼び出し・verify 未定義は対象外 |
| 検収ゲート（§7） | review 単体判定・PASS でも review 保留→approve で done 確定・policy.gate・ゲート無しは即 done・差し戻し再実行 |

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-autonomous/tests
```

---

## 10. 今後の拡張余地（非目標として明示）

- 真偽フラグの設定ファイル対応（`watch: true` 等）— 現状は `auto_adjudicate` のみ三値化済み、他は CLI 専用。
- リモート（別ホスト）インスタンスの発見 — 現状はローカル home のレジストリのみ（git バス越しは未対応）。
- スキルからの常駐ライフサイクル管理（起動/停止/再起動の明示操作）— 現状は発見と読み書きまで。
- 自律裁定の判断材料の拡充（journal/decisions の文脈や成果差分を kiro-cli に渡す）— 現状は task と失敗理由のみ。

これらは本体の不変条件を保ったまま、同じ「外周を足す」方針で段階追加できる。
