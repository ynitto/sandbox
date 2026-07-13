# schemas/ 共通データ契約 — バージョン識別子・互換判定フィールド調査（s5）

対象: `schemas/repos.schema.json`, `schemas/task.schema.json`, `schemas/README.md`
（+ 傍証として `tools/codd-gate/codd-gate.py` の CLI 側バージョン表現を確認）

## 結論（要点）

**schemas/ の JSON データ契約そのものには、バージョン番号フィールドは存在しない。**
互換性は「バージョン一致判定」ではなく「追加のみ許可（additive evolution）＋未知キー無害化」という
**構造的規約**で担保されている。d1（自動検出の判定条件設計）が「schemas 互換判定」を実装する場合、
semver 比較ではなく **構造チェック（キー・型の許容範囲内か）** を軸に設計する必要がある。

## 1. バージョン識別子として使えるフィールド

| 場所 | フィールド | 値の例 | 性質 |
|---|---|---|---|
| `repos.schema.json` / `task.schema.json` | `$schema` | `http://json-schema.org/draft-07/schema#` | JSON Schema **メタスキーマ**のバージョン。データ契約自体のバージョンではない。実データファイル（`repos.json`/task JSON）には出現しない＝実行時の互換判定には使えない |
| 同上 | `$id` | `schemas/repos.schema.json` | スキーマファイルの識別子（パス）。バージョン番号ではなく「どの契約か」の識別のみ |
| **実データ（repos.json / task JSON）** | なし | — | `schemaVersion` / `apiVersion` 相当のフィールドは**存在しない**。両スキーマとも `required` にバージョン系キーを含まない |
| （参考・schemas/ 外）`tools/codd-gate/codd-gate.py:56` | `VERSION = "1.0.0"` | `"1.0.0"` | codd-gate **CLI 本体**のバージョン定数。`--version` フラグ（`codd-gate.py:984`）で `codd-gate 1.0.0` として取得可能。s4（CLI I/F 調査）の対象と重複するが、d1 の「バージョン取得」判定軸はここが実体 |
| （参考・schemas/ 外）`tools/codd-gate/codd-gate.py:603` | scan 出力 JSON の `"version": 1` | 整数 | codd-gate の**接続マップ内部フォーマット**のバージョン。schemas/ の共有契約（task/repos）とは別物で、codd-gate 内部にのみ閉じる |

→ 自動検出でバージョンを問う必要があるなら、参照すべきは **`codd-gate --version` の標準出力**（CLI 本体のバージョン）であり、
schemas/ 側にバージョンを問い合わせる手段は無い。

## 2. 互換判定に使えるフィールド・規約

### 2-1. 共通ルール（`schemas/README.md` §互換性の規則）

- 「未知キーは無視せず保持する（task）／無害に無視する（repos）。キーの削除・意味変更は不可、追加のみ可（additive evolution）」
- この規約自体が事実上の互換ポリシー。**両ツールが同一 git リポジトリ内の同一 `schemas/` を参照する構成**（README 冒頭）である限り、
  スキーマ改訂は追加のみ・破壊的変更なしが前提のため、通常運用でのバージョンスキュー懸念は小さい。

### 2-2. `repos.schema.json`

- **identity**: `(url, path, base)` の組。バージョンではないが「同一リポジトリか」の同定に使う実務上のキー（s6 の repo-dir マッピングと直結）。
- **repo エントリ定義に `required` が無い**（全フィールド任意）。→ 構造的な最低要件チェックが書けない。存在確認できるのは
  既知キー名（`url` / `base` / `path` / `owns` / `docs` / `tests` / `code` / `dir` / `local` / `readonly` / `target` / `desc`）が
  いくつ含まれるかというヒューリスティックのみ。
- **`additionalProperties: true`**（トップレベル・repo 定義の両方）→ 未知キーは無害。これが「互換判定＝バージョン一致ではなく寛容パース」の根拠。
- **予約プレフィックス**: トップレベルの `_` 接頭辞キー（例 `_meta`）はメタデータ予約で、全消費側が repo エントリとして扱わずスキップする規約。
  `_meta.generated_from` は「charter から自動生成された repos.json」の由来マーカー（README §repos）。
  これは唯一の「由来／生成経路」を示すフィールドで、バージョンではないが**互換判定の補助情報**（自動生成 vs 手書き）として使える。

### 2-3. `task.schema.json`

- **`required: ["title"]`** — 唯一の必須フィールド。**互換判定の実務的な最小基準はこれ**：
  オブジェクトが `title`（string）を持てば task スキーマ準拠とみなせる。
- `verify` / `accept` / `verify_template` は「done 確定の根拠」を表す3方式のいずれか（排他ではなくフォールバック関係）。
  自動検出結果を task 化する（e1/e2 の負債取り込み）際、これらのどれが埋まっているかで下流の扱い（即実行可能 verify か、
  triage 行きの inbox かなど）が変わる。
- **`additionalProperties: true`** + 「未知キーは保持する」（README）→ kiro-project 側が知らないキーを codd-gate が足しても
  データは失われない。バージョンアップ時の後方互換はこの一点で担保されている。
- `status` の enum（`inbox`/`draft`/`ready`/`doing`/`done`/`blocked`/`review`）は状態機械の許容値であり互換判定には直接使わないが、
  codd-gate 側が生成する task JSON がこの enum 外の値を書かないことは前提として守るべき制約。

## 3. d1（自動検出の判定条件設計）への示唆

1. 「schemas 互換判定」は **バージョン番号の比較では実装できない**（比較対象が存在しないため）。
   実装するなら「読み込んだ JSON が期待する最小構造を満たすか」の構造チェックにする:
   - repos.json: トップレベルが object で、`_` 始まりキーを除いた各値が object であること（enum 的な required は無いので型チェックのみ）
   - task 出力: 各要素が `title`（string）を持つこと
2. 実体のある「バージョン」を問うなら対象は **codd-gate CLI 自体**（`codd-gate --version` → `VERSION` 定数）であり、
   これは schemas/ の契約ではなく s4（CLI I/F 調査）の管轄。d1 では「CLI 実在検出」と「schemas 互換判定」を
   **別軸**として扱うべきで、両者を同じ「バージョン取得」ロジックに統合しない方がよい（データ契約側にバージョンが無い以上、
   統合すると存在しないフィールドへの参照が発生する）。
3. `_meta.generated_from` は repos.json が自動生成か手書きかを見分けられる唯一のメタ情報。互換判定そのものではないが、
   自動検出モジュールが repos.json の由来をログ/診断に出す際に使える。

## 未解決事項・範囲外で見つけた問題

- schemas/ 自体にはスキーマ改訂履歴やバージョンタグが無いため、将来 schemas/ に破壊的変更が入った場合の検知手段が
  現状存在しない（README の「追加のみ可」規約を守り続ける運用に依存）。これは s5 の範囲外（設計判断が必要）だが、
  d1 で「schemas 側が将来 non-additive な変更をした場合にどう検知するか」を明示的に「対象外」として書いておくと、
  後続の判断が楽になる。
- 本調査は `schemas/` 配下と `codd-gate.py` のバージョン表現のみを確認した。`tools/kiro-project` 側の task JSON 生成箇所
  （enqueue / inbox）が実際に `additionalProperties` を尊重した寛容パースになっているかは s1/s3 の管轄であり未確認。
