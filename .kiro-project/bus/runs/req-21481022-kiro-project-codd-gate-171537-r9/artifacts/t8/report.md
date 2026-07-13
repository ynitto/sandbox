# t8: codd-gate 検出（detect）関数の実装

## 成果

`tools/kiro-project/codd_gate_detect.py`（PATH 探索 `resolve_codd_gate` / バージョン取得
`get_version`）は commit 38f99cac で既にマージ済みで、単体としては各々「例外を投げず None/失敗
値に縮退する」設計が完了していた。ただし合流点である `codd_gate_status.detect_status` は
PATH 探索（`resolve_codd_gate`）のみを行い、バージョン確認（`get_version`）を素通りして
常に `version_known=True` を build_status に渡していた（同関数の旧 docstring 自身が
「バージョン取得はまだ合流していない」と明記）。

タスク t8 の要求「PATH 探索＋バージョン確認をまとめて行い、未インストール時は None
（=usable時に comand() が None）を返し例外を投げない detect 関数」を満たすため、
`detect_status` を次のように拡張した（`tools/kiro-project/codd_gate_status.py`）:

1. `run=subprocess.run` を DI パラメータとして追加（`get_version` と同じ注入パターン）。
2. `resolve_codd_gate` で PATH 探索 → 見つからなければ即 `build_status(None)`（未検出縮退、変更なし）。
3. 見つかった場合のみ `get_version(binary, run=run)` を実行し、成功すれば実バージョンを、
   失敗（timeout・非0終了・パース不能）すれば `None` を `build_status` に渡す
   （`version_known = version is not None`）。
4. `get_version` は例外を投げない設計だが、想定外の例外（`run` の差し替え失敗等）に
   備えて `detect_status` 側でも try/except で捕捉し、「バージョン不明」へ縮退させる。
5. schemas 互換判定（`check_repos_schema_compat`）は repos_path という呼び出し文脈依存の
   引数が要るため、意図的にこの関数へは含めない（既存設計方針を維持。呼び出し側が
   `build_status(binary, version=..., version_known=..., schema_ok=...)` を直接呼べば合流できる）。

これにより `detect_status(explicit=cfg.codd_gate_bin)` の1呼び出しだけで「PATH 探索＋
バージョン確認」の両方を安全に完了できるようになり、t7 の結線仕様（3フック共通で
`detect_status` を1回呼ぶ設計）がそのまま成立する。

## 変更ファイル

- `tools/kiro-project/codd_gate_status.py` — `detect_status` にバージョン確認を統合（上記）
- `tools/kiro-project/tests/test_codd_gate_detect.py` — 統合後の挙動に合わせてテスト更新・追加
  - 既存 `test_cli_present_and_version_compatible_is_usable`: `detect_status` 呼び出しに
    `run=` を注入するよう更新（バージョン確認が実際に行われるようになったため）
  - 新規3件: PATH ありバージョン非互換／PATH ありバージョン不明／`run` が想定外の例外を
    投げても `detect_status` が「未検出」に縮退することを検証

`codd_gate_detect.py` 自体（`resolve_codd_gate` / `resolve_codd_gate_bin` / `get_version` /
`detect_capabilities` / `check_repos_schema_compat`）は既存実装のまま変更していない
（PATH 探索・バージョン確認の個別プリミティブは既に完了条件を満たしていたため）。

## 検証

- `python3 -m pytest tools/kiro-project/tests -q -k codd` → **50 passed**（既存47 + 新規3、
  3 subtests passed）。exit 0。
- `codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base HEAD~1 --strict`
  → `OK: 一貫性ゲート通過`。exit 0。
- 完了条件コマンド（上記2つを `&&` で連結）を実行し、最終 exit code 0 を確認。

## 前提・判断

- t7 の結線仕様がフック側（t11/t16/t19、未着手）で `codd_gate_status.detect_status(explicit=cfg.codd_gate_bin)`
  を「検出は3フック共通で1回だけ」の唯一の入口として使う設計であることを確認した上で、
  この関数自体がタスク名の要求（PATH＋バージョン確認・None 縮退・無例外）を満たしていない
  状態（バージョン確認が欠落）を見つけ、範囲内の最小差分として修正した。
- `kiro-project.py` 本体への結線（`cfg.codd_gate_*` 設定・3フックへの配線）は本タスクの
  範囲外（t9/t11/t16/t19 の責務）であり、一切触れていない。
- schemas 互換判定を `detect_status` に含めない判断は既存設計（d1/d2）を踏襲したもので、
  変更していない。

## 範囲外で見つけた問題

なし。`codd_gate_base.py` / `codd_gate_routing.py` / `codd_gate_invoke.py` / `codd_gate_debt.py`
には手を入れておらず、目視確認した範囲でも本タスクに関連する不整合は見つからなかった。
