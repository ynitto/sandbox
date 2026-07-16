# t2: codd-gate 自動検出・フォールバック挙動 — 実環境ライブプローブによる確認

**差別化の切り口**: 静的なコードレビューではなく、この run の実行環境（コード実体はメイン worktree
`/Users/nitto/Workspace/sandbox`）で `resolve_codd_gate` / `detect_status` を実際に3通り発火させ、
検出→フォールバックの各分岐が返す生の値（起動コマンド・usable・reason・finding）を実測値として提示する。
そのうえで「未検出時のフォールバック挙動」をコードの分岐と1対1対応する契約表として定義する。

## (a) 成果サマリー

t1 の調査（自動検出ロジックは `tools/agent-project/codd_gate_detect.py` の `resolve_codd_gate` に
実装・テスト済み、新規実装は不要）を前提として採用した。t2 の作業は以下の2点に限定した。

1. **この run の実環境での動作を実測で確認**（モックではなく本物の `shutil.which` / ファイルシステム）。
2. **未検出時のフォールバック挙動を、呼び出し側が参照できる契約表として明文化**（コード自体は
   変更していない。定義はコードの実分岐から逆に起こしたものであり、新設した仕様ではない）。

### フォールバック契約表（`resolve_codd_gate` → `codd_gate_status.detect_status` の合流）

| 検出分岐 | 解決される起動コマンド | `usable` | 呼び出し側への影響 |
|---|---|---|---|
| `explicit` 引数指定 | `[explicit]`（`.py` なら `[sys.executable, explicit]`） | version/schema 判定に従う | 明示指定なので PATH 探索はスキップ |
| PATH 上に `codd-gate` あり | `[which の結果パス]` | version が `MIN_SUPPORTED_VERSION=(1,0,0)` 以上かつ既知なら True | `regression_cmd`/`intake_cmd` を実行可能 |
| PATH に無いが同梱パス `tools/codd-gate/codd-gate.py` あり | `[sys.executable, 同梱パス]` | 同上 | 同上（PATH 未整備環境向けの縮退） |
| PATH にも同梱パスにも無い | `None` | `False`（`command()` は常に `None`） | info finding 1件を生成し `agent-project` 本体は無停止で継続（no-op 縮退） |
| バージョンが `MIN_SUPPORTED_VERSION` 未満 | 起動コマンドは解決されるが | `False` | warn finding 1件、`command()` は `None` |
| バージョン不明（`--version` timeout・非0終了・パース不能） | 同上 | `False` | info/warn finding、`command()` は `None` |

「見つからない・分からない」は例外化せず一貫して `usable=False` の no-op 縮退に倒す設計であり、
charter 制約「人による操作や確認を最小限にする」（codd-gate 未導入環境でも agent-project が壊れない）
を満たす。

## (b) 検証内容と結果

| 検証項目 | 方法 | 結果 |
|---|---|---|
| 既存ユニットテスト再実行 | `python3 -m unittest discover -s tests -k codd_gate`（`sandbox/tools/agent-project`） | **81 passed**（t1 と同じ件数を独立に再現） |
| PATH 検出の実測（本物の `shutil.which`） | `resolve_codd_gate()` を無引数で実行 | `['/Users/nitto/.local/bin/codd-gate']` を解決。`codd-gate --version` 実体は `codd-gate 1.0.0` |
| `get_version` の実測 | 上記 binary に対し `get_version()` | `(1, 0, 0)`（`MIN_SUPPORTED_VERSION` と同値=境界値、互換） |
| `detect_status()` 総合の実測（この run の実環境） | 無引数で実行 | `usable=True`, `reason=''`, `findings=[]`, `command('verify','--base','X')` → `['/Users/nitto/.local/bin/codd-gate', 'verify', '--base', 'X']` |
| フォールバック段2（PATH無し・同梱パスあり）の実測 | `which=lambda _: None` を注入（ファイルシステムは実物のまま） | 同梱パス `tools/codd-gate/codd-gate.py` へ実際にフォールバックし `usable=True` を確認 |
| フォールバック段3（完全未検出）の実測 | `which=lambda _: None` + `Path.exists=False` を注入 | `usable=False`, `reason='codd-gate が見つからない（PATH・同梱パスのいずれにも無い）'`, `command(...)` は `None`, info finding 1件を確認 |
| この run の control-plane 設定に対する `regression_wired`/`intake_wired` の実行 | `.agent/agent-project.yaml` の `regression_cmd`/`intake_cmd` を正規表現で抜き出し `codd_gate_wiring.regression_wired`/`intake_wired` に通す | 両方とも `True`（grep だけでなく判定関数そのもので整合確認） |

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- t1 の方針決定どおり、t2 の責務は新規実装ではなく「既存実装のこの run 環境での動作確認」と
  「フォールバック挙動の明文化」に限定した。コード（`codd_gate_detect.py` 等）への変更は行っていない。
- 完了条件grep自体（`agent-project.yaml` というパスの不一致）の是正は t6（統合）の責務であり、
  t1 の申し送りどおり t2 の範囲外とした。

**範囲外で見つけた問題（報告のみ）**
- 参照専用リポジトリ `/Users/nitto/Workspace/sandbox`（読み取り専用の対象）に、
  `.agent/agent-project.yaml` の未コミット差分（`regression_cmd`/`intake_cmd` の
  `--repos .agent-project/repos.json` → `--repos repos.json` への変更）が存在する。
  本タスクの変更対象外かつ read-only 制約のため、確認のみで一切手を加えていない。
  t3/t4/t6 が同リポジトリへの書き込みを検討する場合は、この未コミット差分の扱い（誰の変更か・
  コミットすべきか）を先に確認する必要がある。

**未解決事項**
- ライブプローブは本セッション実行時点（この run のホスト環境に `codd-gate 1.0.0` が
  `/Users/nitto/.local/bin/codd-gate` として実在）の結果であり、CI 等 codd-gate 未導入環境での
  挙動は「フォールバック段3（完全未検出）」の実測で代替確認した（この run では強制的に
  `which`/`Path.exists` を差し替えて再現）。
