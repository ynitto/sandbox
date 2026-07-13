# t8: codd-gate バージョン確認関数（`check_availability`）

**差別化の切り口**: 可用性判定を「`get_version` の生の判定を bool に丸めるだけの薄いラッパー」
に留め、キャッシュは `(tuple(binary), timeout)` をキーにした辞書にした。単一グローバルフラグ
（compute-once）にせず入力でキー分けすることで、(a) 本番で `codd_gate_bin` 設定を変えて
複数回解決し直しても誤ったキャッシュ命中をしない、(b) 同一プロセス内テストが異なる binary /
フェイク `run` を使い回しても互いに汚染しない、という2つの正しさを両立させた。加えて
`binary=None`（未検出）を関数内で直接吸収し、`check_availability(resolve_codd_gate(...))` と
そのまま連結できるようにした（呼び出し側に None ガードを書かせない）。

## 成果

`tools/kiro-project/codd_gate_detect.py` に以下を追加。

- `check_availability(binary: list[str] | None, run=subprocess.run, timeout=PROBE_TIMEOUT) -> tuple[bool, tuple[int,int,int] | None]`
  - `binary is None` なら `run` を一切呼ばず `(False, None)` を即返す。
  - それ以外は `get_version(binary, run=run, timeout=timeout)` を呼び、`version is not None` を
    `available` として `(available, version)` を返す。プロセス起動失敗（`OSError`）・timeout
    （`subprocess.TimeoutExpired`）・非ゼロ終了・パース不能は `get_version` 側で既に「不明」
    （`None`）に丸められているため、ここでは単純にそれを `available=False` へ写像するだけ
    （d1 の「わからない」を「大丈夫」に丸めない方針を踏襲、新しい判定ロジックは増やさない）。
  - モジュール変数 `_availability_cache: dict[tuple, tuple[bool, version|None]]` に
    `(tuple(binary), timeout)` をキーとして結果を保存し、同一プロセス内の再呼び出しでは
    `subprocess.run` を再度起動しない。
- `clear_availability_cache() -> None` — キャッシュ全体を破棄する（テスト・長時間常駐プロセスの
  再検出用）。

`tools/kiro-project/tests/test_codd_gate_detect.py` に `TestCoddGateCheckAvailability`
（8ケース: 成功時 available、非ゼロ終了・timeout・起動失敗がすべて unavailable、
binary=None で run 未呼び出し、2回目呼び出しでキャッシュ再利用（run 呼び出し回数で検証）、
異なる binary は互いのキャッシュ枠を汚染しない、`clear_availability_cache` 後は再プローブする）
を追加。各テストは `setUp` で `clear_availability_cache()` を呼び、他テストが
`["codd-gate"]` という同じ binary キーを使い回してもキャッシュ汚染しないようにした。

### 依存タスク t7 との整合について（範囲外の所見）

作業開始時、渡された worktree の `git status` は detached HEAD で `[kiro-flow] t18` コミット
（同一 run の別タスク系列）を指しており、依存タスク t7 の成果（`resolve_codd_gate_bin` の追加、
コミット `10dcfbf`）を含んでいなかった（`git log --all` には存在するが HEAD の祖先ではない）。
`check_availability` は `resolve_codd_gate_bin` に依存しないため実装自体は独立に成立するが、
ファイルの一貫性を保ち完了条件のコマンドをこの worktree で実際に検証できるようにするため、
t7 の差分（`resolve_codd_gate_bin` 本体・モジュール docstring・対応テスト
`TestCoddGateResolveBin`）を報告済みの内容のまま再適用した上で `check_availability` を追加した。
worktree の base 不整合自体はこのタスクの範囲外（orchestration 側の問題）であり、synth 段階で
実際の t7 コミットとの整合を取ることを想定する。

## 検証内容と結果

- `python3 -m pytest tools/kiro-project/tests -q -k codd` → **45 passed**
  （t7 適用後の37 + 本タスクの新規8）
- 完了条件コマンド一式（pytest -k codd && `codd-gate verify --repos ./.kiro-project/repos.json
  --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict`）→ **exit 0**
  （一貫性ゲートも `tools/kiro-project/codd_gate_detect.py` / 対応テストの両方が GREEN）
- `git status --short` / `git diff --stat` で変更ファイルが `codd_gate_detect.py` と対応テスト
  ファイルの2件のみであることを確認（範囲外変更なし）

## 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 「可用性」は本タスクの文言通り「`--version` が成功しバージョン文字列をパースできる
  か」の1点に限定し、`MIN_SUPPORTED_VERSION` とのセマンティックバージョン比較（下限判定）は
  含めない。下限判定は `codd_gate_status.py` の `build_status` が既に担っており、
  `codd_gate_detect.py` 自身の docstring が「使ってよいかの判断はしない・生の判定に絞る」と
  明記しているため、責務の重複を避けた。
- **前提**: キャッシュはプロセス内・無期限（TTL なし）とした。a3 の申し送り「1実行中に複数回
  走らないように」という要求は「実行の終わりまで有効」を意味すると解釈した。長時間常駐する
  プロセス（kiro-flow デーモン等）で codd-gate の入れ替えを検知したい場合は
  `clear_availability_cache()` を呼び出し側が明示的に叩く前提。
- **前提**: キャッシュキーに `run`（DI されたコーラブル）を含めない。本番では
  `subprocess.run` 固定でバイナリ・timeout だけが変数のため、これで十分に正しい。テストが
  異なる `run` フェイクを使い回す場合は `setUp` での `clear_availability_cache()` 呼び出しで
  分離する規約にした。
- **未解決事項**: `check_availability` を実際に呼び出す配線（kiro-project.py 本体・
  `codd_gate_status.py` の `build_status` への合流）は本タスクの範囲外（t7 の報告と同じ理由で
  「検出関数の実装のみ」がこのタスクの完了条件）。合流するなら
  `build_status(binary, version=..., version_known=available, ...)` の形で
  `check_availability` の戻り値をそのまま渡せる設計にしてある。
- **範囲外で見つけた問題**: 上記の t7 依存不整合（worktree の base コミット不一致）以外なし。
