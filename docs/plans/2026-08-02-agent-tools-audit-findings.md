# agent-tools 監査所見（2026-08-02）

- 状態: 棚卸し（実装修正は一部済み。未修正・要追加調査を残す）
- 対象: `tools/agent-tools`（主に `agentcore`）および依存する呼び出し側
  （agent-project / agent-flow / agent-amigos / agent-dashboard / install.sh）
- 契機: agent-tools の実装バグ・仕様バグ探索
- 修正 PR: [#653](https://github.com/ynitto/sandbox/pull/653)（`cursor/agent-tools-bugfix-2cc4`）
- 上位文書: [コンセプト正典](../designs/agent-tools-concept.md) /
  [常駐一本化設計](2026-07-24-single-resident-controller-design.md) /
  [P2 契約一本化](2026-07-26-p2-contract-consolidation-detailed-design.md) /
  [積み残し総覧](2026-07-26-open-items-and-concerns.md)

**読み方**: §1 が結論。§2 が今回直したもの。§3 が**未修正**（優先度つき）。
§4 が**更なる調査が必要**なもの。§5 が検証で分かった非起因の既存失敗。
§6 が参照した証拠の所在。

---

## 1. 結論

agentcore を横断監査し、再現できた高確度の実装バグは PR #653 で直した。
副作用（原子書き込みの掃除互換、scoped commit の誤例外、ollama timeout、
agentcli の型握り潰し）も同 PR で緩和済み。

残るのは次の 3 類:

1. **仕様・設計レベルの未修正** — 局所パッチでは足りず、設計判断または複数モジュール
   の合意が要る（§3）
2. **更なる調査が必要** — 症状やずれは見えているが、影響範囲・採るべき正典が未確定（§4）
3. **査読で訂正した既存テスト** — main での失敗は実装不具合ではなく、
   先行する停止契約変更に期待値が追従していなかったもの（§5）

柱への効き方: 未修正の大半は柱 1（チーム分担の正しさ: claim / 板 / transport）か、
柱 2（人介在の前に機械が fail-close すること: verify / CLI 契約）に触る。

---

## 2. 今回直したもの（要約）

詳細は PR #653 と `CHANGELOG.md` Unreleased「agent-tools / agentcore: 監査で見つかった
実装バグの修正」を正とする。ここには一覧だけ置く。

| ID | 領域 | 直したこと |
|---|---|---|
| F1 | `protocol` | `extra` が `who` / `ts` / `lease_until` を上書きできた |
| F2 | `protocol` | 壊れた `lease_until` で `renew_lease` が ValueError |
| F3 | `protocol` / `commands` | 原子書き込みの一時名衝突。最終形は `<path>.tmp.<pid>.<unique>`（掃除・umask 互換） |
| F4 | `commands` | receipt payload が `ok` / `source` を偽装できた |
| F5 | `board` | 壊れた `requires` が制限なし扱い（fail-open）→ fail-close |
| F6 | `board` | 文字列 `contract_version` 無視 → パース、読めなければ不参加 |
| F7 | `verifycontract` | `confirm < 1` / `timeout_sec <= 0` を通していた |
| F8 | `verifycontract` | `exit_code != 0` + `inconclusive` が inconclusive 扱い |
| F9 | `repolocal` / dashboard | ユーザ無し SCP（`host:path`）をローカルパス化 |
| F10 | `repolocal` | 非 object の host 設定で `.get()` クラッシュ |
| F11 | `transport` | `user.email` だけある clone で `user.name` 未補完 |
| F12 | `transport` | `git add` / `commit` 失敗の握り潰し → 失敗は raise、no-op は index 差分で判定 |
| F13 | `agentcli` / `install.sh` | `~/.agents` 親存在ではなく `agents/` サブディレクトリ単位で新旧判定 |
| F14 | `agentcli` | `prompt_via` / `output` / `env` / `errors`（`[]`/`{}` 含む）の型・enum 検査 |
| F15 | `ollama_adapter` | timeout（既定 600s / `OLLAMA_TIMEOUT`）と非 dict 応答の拒否 |
| F16 | `agent-flow` cleanup | 新一時名接尾辞を残骸掃除が拾うよう正規表現を拡張 |

検証（PR 時点）: agentcore 両ルート緑、agent-flow 677 緑、agent-amigos 182 緑、
dashboard URL / agent-cli golden 緑。agent-project で当時 FAIL した 2 件は、査読で古い期待値と
判明して訂正し、1158 件が緑（§5）。

### 2b. 上記の追修正（追試で見つかった取りこぼし・副作用）

F1〜F16 を追試したところ、**fail-close の入れ方が「拾わない」ではなく「落ちる / 止まる」に
なっていた 2 件**と、**同じ性質を片側だけ直していた 2 件**が残っていた。テストはいずれも
修正前に落ちることを確認済み。

| ID | 領域 | 直したこと | 由来 |
| --- | --- | --- | --- |
| G1 | `transport` | subdir 未作成の初回 `sync_push` が pathspec エラーで RuntimeError。ステージ対象が作業ツリーにも index にも無いときだけ no-op（`_scope_absent`） | F12 の取りこぼし |
| G2 | `board` | `contract_version` が `NaN` / `Infinity` だと `int()` の例外が `eligible()` を貫通し入札巡回ごと停止 | F6 の取りこぼし |
| G3 | `protocol` | `winner()` 側の `_as_float` が `ts` 欠落・`null` を 0.0 と読み、壊れた claim が恒久的に勝つ。`NaN` も決定性を壊すため無視 | F2 が `renew_lease` 側のみだった |
| G4 | `agent-flow` stategit | 同期除外が `.tmp` 末尾のみで、実生成名 `<name>.tmp.<pid>[.<unique>]` の残骸を共有状態リポジトリへ push していた | F16 が掃除側のみだった |

G1 は `state_git_subdir` 運用（バスが毎パス `sync_push` を呼ぶ）で初回パスが必ず止まるため、
影響が最も大きい。G2 は `json` が既定で `NaN` / `Infinity` リテラルを受理する点が前提。

**教訓（次の監査へ）**: fail-open を塞ぐ変更は、「拾わない」に倒れているか「落ちる」に
倒れていないかを必ず対にして確認する。また、同じ値を読む関数が複数経路にある場合
（`_as_float` の `winner` / `renew_lease`、一時名の掃除側 / 除外側）は、片側だけ直すと
症状が別経路へ移るだけになる。

---

## 3. 未修正（仕様・設計バグ / 意図的に触らなかったもの）

着手するときは、コンセプト正典 §7 の作業ゲートを通すこと。ここにある項目は
「バグだと分かっているが、直すと契約・運用・複数エンジンに波及する」もの。

### 3.1 優先: 高 — 分担の正しさに直結

#### U1. 分散 claim の一過性二重勝者

- **何が起きるか**: 別ノード間では claim ディレクトリの file lock が共有されない。
  ノード A が sync 後に `try_claim=True` で作業を始めたあと、ノード B がより小さい
  `(ts, who)` の claim を push して sync すると、B も `True` を得うる。収束後の勝者は
  決定的だが、**既に始まった副作用は取り消されない**。
- **根拠**: `agentcore/protocol.py` のモジュール docstring 自体が「git 分散はクローンごとに
  別ロック」と明記。設計側は「claim が二重実行を防ぐ」と読める記述がある
  （[常駐一本化設計](2026-07-24-single-resident-controller-design.md) 周辺）。
- **なぜ今回直さないか**: ローカルの winner 再計算だけでは足りない。fencing トークンを
  副作用・成果境界で検証するか、リモート側の CAS / 直列化が要る。設計変更。
- **次の調査**: 実害が出る呼び出し境界の列挙（task claim / role claim / board bid の
  それぞれで「True のあと何を始めるか」）。canary で二重実行 0 を観測する項目との接続。

#### U2. 再クローン復元が未 push の「追跡ファイル更新」を捨てる

- **何が起きるか**: 破損回復（`_rebuild_clone`）は未 push ファイルを退避し、
  **再クローン後に存在しないパスだけ**書き戻す。リモートに既にある追跡ファイルへの
  ローカル未 push 変更は、リモート版に置換されて消える。
- **根拠**: `transport._restore_salvaged_files` のコメントが「既存ファイルは上書きしない」
  と明記。意図的だが、未 push の更新ロスという点では設計バグに近い。
- **なぜ今回直さないか**: 衝突時に「ローカルを残す / リモートを残す / conflict ファイルを
  残す」のどれが正かが未決。generation 原子 swap（U3）とセットで決めるべき。
- **次の調査**: 実運用で「壊れたあとに失われて困る」書き込み種別（claim / status /
  board bid / run meta）ごとの影響。

#### U3. 再クローンが generation 原子 swap ではない

- **何が起きるか**: 設計は横に新世代を作ってポインタを切り替える形を想定しているが、
  実装は live clone を in-place で消して作り直す。共有参照者は欠損や部分状態を見うる。
- **根拠**: [常駐一本化設計](2026-07-24-single-resident-controller-design.md) の回復記述と
  `transport._rebuild_clone` の実装差。
- **なぜ今回直さないか**: パス切替・Windows / WSL での rename 原子性・呼び出し側の
  workdir 保持の見直しが要る。U2 と同時に設計する方が安い。

#### U4. 入札選別が `workspace.path` / `workspace.base` を見ない

- **何が起きるか**: 契約上のリポジトリ同一性は `(url, path, base)`
  （`schemas/repos.schema.json` / `delegation.schema.json`）。
  `declared_repo_ids` / `eligible` は name と正規化 URL だけを比較する。
  同じ URL の別 path / 別 base を担当するノードが、別ワークスペースの公示に入札しうる。
- **根拠**: P2 詳細設計 §2.2 でも「入札判定は name と正規化 url だけ」と実測されている。
  当時は local 漏洩の話が主で、path/base 同一性までは閉じていない。
- **なぜ今回直さないか**: ノード宣言側に path/base をどう載せるか、legacy 宣言
  （url のみ）との互換、板スキーマの改訂が要る。CONTRACT_VERSION を上げる判断にも触る。
- **次の調査**: 実ボード上の公示で path/base が付いている割合。モノレポ運用の有無。

### 3.2 優先: 中 — 契約のずれ・静かな不参加

#### U5. URL 正規化が path まで全面小文字化

- **何が起きるか**: ホスト名だけでなく path も lower する。case-sensitive な forge では
  別リポジトリを同一視しうる。テストとコメントは「パス側も実運用で揺れる」として意図化。
- **根拠**: `repolocal.normalize_repo_url` / dashboard `normalizeRepoUrl` / ゴールデン表。
- **なぜ今回直さないか**: 規則変更は Python / JS / 既存 host.yaml / 板の照合を同時に
  動かす静止点が要る。誤って「一致しなくなる」方が現場症状として重い。
- **次の調査**: 利用中 forge の path 大小文字方針。ホストのみ lower にする互換パッチの可否。

#### U6. `requires: null` は今も unrestricted

- **何が起きるか**: 壊れた非 object の `requires` は F5 で fail-close にした。
  ただし明示の `null` は「要求なし」として通る。
- **根拠**: `board.eligible` の `is not None` ガード。
- **なぜ今回直さないか**: スキーマ上 null が来ない前提なら実害は小さい。厳格化するなら
  スキーマと生成側を同時に直す。
- **次の調査**: 板・dashboard・エンジンが `requires: null` を書きうるか。

#### U7. installer の `--only` と「同じ agentcore を共有」方針の緊張

- **何が起きるか**: README / install.sh は「3 本を別々に入れるな」と言いつつ `--only` を
  提供する。片方だけ古い agentcore を同梱した zipapp が並ぶと、状態の読み書きが噛み合わない。
- **なぜ今回直さないか**: 運用上の逃げ道としても使われている。廃止するか、
  混在を検出して拒むかの方針決定が先。
- **次の調査**: 自己更新・手順書・シムが `--only` に依存している箇所の列挙。

#### U8. speculation フィールドの文書・スキーマ併存

- **何が起きるか**: `delegation.schema.json` に `speculation` が残り、
  `board.schema.json` は未来機能扱い、常駐一本化設計は「未実装契約を削除した」と書く。
- **なぜ今回直さないか**: どれを正典にするかの文書作業。実装パスは既に無い想定。
- **次の調査**: スキーマ消費者（dashboard / 生成側）がキーを読んでいるか。読んでいなければ
  スキーマから削除して設計・CHANGELOG を揃えるだけでよい。

### 3.3 優先: 低〜中 — 検証・CLI 契約の穴

#### U9. `receipt_errors` が自称 `verdict` を再導出と比較しない

- **何が起きるか**: `verification-receipt.schema.json` は「宣言 verdict を再導出して比較する」
  と読めるが、`receipt_errors` は照合しない。現状の採用側は `receipt_overall` で再計算する
  ため実害は限定的。壊れた receipt が `receipt_errors` だけ通る経路が残る。
- **次の調査**: 採用側が `receipt_errors` だけ見て受理する経路の有無。

#### U10. verify policy の上限不足（NaN / 過大 confirm）

- **何が起きるか**: F7 で `confirm < 1` と `timeout_sec <= 0` は塞いだ。
  しかし `timeout_sec = NaN/Infinity` や極端に大きい `confirm` はまだ通る。
- **次の調査**: runner がどう解釈するか。スキーマに `maximum` / finite 制約を足すか。

#### U11. dashboard JS ローダと Python `agentcli` の厳格さの非対称

- **何が起きるか**: Python 側は F14 で enum/型を厳格化した。dashboard の JS ローダは
  不正な `prompt_via` / `output` を黙って stdin/stdout へ倒し、`env` / `errors` の型も緩い。
- **なぜ今回直さないか**: UI 応答性のための別実装であり、ゴールデンは argv 正常系中心。
  壊れた定義の共通ゴールデンを足してから揃える方が安全。
- **次の調査**: dashboard が読む定義の置き場と、壊れた定義を人が置いたときの症状。

#### U12. agent-amigos `matches_role` の CLI 要求が空宣言で fail-open

- **何が起きるか**: 板の `eligible` とは別経路のロール割当で、`requires.cli` があっても
  ノードの CLI 宣言が空だと要求を無視してマッチしうる（監査時の所見）。
- **本監査の扱い**: agentcore 外。板集約の対象外として残っている重複ロジック。
- **次の調査**: `assign.py` / daemon の渡し方を実測し、board と同じ fail-close に寄せる
  パッチの影響範囲を測る。

---

## 4. 更なる調査が必要なもの

着手パッチの前に、事実確認または設計選択が要る項目。

| ID | 題目 | 分かっていること | まだ分かっていないこと | 調べ方 |
|---|---|---|---|---|
| I1 | 分散 claim の実害境界（U1 の深掘り） | アルゴリズム上は二重 True がありうる | どのワークロードが True の直後に副作用を始め、どこで fencing が効くか | flow/amigos/project の `try_claim` 成功直後のコードパスを列挙し、成果書き込み前の再検証有無を表にする |
| I2 | 破損回復で失ってよい/いけない書き込み（U2/U3） | 新規ファイルは戻り、追跡ファイル更新は戻らない | バス上のどのファイルが「未 push のまま破損」しやすいか | canary / 障害注入で claim・status・bid を壊し、回復後の差分を取る |
| I3 | path/base を入札に入れる互換戦略（U4） | スキーマは 3 タプル、実装は url | legacy 宣言（url のみ）をどう読むか、CONTRACT_VERSION を上げるか | 実ノード宣言と公示サンプルを集め、欠落時の default（path=`/` / base=`main` 等）案を比較 |
| I4 | URL path の case fold 方針（U5） | いま全面 lower、テスト固定 | 利用 forge が path を区別するか | 運用中 remote URL の inventory。区別するならホストのみ lower への移行計画 |
| I5 | receipt verdict 再検算の契約（U9） | スキーマ文言と `receipt_errors` が不一致 | 採用側の単一入口はどれか | agent-project / agent-flow の receipt 受理呼び出しを全列挙 |
| I6 | Python/JS agentcli 契約の単一ゴールデン（U11） | 正常系 argv は揃っている | 異常系の期待（reject vs coerce）をどちらを正にするか | コンセプト的には fail-close。壊れた定義ケースを両言語で共有テーブル化 |
| I7 | install の混在版検出（U7） | `--only` と「共有せよ」が併存 | 実フィールドで混在が起きているか | zipapp 内 agentcore 版・CONTRACT_VERSION・file mtime の比較手段を設計 |
| I8 | transport の 30s lock 削除前提 | 「書き手は常駐 1 プロセス」前提で stale lock を消す | 前提が破れたときの誤削除 | 同一 workdir を触る外部 git/hook の有無をガイドと doctor で検出できるか |
| I9 | sparse-checkout 失敗の握り潰し | `sparse-checkout` 失敗でも checkout が成功しフル clone になりうる | 隔離破れの実害（秘密パス同梱等） | 失敗時に probe して managed flag を落とす案の影響 |
| I10 | `sync_pull` の「試みたか」戻り値 | 成功/失敗を呼び出し側が区別しにくい | behind/health 表示に足りる情報 | dashboard / doctor が欲しい信号を先に決める |
| I11 | heartbeat の未来時刻 | 未来の heartbeat はローカル時計が追いつくまで fresh | 時計ずれノードの影響 | 上位側で max-skew を見ているか確認 |
| I12 | agent-project の sticky cancel 残存（§5） | **査読済み**: テストの期待が古かった | `cancelled` の cancel は実行所有者が停止を確認するまで残す。終端 meta 書き込み直後に消すと、並行 heartbeat が古い meta を戻した場合に停止意図を失う | 実装の安全側の契約に合わせ、テストを sticky cancel の内容まで検査するよう訂正 |

---

## 5. 査読で訂正した既存テスト

agent-project 全件実行時に次の 2 件が失敗した。PR #653 の agentcore を main の内容に
戻しても同じ失敗を再現したが、査読の結果、これは未修正の実装バグではない。
`detach_flow_run` は、終端 meta の書き込みと実行所有者の停止確認の間に並行 heartbeat が
古い meta を書き戻す競合でも停止意図を保つため、`cancelled` の cancel マーカーを残す。
2026-08-02 の先行変更（`43fe377`）でこの契約になった一方、テストの古い期待値だけが残っていた。

| テスト | 症状 |
|---|---|
| `tests.test_commands.TestRevise.test_revise_offloaded_detaches_and_requeues` | 停止確認前の sticky cancel が残ることと、id / reason を検査するよう訂正 |
| `tests.test_commands.TestRevise.test_approve_offloaded_detaches_and_requeues` | 同上 |

実装を cancel 即時削除に戻すと上記の競合を再導入する弊害があるため、実装は変えず、
テストを現行契約へ合わせた。I12 はこれで決着とする。

---

## 6. 証拠・参照

### 6.1 コード（監査時点の主座）

- `tools/agent-tools/agentcore/agentcore/protocol.py` — claim / lease / 原子書き込み
- `tools/agent-tools/agentcore/agentcore/transport.py` — sync / 破損回復 / commit
- `tools/agent-tools/agentcore/agentcore/board.py` — 入札選別
- `tools/agent-tools/agentcore/agentcore/verifycontract.py` — plan / receipt
- `tools/agent-tools/agentcore/agentcore/repolocal.py` — URL 正規化 / host 宣言
- `tools/agent-tools/agentcore/agentcore/agentcli.py` — CLI 定義ローダ
- `tools/agent-tools/agentcore/agentcore/commands.py` — 指示ドロップ receipt
- `tools/agent-tools/install.sh` — 配布と agents ホーム
- `tools/agent-flow/agent_flow/cleanup.py` — `.tmp.<pid>` 残骸掃除
- `tools/agent-dashboard/src/features/agent-project/main/nodeRepos.js` — URL 正規化（JS）

### 6.2 スキーマ・設計

- `schemas/repos.schema.json` — `(url, path, base)`
- `schemas/delegation.schema.json` / `schemas/board.schema.json` — 板契約・speculation
- `schemas/verification-plan.schema.json` / `verification-receipt.schema.json`
- `schemas/agent-cli.schema.json` — agents ホーム判定（サブディレクトリ単位）
- [常駐一本化設計](2026-07-24-single-resident-controller-design.md)
- [P2 契約一本化](2026-07-26-p2-contract-consolidation-detailed-design.md)

### 6.3 修正の所在

- PR: https://github.com/ynitto/sandbox/pull/653
- CHANGELOG: Unreleased「agent-tools / agentcore: 監査で見つかった実装バグの修正」

---

## 7. 推奨する次の一手（この文書の使い方）

1. **設計判断が要る本丸は U1 → U2/U3 → U4** の順。いずれも柱 1。詳細設計を書いてから
   触ること。局所パッチで「winner をもう一度見る」程度では U1 は閉じない。
2. **安く閉じられる文書・契約ずれは U8 / U9 / U11**。実装より正典揃えが先。
3. この文書の項目を消化したら、行を「決着（PR / 設計）」と記して
   [積み残し総覧](2026-07-26-open-items-and-concerns.md) 側へ転記または参照を足す。
