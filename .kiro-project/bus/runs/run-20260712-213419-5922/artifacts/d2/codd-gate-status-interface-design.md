# codd-gate 検出結果の共通インターフェース設計（d2）

**差別化の切り口**: `CoddGateStatus` を「schemas/ の3つ目の契約」にはしない。3フック
（regression/acceptance/enqueue）は同一プロセス内の呼び出し元でしかないため、検出結果は
ディスクにもスキーマにも乗らない**プロセス内一過性の値オブジェクト**として設計し、依存の矢印を
「kiro-project → subprocess → codd-gate」「kiro-project ⇄ 既存2スキーマファイル」の
**既存2本**だけに閉じ込める。3フックへの受け渡しは新しい共有フォーマットの発明ではなく、
各フックがもともと持っている自動配線ポイント（`load_charter` / `evaluate_acceptance` /
`intake_cmd`）へ**同じ値を関数引数として素通しする**だけにする。

## 1. 前提（完了条件の解釈）

このタスクは「検出結果を3フックへ渡す共通インターフェースの確定」＝データ構造とアクセサの
**設計・文書化**が成果物であり、実装（b系）ではない。ワークスペースのコードは変更していない
（`git status` クリーン。s1/s2/s3/d1 と同じ扱い）。完了条件のシェルコマンドは s1〜s3・d1 が
確認した通り、`.kiro-project/repos.json` 未生成と `codd` テスト未実装により現状失敗する
（下記「6. 検証」で再確認）。これは実装系タスクの責務であり、本書はその実装が読む入力として書く。

d1（`codd-gate-autodetect-judgment-design.md`）が「使えるか使えないか」の**判定条件**
（4項目・短絡パイプライン）を確定済みなので、本書はその判定結果を**どういう形で持ち回し、
3つの異なる消費者にどう見せるか**にのみ責務を持つ。判定条件そのものの再設計はしない。

## 2. CoddGateStatus データ構造

```python
@dataclass(frozen=True)
class CoddGateStatus:
    binary: "list[str] | None"       # argv prefix。resolve_kiro_flow() の戻り値と同型。None=未検出
    version: "tuple[int, int, int] | None"
    findings: "list[dict]"           # doctor_env_findings と同型 {category, severity, title, evidence, fix, fix_action}
```

- **`binary`**: d1 の 2.1（CLI 実在判定）の結果。`resolve_kiro_flow`（`kiro-project.py:3033`）と
  対称な `resolve_codd_gate(explicit) -> list[str] | None` の戻り値をそのまま保持する。
  `[found]` または `[sys.executable, "<local>/codd-gate.py"]` の形（同梱パス経由のときは
  Python インタプリタを明示する必要があるため、単なる `str` ではなく argv prefix にする —
  `resolve_kiro_flow` が `str` ではなく `list[str]` を返す理由と同じ）。
- **`version`**: d1 の 2.2 の結果。タプル化済み（`(1, 0, 0)`）。取得失敗（timeout/exit≠0/
  パース不能）は `None`（「不明」を「大丈夫」に丸めない、という d1 の方針をそのまま型で表現：
  `None` は「わからない」であって「無条件 OK」ではない）。
- **`findings`**: d1 の 2.1〜2.3(a) で発生した finding をそのまま集約したリスト。
  **空リスト＝短絡パイプラインを最後まで通過＝連携可**、これ以外の意味を持たせない
  （severity で「使える/使えない」を分岐しない。d1 のフォールバック表は severity に関係なく
  全項目が「連携しない」に倒れるため、severity は doctor 表示の強さだけに使う）。
  d1 の 2.3(b)（`tasks` 出力の per-record `title` チェック）は**含めない**——理由は 5 節。

3 フィールドのみで、repos.json のパスや `--repo-dir` の実引数は**持たない**。これらはルーティング
情報であり、s6 の申し送り通り「引数の実引数生成は b2 の管轄」に属する。`CoddGateStatus` は
「codd-gate を使ってよいか／どう呼べば起動できるか」だけに責務を絞り、「何を指定して呼ぶか」は
各フックが自分の文脈（per-task の repo-dir、project 全体の repos.json パス）から組み立てる。

## 3. アクセサ

```python
@property
def usable(self) -> bool:
    return self.binary is not None and not self.findings

def command(self, *args: str) -> "list[str] | None":
    """引数を付けた argv を返す。usable でなければ None（呼び出し側の if 分岐を1行にする）。"""
    return [*self.binary, *args] if self.usable else None

@property
def reason(self) -> str:
    """スキップ理由の一文（journal・doctor 以外のログ向け）。usable なら空文字列。"""
    return self.findings[0]["title"] if self.findings else ""
```

- **`usable`**: 3フック共通の唯一のゲート条件。`binary is not None` を明示的に含めるのは、
  `findings` が空でも `binary` が未計算（呼び出し順序ミス）のケースを型で弾くため
  （`findings` 空かつ `binary` None は本来到達しない状態だが、`usable` の定義に両方含めておけば
  将来 `resolve_codd_gate_status` の実装が変わっても呼び出し側は壊れない）。
- **`command(*args)`**: `resolve_kiro_flow(cfg.kiro_flow) + ["--bus", str(cfg.bus)]`
  （`kiro-project.py:3269, 5781`）で既に使われている「argv prefix + 可変長引数」の連結パターンを
  そのままメソッド化したもの。3フックはこれ1つを呼ぶだけで、「codd-gate をどう起動するか」
  （PATH 経由か同梱パス経由か）を一切意識しなくてよい。
- **`reason`**: doctor 以外の場所（`append_journal` へのスキップ理由記録など）で `findings` の
  中身を毎回パースしなくて済むための糖衣。

## 4. 3フックでの使い方

いずれも `resolve_codd_gate_status(cfg, which=shutil.which, run=subprocess.run) -> CoddGateStatus`
（d1 提案の `doctor_codd_gate_findings` を内部実装として吸収し、`findings` を1回の計算で
両方の消費者——doctor 表示と3フックの自動配線——に供給する。呼び出しシグネチャの `which`/`run`
注入パターンは `doctor_env_findings(cfg, which=shutil.which)` に合わせる）を**1回だけ**呼び、
その結果を3箇所へ引数として渡す。計算・呼び出し箇所の詳細は6節。

### 4.1 regression（差分ゲート）— `cfg.regression_cmd`

`load_charter`（`kiro-project.py:7977-7982`）が `_apply_repo_registry` を呼ぶのと同じ
タイミングで、`cfg.regression_cmd is None` のときだけ次の形の文字列を組み立てて設定する
（既存の明示設定は上書きしない——d1 の「明示設定があれば自動判定自体をスキップ」を実装レベルでは
「上書きしない」という条件に落とす）:

```python
argv = status.command("verify", "--repos", str(repos_path),
                       "--repo-dir", f"{name}={path}", "--base", "$KIRO_BASE_REV", "--strict")
```

`--repo-dir`/`path` の実際の解決（自己ホスト構成か否かでの相対/絶対パス切替）は s6 の申し送り
通り b2 の管轄。この形は完了条件のシェルコマンド
（`codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base ... --strict`）
とサブコマンド・フラグの構成が一致しており、b2 が実装する際の直接の型になる。

### 4.2 acceptance（受入判定）— `Charter.acceptance`（ディスク非破壊）

`evaluate_acceptance`（`kiro-project.py:8937`）は `charter.acceptance`（`list[str]`）を
そのまま順に実行するだけで、コマンドの出自を区別しない。ここに codd-gate 由来の1行を混ぜたい
だけなら、**charter.md というユーザー authored ファイルを自動で書き換える必要はない**。
`_apply_repo_registry` が `ch.repo_specs` をパース後にインメモリで加工してから返す
（`load_charter` 内、ファイルには書き戻さない）のと同じパターンで、`Charter` オブジェクトの
`.acceptance` リストへ**パース後・返却前**に1行追記するだけで足りる:

```python
def _apply_codd_gate_acceptance(cfg: "Config", ch: "Charter", status: CoddGateStatus) -> "Charter":
    argv = status.command("verify", "--debt", "--repos", str(repos_path), "--strict")
    if argv:
        line = shlex.join(argv)
        if line not in ch.acceptance:
            ch.acceptance = [*ch.acceptance, line]
    return ch
```

こうすると `resolve_charter_acceptance` / `_failing_acceptance_specs`（`kiro-project.py:8980,
9016`）など**下流の既存機構を一切変更せずに**そのまま乗る——特に `_failing_acceptance_specs`
は未達 acceptance を `source="acceptance"` の backlog task spec に変換して積み直す
（s2 の指摘）ので、codd-gate 由来の acceptance 行が失敗すれば**自動的に**「負債取り込み」相当の
バックログタスクが生まれる。enqueue フック（4.3）を実装しなくても、acceptance 経由の再投入は
このパスだけで最低限機能する（ただし冪等排除は `run_intake` の id 一致方式より弱く、
`_acceptance_specs` はタスク文言から派生 id を作る既存ロジックに委ねる点は留意）。

`--debt`（プロジェクト全体の棚卸し）を選ぶのは、acceptance が「この差分が壊れていないか」
（regression の役割）ではなく「プロジェクトの現在地が一貫しているか」を問うため。`--max-*`
しきい値の既定値は b2 の政策判断（本書は形だけを定義する）。

**charter.md 自体は変更しない**設計を選んだ理由: charter.acceptance はユーザーが書く
「プロジェクトの完了条件」であり、repos.json（もとから「派生物・正は charter」と明記された
自動生成ファイル）と違って人が読む一次情報。自動追記した行が `_meta.generated_from` のような
出自マーカーを持てない（`list[str]` に構造化メタデータを載せる場所がない）ため、ディスクに
書くと「なぜ書いた覚えのない行が charter.md にあるのか」という混乱を生む。インメモリ加算に
留めれば、`charter.md` を見れば人が書いた受入条件だけが見え、実際の判定（`evaluate_acceptance`
呼び出し時の `ch.acceptance`）にだけ safety net が足される。

### 4.3 enqueue（負債取り込み）— `cfg.intake_cmd`

regression と同じタイミング・同じ「未設定のときだけ」条件で:

```python
argv = status.command("tasks", "--debt", "--repos", str(repos_path), "--repo-dir", f"{name}={path}")
if argv and cfg.intake_cmd is None:
    cfg.intake_cmd = shlex.join(argv)
```

以降は `run_intake`（`kiro-project.py:502`）の既存経路（id 一致で冪等排除、非 JSON/timeout は
journal に残してループ継続）がそのまま効く。`CoddGateStatus` はここでも「呼んでよいか」だけを
決め、`tasks` の stdout パース・per-record 検証には一切関与しない（5節）。

## 5. `findings` に含めないもの — 動的判定との境界

d1 の 2.3(b)（`codd-gate tasks` が返す各要素が `task.schema.json` の `title` 必須を満たすか）
は `CoddGateStatus.findings` に含めない。理由:

- `CoddGateStatus` はプロセス内で**1回計算してキャッシュする**セッション粒度の値
  （6節）。一方 2.3(b) は `tasks` を呼ぶ**都度**、返ってきた配列の**要素ごとに**判定が変わる
  （ある回は全件 `title` 付き、次の回は1件だけ欠落、ということが起こり得る）。この2つを
  同じ型に混ぜると、「一度 usable=true になったら以後ずっと信用してよい」という
  `CoddGateStatus` の不変条件が壊れる。
- 2.3(b) の失敗は d1 の方針通り「その1件だけ棄却、ループは止めない」。これは
  `run_intake` が個々の spec を `enqueue_task` に渡す直前でやるのが自然で
  （`kiro-project.py:539-548` の spec ループに1行足すだけ）、`CoddGateStatus` を経由させる
  理由がない。

依存方向で言うと、2.3(a)（kiro-project 自身が書く repos.json の出力契約チェック）は
「kiro-project → 静的自己点検」なので `CoddGateStatus` 計算時（起動時1回）に属するが、
2.3(b)（`tasks` の個々の出力）は「codd-gate → kiro-project」の**データの流れそのもの**の
一部であり、検出結果ではなく消費コードの防御的パースに属する。この境界線が
「セッション粒度の判定＝`CoddGateStatus`」「レコード粒度の検証＝各フックの消費コード」を
分ける基準になる。

## 6. 依存方向の定義（schemas 経由の疎結合を崩さない）

```
kiro-project.py ──(a) subprocess argv/exit code/stdout text──▶ codd-gate.py
       │                                                              │
       │ (b) 書く: repos.json（schemas/repos.schema.json 形）          │ 読む: --repos
       └──────────────────────────────────────────────────────────────┘
       │                                                              │
       │ 読む: tasks の stdout（schemas/task.schema.json 形）           │ 書く: tasks の stdout
       ◀──────────────────────────────────────────────────────────────┘

CoddGateStatus はどの矢印にも乗らない。resolve_codd_gate_status() の戻り値として
プロセス内メモリだけを移動する（ディスク化しない・schemas/ に3つ目のファイルを増やさない）。
```

守るべき規則:

1. **codd-gate.py は `CoddGateStatus` の存在を知らない**。import されない・参照されない
   （一方向）。codd-gate から見える kiro-project 由来の入力は `--repos` のファイル1つだけ
   （既存の (b) の矢印のまま）。
2. **schemas/README.md の2契約（repos / task）は変更しない**。3つ目のスキーマファイルを
   増設しない——`CoddGateStatus` に対応する「owner」は schemas/README.md の表
   （共有 / kiro-projects 所有）のどちらにも自然に当てはまらず、無理に追加すると
   「ツール同士は互いの実装を知らず、結合は常に一方向×データ」（schemas/README.md:4）という
   既存の不変条件を壊す（codd-gate 側が kiro-project 固有の内部型を意識する経路が生まれる）。
3. **計算は1回・消費は3箇所**。`resolve_codd_gate_status` は `load_charter`
   （`kiro-project.py:7977`、複数 charter 運用なら `:8102` も）から**1回だけ**呼び、
   `_apply_repo_registry` 適用後の `Charter`/`Config` に対して 4.1〜4.3 を適用してから返す。
   regression/acceptance/enqueue の各フックは自前で `subprocess.run([..., "--version"])`
   を呼び直さない——同一 run 内で「規制チェック時は usable だったのに数分後の enqueue 時は
   バイナリが消えていた」という一貫性の揺れを避け、かつ `--version` サブプロセスのコストを
   1プロセス生涯で1回に抑える。
4. **キャッシュの置き場所**: `Config` はデータクラスで長寿命の1インスタンスなので、
   `resolve_codd_gate_status` の結果は呼び出し元（`load_charter`）が最初の1回だけ計算し
   `cfg` の非公開属性（例 `cfg._codd_gate_status`）に保持して次回以降は再利用する案と、
   `_INTAKE_LAST`（`kiro-project.py:514-519`、intake_cmd の実行間隔スロットリングに使っている
   モジュールレベル dict）と同型の外部キャッシュにする案の両方が成立する。前者は
   `Config` が単純なデータの入れ物という既存の性格（frozen ではないが「設定値の集合」）から
   外れる懸念があるため、**後者（モジュールレベルキャッシュ、`cfg.codd_gate` 明示値か
   `cfg.backlog` をキーにする）を推奨**——実装方式の最終選択は b 系に委ねる。
5. **明示設定は自動配線より常に優先**（d1 を実装レベルで継承）: `cfg.regression_cmd` /
   `cfg.intake_cmd` が既に値を持つ場合、4.1/4.3 のブロックは `CoddGateStatus` の値に関わらず
   実行しない。4.2（acceptance）は追記であって上書きではないため、この「未設定のときだけ」
   条件を持たない（同じ行が charter.md に人力で書かれていれば `line not in ch.acceptance`
   の重複排除で二重にならない）。

## 7. 検証内容と結果

- `python3 -m pytest tools/kiro-project/tests -q -k codd` → `codd` 一致テスト0件で `exit=5`。
- 後続の `codd-gate verify --repos ./.kiro-project/repos.json ... --strict` は
  `.kiro-project/repos.json` が本 worktree に存在せず失敗（s1〜s3・d1 と同一原因、
  コミット `645d86f` での `.kiro-project/` 削除起因）。
- 本タスクはデータ構造・アクセサ・依存方向の設計文書化のみが成果物であり、上記完了条件を
  満たすコード（`resolve_codd_gate_status` の実装、3フックへの結線、`.kiro-project/repos.json`
  の復元、`codd` テストの追加）は書いていない——s1〜s3・d1 が確認した通りこの run の
  実装系タスクの責務であり、本書はその入力として書いた。ワークスペースは無変更
  （`git status --short` 差分ゼロ）。

## 8. 採用した前提・未解決事項・範囲外

- **前提**: 「regression/acceptance/enqueue の3フック」を、s1（`cfg.regression_cmd`）・
  s2（`Charter.acceptance` + `evaluate_acceptance`）・s3（`cfg.intake_cmd` 経由の
  `run_intake`）の各報告が特定した実装箇所と1:1で対応させた。s2 が挙げた3層のうち
  「acceptance」はプロジェクト全体の完了ゲート（`Charter.acceptance`）と解釈し、
  タスク単体 verify・人検収 review は対象外とした（元要求の文言「受入判定」が
  s2 の言う「プロジェクト done の唯一の根拠」に最も近いため）。
- **未解決事項**: 4.2 で提案した「charter.md 非破壊・インメモリ追記」は、対抗案
  （charter.md への実書き込み、repos.json と同じ `_meta.generated_from` パターンの適用）と
  比べて「何が受入条件か」が `charter.md` を見るだけでは分からなくなるトレードオフを持つ。
  `cmd_doctor`/CLI の `charter show` 相当のコマンドで「自動追加された acceptance」を
  可視化する手当てが要るなら、それは本書の範囲外（b系の追加検討事項）。
  `resolve_codd_gate_status` のキャッシュ方式（6節4.）も2案を提示するに留め決定していない。
- **範囲外で見つけた問題**: なし。本タスクは設計文書のみでコード変更を伴わない。
  s1 が指摘した `regression_revert` の workdir/vcwd 不一致、s6（未読だが d1 が言及）の
  `.kiro-project/` 削除は、いずれも本書のデータ構造設計とは独立の問題として追跡を委ねる。
