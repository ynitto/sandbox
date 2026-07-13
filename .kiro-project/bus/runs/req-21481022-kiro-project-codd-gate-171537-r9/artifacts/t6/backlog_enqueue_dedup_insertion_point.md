# backlog enqueue / id 採番 / 重複判定 — ドリフト項目の投入先調査

対象: `tools/kiro-project/kiro-project.py`（**`kiro_project/model.py` という別ファイルは
リポジトリ内に存在しない**。kiro-project は単一ファイル実装で、Task 定義・enqueue・重複判定は
すべて `kiro-project.py` に同居している。以下はこのファイルの行番号で示す）。

## 前提の訂正

タスク文面は `kiro_project/model.py` を指すが、`find` で全リポジトリを走査しても該当ファイルは
無い（`tools/kiro-project/` 配下は `kiro-project.py` 1 本 + `codd_gate_*.py` 5 本の構成）。
`model.py` はモジュール分割後を想定した仮称、または誤記と判断し、実体である
`tools/kiro-project/kiro-project.py` を調査対象として読んだ。

---

## 1. backlog タスクのスキーマ

### 1.1 実行時表現（`Task` dataclass, L84-118）

```python
@dataclass
class Task:
    id: str
    title: str
    status: str = "ready"
    source: str = "human"
    priority: int = 0
    verify: str = ""
    retries: int = 0
    extra: "list[tuple[str, str]]" = field(default_factory=list)
```

`status` は `VALID_STATUS`（L48-49）に閉じる。`extra` は未知/追加キーを `(key, value)` の
タプル列としてそのまま保持する（`get`/`set`/`drop` で操作、L107-117）。

### 1.2 永続化形式

1 ファイル = 1 タスク（`backlog/<id>.md`）。`serialize_task`（L175-185）が
`## <id>: <title>` ヘッダ + `- key: value` 行群に落とし、`parse_task`（L142-172）が
その逆をやる。**id はファイル名（stem）が正**（L143 のコメント通り）。

### 1.3 JSON 契約（intake/enqueue の入力形）

`schemas/task.schema.json` が正典。`required: ["title"]`、`additionalProperties: true`
（未知キー保持）。`id` の説明に明記されている一文がそのままこの調査の結論:

> 「タスク id（省略時は title から生成）。**定期投入（intake_cmd）では冪等キーになる**」

このスキーマの説明文には `intake_cmd の stdout / codd-gate tasks の出力` も JSON 表現の一つとして
明示されており、codd-gate 由来のドリフト項目がこの契約に乗ることは設計時点で織り込み済み。

---

## 2. id 採番ロジック（enqueue 経路）

呼び出し順: `enqueue_task(cfg, spec)` (L290) → `task_from_spec` (L250) → `_gen_task_id` (L239)。

- **明示 id あり**（`spec["id"]` が非空）: `_gen_task_id` は改名せず
  `_unique_task_id(cfg, _slug_id(explicit))` で **backlog 内の衝突だけ** 回避する
  （L240-243 のコメント: 「明示 id は冪等キーなので改名しない」）。衝突時は `-2`, `-3`…と
  サフィックスを振る（`_unique_task_id`, L222-236）。
- **id 省略**: title を `_slug_id` でスラグ化し `<slug先頭24字>-<HHMMSS>` を基底 id にする
  （空 title 相当なら `enq-<YYYYMMDD-HHMMSS>`）。この経路は `include_archive=True` で
  archive も衝突回避対象に含める（L245-247）。
- `_slug_id`（L217-219）: `[^A-Za-z0-9_-]+` を `-` に置換し 48 字で切り詰め
  （schema の説明と一致）。

**重要**: `_gen_task_id`/`_unique_task_id` は「ファイル名衝突の回避」のみを行い、
「意味的に同じ発見だから作成しない」という判定はしない。後述の重複判定はこれとは別レイヤ。

---

## 3. 既存タスクとの重複判定ロジック — 2系統ある

コードベースには重複防止が**2つの独立した仕組み**として存在し、どちらもこの単一ファイル内。
ドリフト項目の投入先としてどちらに乗るかで挙動が変わるため両方を記録する。

### 3.1 `run_intake`（L502-554）— 外部ゲート/検出器向けの汎用フック（★ 本命の投入口）

```python
def run_intake(cfg: "Config") -> "list[Task]":
```

docstring 冒頭（L502-511）に **`codd-gate tasks --debt` が名指しの想定用途**として書かれている:

> 「外部の決定的ゲート/検出器（例: `codd-gate tasks --debt`）を watch の周期で汲み上げる汎用フック」

処理フロー:
1. `cfg.intake_cmd`（Config, L4609: `intake_cmd: "str | None" = None`）が未設定なら即 `[]`（no-op）。
2. `cfg.intake_interval`（L4610, 既定 600 秒）で律速。プロセス内 dict `_INTAKE_LAST`（L499）に
   backlog パスをキーに最終実行時刻を持ち、間隔未満なら実行しない。
3. `subprocess.run(cfg.intake_cmd, shell=True, cwd=cfg.workdir, timeout=cfg.verify_timeout)` で
   単発実行。非 0 終了・例外・非 JSON はすべて `append_journal` に記録して **無視**（ループを殺さない）。
4. stdout の JSON を `data if isinstance(data, list) else [data]` で配列化
   （`codd_gate_debt.parse_debt_output` の「object でも array でもよい」という設計と対称）。
5. **重複判定（id ベース・厳密一致）**（L538, L542-544, L550-551）:
   ```python
   existing = {f.stem for f in cfg.backlog.glob("*.md")} if cfg.backlog.exists() else set()
   for sp in (data if isinstance(data, list) else [data]):
       ...
       sid = _slug_id(str(sp.get("id", "") or ""))
       if sid and sid in existing:
           continue                        # 冪等: 現役 backlog に居る発見は再投入しない
       created.append(enqueue_task(cfg, sp))
       ...
       if sid:
           existing.add(sid)               # 同一 run 内の連続投入にも反映
   ```
6. 呼び出し箇所は2つ: 通常ループ先頭 `inboxed = run_intake(cfg) + ingest_inbox(cfg)`（L5608）と
   idle ポーリング内（L6795）。**watch の周期に自動的に乗る**。

**この重複判定の性質**:
- 照合キーは `id` のみ。`_slug_id(spec["id"])` が **現役 backlog（`archive/` は含まない）** の
  ファイル stem 集合に含まれるかだけを見る。title の類似度は一切見ない。
- **id が空/欠落なら重複判定は完全にスキップされる**（`if sid and sid in existing` は sid が
  空文字なら False）→ その spec は無条件で `enqueue_task` に渡り、`_gen_task_id` の
  タイムスタンプ生成分岐（L244-247）に落ちて**毎回新しい id**になる。つまり **codd-gate 側が
  ドリフト項目ごとに安定した `id`（例: ファイルパス+シンボル+検出種別のハッシュ）を発行しない限り、
  この冪等機構は機能せず、同じ発見が poll 毎に新規タスクとして積まれ続ける**。
- done→archive 済みの発見は再チェック対象に入らない（archive は見ない）ため、同じ発見が
  再発した場合は新タスクとして積み直せる（docstring L508 の意図通り）。裏を返すと、
  「人が明示的に却下した（archive/rejected）」発見と「まだ直っていないだけ」の発見を
  `run_intake` は区別しない（次項 3.2 の `active_only` 相当の保護は無い）。

### 3.2 `_is_duplicate` / `_existing_titles` / `_enqueue_specs`（L8900-8902, L8868-8897, L9258-9289）
— charter 駆動の plan/evaluate 専用（**run_intake からは呼ばれない**）

```python
def _is_duplicate(title: str, verify: str, existing: "list[str]", threshold: float) -> bool:
    return any(_title_overlap(title, e) >= threshold for e in existing)
```

- `_title_overlap`（L956-961）は語集合の Jaccard 係数。閾値は `cfg.learn_threshold`
  （既定 0.5, L10248）。
- `_existing_titles`（L8868-8897）が照合対象を用意: **backlog + archive 両方**のタイトルを集め、
  `charter` 引数を渡すとそのタグ一致（またはタグ無し）に絞る。`active_only=True` なら
  「done 以外の backlog」＋「archive の rejected のみ」に絞り込む（却下済みの復活だけは防ぎ、
  done 済みのやり直しは許す、という 3.1 には無い区別をここでは持つ）。
- `_enqueue_specs`（L9258-9289）が実際の投入ループ: spec 群を1件ずつ `_is_duplicate` でスキップ
  判定し、通過したものだけ `enqueue_task` を呼ぶ。呼び出し元は `_project_evaluate`
  （L9753 以降、acceptance 未達タスクや敵対的レビュー findings の投入、L9777/9782）と
  charter の初回分解（L9962 付近）。**codd-gate ドリフト検出はこの経路の対象外**
  （charter/project サブコマンドの内部専用）。

### 3.3 CLI `cmd_enqueue`（L7979-8023）は重複判定なし

人間/外部アダプタが `kiro-project enqueue --json` を直接叩く経路。`enqueue_task` を
そのまま呼ぶだけで、id 一致チェックも Jaccard チェックも行わない（呼び出し側が冪等性の
責任を持つ設計）。

---

## 4. codd-gate 側の既存実装との整合（`codd_gate_debt.py`）

`tools/kiro-project/codd_gate_debt.py`（同ディレクトリ、今回のスコープ外だが投入形の
根拠として確認済み）は `codd-gate tasks --debt` の stdout を `DriftItem` へ正規化し、
`DriftItem.to_spec()`（L45-51）で

```python
spec = {"title": self.title}
if self.id:
    spec["id"] = self.id
spec.update(self.fields)
```

という **`run_intake`/`enqueue_task` がそのまま受け取れる dict** を返す設計になっている
（docstring L46 に明記）。同ファイルの docstring（L20-23）は「kiro-project.py への結線・
`cfg.intake_cmd`/`run_intake` との統合、id ベースの冪等排除」を**意図的にこのモジュールの
責務外**としており、3.1 節の `id` 空欄時の脱落問題（poll 毎に新規タスク化する不具合の芽）は
結線タスク側で対処が必要な既知の隙間として残っている。

---

## 5. 結論（投入位置・重複防止の要約）

| 項目 | 内容 |
|---|---|
| **ドリフト項目の投入位置** | `run_intake(cfg)`（L502-554）。`cfg.intake_cmd` に `codd-gate tasks --debt`（相当）を設定すれば、watch ループ（L5608）と idle poll（L6795）の周期で自動的に汲み上げられる。CLI 手動投入なら `cmd_enqueue`（L7979）経由でも同じ `enqueue_task` に到達するが冪等性は呼び出し側任せ |
| **投入形式** | `schemas/task.schema.json` 準拠の dict（`title` 必須、`id` は冪等キー、他は自由）。`codd_gate_debt.DriftItem.to_spec()` が既にこの形を返す |
| **id 採番** | 明示 id は `_slug_id` 後そのまま冪等キーとして使う（衝突時のみ backlog 内で `-2`, `-3`…採番）。省略時は title スラグ+時刻から自動生成（`_gen_task_id`, L239-247） |
| **重複防止（intake 経路）** | `run_intake` 内の id 完全一致チェック（L542-544）。**backlog のみ参照・archive は見ない・id が空だと機能しない** |
| **重複防止（別経路、参考）** | `_is_duplicate`（Jaccard タイトル類似度 ≥ `learn_threshold`）は charter の plan/evaluate 専用で `run_intake` からは呼ばれない。より緩い/賢い判定が要るならこちらへの合流も選択肢だが、現状は配線されていない |
| **次工程（結線実装）への申し送り** | codd-gate が発行する `id` は決定的・安定（同一ドリフトなら再検出時も同じ id）である必要がある。そうでないと 3.1 の脱落経路で poll 毎に重複タスクが積まれる。加えて `run_intake` には rejected 除外が無いため、人が却下したドリフトを再提案させたくない場合は結線側で `find_avoidance`（L1031-1040, avoid 学習）や `active_only` 相当のフィルタを追加で挟む必要がある |

---

## 検証内容

- `find` によるファイル探索: `tools/kiro-project/` 配下に `model.py` は存在しないことを確認（コマンド実行、非対話）。
- `grep -n` で `enqueue`/`重複`/`dedup`/`duplicate` 関連シンボルを洗い出し、該当箇所を `Read` で全文確認（L1-330, L940-1060, L8840-9020, L9250-9340, L7979-8023, L502-554）。
- `schemas/task.schema.json` と `codd_gate_debt.py` を突き合わせ、投入形式の契約が既に整合していることを確認。
- コード変更は行っていない（調査のみのスコープ）。テスト実行は本タスクの完了条件に含まれない
  （`enqueue`/重複判定への実装変更は別タスクの担当のため、現時点で pytest 等を回しても本タスクの
  正否には無関係と判断し実行していない）。

## 前提・未解決事項・範囲外で見つけた問題

- **前提**: `kiro_project/model.py` は実体不在のため `tools/kiro-project/kiro-project.py` を対象とした（上記「前提の訂正」参照）。
- **範囲外で見つけた問題**（直さず記録のみ）:
  1. `run_intake` の重複判定は `id` が空だと素通りする（3.1 節）。codd-gate 側が id を必ず安定発行する契約になっているか未確認 — 結線タスクでの確認・保証が必要。
  2. `run_intake` は archive の `rejected` を除外しない。charter 経路の `_existing_titles(active_only=True)` にある「却下済みは復活させない」保護がここには無い。
