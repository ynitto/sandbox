# `_first_command_line` 呼び出し元調査 と 回帰観点の入力サンプル

対象: `tools/agent-project/agent_project/verify.py:386` の `_first_command_line`
（呼び出し元・既存テストは `/Users/nitto/Workspace/sandbox`（main ブランチ、コミット `47c65ff7`）を対象に調査した。
このタスクの worktree — `/Users/nitto/Workspace/sandbox-agent-state`（`agent-state` ブランチ）— には
`tools/agent-project` 自体が存在しないため、参照のみで編集はしていない）。

## 前提

- 本タスクは調査・回帰観点の洗い出しのみが範囲であり、コード変更は行っていない。
- 完了条件のシェルコマンド（下記）は **現状のコードで既に exit 0 で成功する**ことを確認した
  （`codd-gate` は `_KNOWN_COMMAND_WORDS` に既に含まれており、`検証コマンド:\n<command>` という
  「ラベル行 → 改行 → コマンド行」の2行形式は既存ロジックで正しく抽出できるため）。
  ```
  PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'
  ```
  → 実行結果: exit code 0（後述「検証内容と結果」）。
- したがって本タスクの成果物は「これから壊さないための回帰入力サンプルと影響範囲の棚卸し」であり、
  抽出規則そのものの変更は他タスク（本 run の他ワーカー）の担当と判断した。

## 1. 呼び出し元（verify 合成経路）

`_first_command_line` の呼び出し元は `synth_verify` 1箇所のみ（`verify.py:423`）。
`synth_verify` はさらに2つの経路から呼ばれる。

```
_first_command_line (verify.py:386)
  └─ 呼び出し元: synth_verify (verify.py:409-434), 呼び出し箇所 verify.py:423
       ├─ ensure_verify (verify.py:437-, 呼び出し箇所 verify.py:469)
       │    └─ 呼び出し元: mr.py:547
       │         `if t.norm_status() in CONSUMABLE and not t.verify and ensure_verify(cfg, t):`
       │         （タスク消化ループ。task.verify が空かつ verify_template も無いとき、
       │           task.extra の accept 自然言語から決定的 verify を合成する経路）
       └─ resolve_charter_acceptance (project.py:69-95, 呼び出し箇所 project.py:86)
            └─ 呼び出し元: project.py:370
                 `resolved, unresolved = resolve_charter_acceptance(cfg, charter, state, kiro_run)`
                 （charter.acceptance の各行のうち、シェルコマンドでない自然言語行を
                   synth_verify で決定的コマンドへ解決する経路。結果は
                   state["acceptance_synth"] に原文キーでキャッシュされ、次回以降は
                   再合成しない）
```

`_first_command_line` はどちらの経路でも「エージェント（kiro-cli）の自由形式応答から
最初に実行可能に見える1行を取り出す」役割であり、ラベル文言（日本語/英語）・コードフェンス・
プロンプト記号などをすべて剥がした後の最終候補が `_looks_like_shell_command`（`sh -n` 構文チェック）
と `_verify_is_degenerate`（恒真式チェック）に渡る。抽出規則を変えると、この2段の後段チェックに
渡る文字列そのものが変わる点に注意（後述「壊れうる既存ケース」）。

## 2. 既存テスト一覧（`tools/agent-project/tests/test_agent_project.py`）

### 2-1. `_first_command_line` / 補助関数への直接テスト（5580〜5737行）

| 行 | テスト名 | 検証内容 |
|---|---|---|
| 5580 | `test_first_command_line_returns_direct_command` | コメント行のみ先行する素のコマンド行を抽出 |
| 5583 | `test_first_command_line_skips_unfenced_prose_before_command` | 日本語の説明文（句点あり）+ コマンド行 → コマンドのみ抽出 |
| 5590 | `test_first_command_line_skips_unpunctuated_english_prose` | 句読点なしの英語散文 + コマンド行 → コマンドのみ抽出 |
| 5594 | `test_first_command_line_accepts_path_and_hyphenated_cli` | `./scripts/...` パス形式・ハイフン付き独自 CLI 名を先頭トークン許容 |
| 5600 | `test_first_command_line_extracts_all_fence_lines_in_order` | `_code_fence_lines` が複数フェンスを出現順に返す |
| 5604 | `test_first_command_line_extracts_from_untagged_sh_and_console_fences` | 言語タグなし/`sh`/`console`（`$ ` プロンプト付き）フェンス |
| 5615 | `test_first_command_line_treats_unclosed_fence_as_running_to_end` | 閉じフェンスが無い場合、末尾まで1ブロック扱い |
| 5620 | `test_first_command_line_returns_command_from_bash_fence_after_prose` | 日本語説明文の後の `bash` フェンス |
| 5627 | `test_first_command_line_ignores_colon_terminated_preamble_before_fence` | **コロン終端の日本語前置き文 + フェンス**（フェンス優先ロジックで前置き行ごと無視） |
| 5639 | `test_first_command_line_skips_blank_and_comment_lines_inside_fence` | フェンス内の空行・インデント付きコメント行をスキップし、後続の別コマンド行は採らない |
| 5653 | `test_first_command_line_skips_language_tag_remnant_inside_fence` | フェンス内先頭の言語タグ残骸行をスキップ |
| 5657 | `test_first_command_line_strips_leading_shell_prompt_symbol` | 行頭 `$ ` の除去 |
| 5663 | `test_first_command_line_returns_none_without_candidate` | コメントのみ → `None` |
| 5666 | `test_first_command_line_returns_none_for_prose_only` | 英語散文のみ（コマンド行なし）→ `None` |
| 5671-5694 | `test_join_continuations_*`（4件） | バックスラッシュ継続行の結合（`_first_command_line` 自体は未使用だが同モジュールの近接ロジック） |
| 5696 | `test_first_command_line_prose_only_never_becomes_synth_verify_command` | 散文のみが2回連続でも `synth_verify` が誤採用しない（retry込み） |
| 5720 | `test_synth_verify_rejects_japanese_prose` | 日本語の拒否文をそのまま `synth_verify` に渡すと空文字 |
| 5726 | `test_synth_verify_rejects_malformed_shell_prose` | 構文的に壊れた英語散文（`sh -n` NG）を拒否 |
| 5732 | `test_looks_like_shell_command` | 全角句読点・未閉じクォートの判定 |

### 2-2. 合成経路（`synth_verify` 経由）を間接的に通す既存テスト

| 行 | テスト名 | 経路 |
|---|---|---|
| 5572 | `test_synth_verify_strips_ansi_from_kiro_output` | ANSI 付き出力 → `synth_verify` |
| 4540 | `test_resolve_acceptance_synthesizes_natural_language` | `resolve_charter_acceptance` → `synth_verify`、結果が `acceptance_synth` にキャッシュされ再合成されないことも検証 |
| 4558 | `test_resolve_acceptance_unresolved_when_synth_fails` | 散文のみの応答 → `unresolved` へ |
| 4569 | `test_natural_language_acceptance_converges` | `cmd_project` 経由で `kiro_run` の応答（`test -f {flag}` という素のコマンド文字列）が抽出されて収束する |
| 4582 | `test_unsynthesizable_acceptance_escalates` | 空応答 → 人へエスカレーション |
| 9435 | `test_synth_verify_and_assess_raise_to_med` | 別観点（優先度）だが `synth_verify` の合成結果を利用 |

**呼び出し元のうち `mr.py:547`（`ensure_verify` の唯一の呼び出し元）を直接エンドツーエンドで
踏むテストは検索した範囲では見当たらなかった**（`ensure_verify` 自体の単体テストは存在するが
`kiro_run` 経由で `_first_command_line` の抽出規則そのものを揺さぶるケースは無い）。
抽出規則を変更する際は `mr.py` 経由（タスク単位の `accept` 合成）の手動確認、または
新規テスト追加を推奨する（範囲外のため本タスクでは追加していない）。

## 3. 抽出規則を変更したときに壊れうる既存ケース

現状の抽出は2段構成:
1. `_code_fence_lines` でフェンスを探し、見つかればフェンス内最初の非空・非コメント行を無条件採用
   （`require_shell_syntax=False`）。
2. フェンスが無ければ、`_has_command_like_leading_token`（先頭トークンが既知コマンド語 /
   `./`,`../`,`/` 始まり / ハイフン付き CLI 名）で行ごとふるいにかけた後、`_looks_like_shell_command`
   （`sh -n` 構文チェック + 全角句読点排除）で最終確定する。

「日本語ラベル付き出力からの抽出」を発展させる変更（例: 同一行のラベル
`検証コマンド: <command>` を分離対応する、コロンで前置き文を機械的に切り落とす、
`_KNOWN_COMMAND_WORDS` を拡張する等）を入れる場合、以下の既存ケースが壊れうる。

- **同一行コロン分離を安易に入れると、コマンド本体に含まれる正当なコロンを誤って
  分割してしまう。** 現状は「1行 = 1候補」判定であり、行内のコロン位置は一切見ていない。
  以下はいずれも現状 `_first_command_line` がそのまま正しく返す素のコマンドだが、
  「先頭のコロンで前置きを切る」実装にすると誤破壊されうる:
  - `git commit -m "note: fix bug"` — クォート内のコロンで誤分割される
  - `grep -n "time: 12:30" file.txt` — 同上、かつコロンが2箇所
  - `docker run alpine:3.18 echo hi` — イメージタグのコロン
  - 全角コロン「：」も同種のリスク（日本語ラベルは全角コロンが自然なため対応漏れも起きやすい）
- **`test_first_command_line_ignores_colon_terminated_preamble_before_fence`（5627行）は
  「コロン終端の前置き文 + フェンス」を "フェンス優先" で処理しており、コロン分離ロジックとは
  無関係に成立している。** 新しいコロン処理をフェンス判定より先に走らせる実装にすると、
  この既存テストの前提（フェンスが見つかれば前置き行は無条件無視）を壊しうる。
- **`_has_command_like_leading_token` は「行全体の最初のトークン」で判定しており、
  ラベル + コマンドが同一行にある場合は行全体が候補から落ちる**（後述サンプル
  `ja_label_colon_same_line_as_command` は現状 `None`）。この既知の未対応ケースを
  救おうとして正規表現等で「最初のコロン以降を候補にする」ような一般化をすると、
  上記のクォート内コロン・タグ内コロンを含む既存の "そのまま通る" ケースを巻き込みやすい。
- **`_KNOWN_COMMAND_WORDS` 拡張は無関係な既存テストへの副作用は小さいが**、
  `test_first_command_line_returns_none_for_prose_only`（5666行）・
  `test_first_command_line_returns_none_without_candidate`（5663行）は
  「候補ゼロ → `None`」を前提にしているため、フォールバック規則を緩めすぎる
  （例: 未知語でも英字で始まれば候補にする等）と、これらが偽陽性で `None` 以外を
  返すようになり壊れる。
- **`_verify_is_degenerate` / `_looks_like_shell_command` は最終候補にのみ適用される**
  ため、抽出規則側の変更で候補文字列の前後に余分な空白・引用符の片割れが残ると、
  `sh -n` チェックで構文エラーになり `test_synth_verify_rejects_malformed_shell_prose`
  相当の「散文として拒否される」経路に誤って落ちる（＝正しいコマンドなのに `None`/`""` になる）
  リグレッションを生みやすい。

## 4. 回帰観点の入力サンプル（現状の実測結果つき）

すべて `PYTHONPATH=tools/agent-project` で `agent_project._first_command_line(<input>)` を
実行して得た**現状の実際の戻り値**を併記する。抽出規則を変更した後、同じ入力を流して
この表と差分が出ないか（意図した箇所以外は不変か）を回帰確認に使う想定。

### 4-1. 素のコマンド行のみ（ラベルなし・フェンスなし）

```python
# 入力 → 現状の戻り値
'codd-gate verify --base "$KIRO_BASE_REV"'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'

'codd-gate verify --base "$KIRO_BASE_REV"\n'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'

'git commit -m "note: fix bug"'
# -> 'git commit -m "note: fix bug"'   ※コマンド内コロンが誤分割されないことの回帰サンプル

'grep -n "time: 12:30" file.txt'
# -> 'grep -n "time: 12:30" file.txt'  ※同上（コロン2箇所）

'docker run alpine:3.18 echo hi'
# -> 'docker run alpine:3.18 echo hi'  ※イメージタグのコロンが誤分割されないことの回帰サンプル
```

### 4-2. コードフェンス（```）で囲まれた出力

```python
'```\ncodd-gate verify --base "$KIRO_BASE_REV"\n```'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'

'```sh\ncodd-gate verify --base "$KIRO_BASE_REV"\n```'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'

'検証コマンド:\n```bash\ncodd-gate verify --base "$KIRO_BASE_REV"\n```'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'   ※ラベル行 + フェンスの併存

'Verify command:\n```bash\ncodd-gate verify --base "$KIRO_BASE_REV"\n```'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'   ※英語ラベル行 + フェンスの併存

'以下のコマンドで検証できます:\n```bash\npython3 -m pytest tools/agent-project/tests -q -k first_command_line\n```'
# -> 'python3 -m pytest tools/agent-project/tests -q -k first_command_line'  ※既存テスト5627行と同一入力
```

### 4-3. 先頭に説明文がある出力（フェンスなし）

```python
'検証コマンドは次のとおりです。\ncodd-gate verify --base "$KIRO_BASE_REV"'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'

'実行時刻は12:30を想定しています\npytest -q'
# -> 'pytest -q'   ※説明文中の（ラベルではない）コロンに惑わされないことの回帰サンプル
```

### 4-4. 英語ラベル（`Verify command:` など）

```python
'Verify command:\ncodd-gate verify --base "$KIRO_BASE_REV"'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'

'Here is the verify command\ncodd-gate verify --base "$KIRO_BASE_REV"'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'

'Verify command: pytest -q'
# -> None   ※【現状の既知ギャップ】ラベルとコマンドが同一行だと未対応（下記4-6参照）
```

### 4-5. 空行・インデント付き行

```python
'\n\n\ncodd-gate verify --base "$KIRO_BASE_REV"'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'

'検証コマンド:\n\ncodd-gate verify --base "$KIRO_BASE_REV"'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'   ※ラベル行の後に空行を挟むケース

'検証コマンド:\n    codd-gate verify --base "$KIRO_BASE_REV"'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'   ※インデント付き（フェンス外・strip()で吸収）

'```bash\n\n    codd-gate verify --base "$KIRO_BASE_REV"\n```'
# -> 'codd-gate verify --base "$KIRO_BASE_REV"'   ※フェンス内の空行 + インデント
```

### 4-6. 現状の既知ギャップ（今回のタスクの完了条件には含まれないが、規則変更の対象候補として記録）

```python
'検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"'
# -> None   ※ラベルとコマンドが半角コロン+スペースで同一行

'検証コマンド：codd-gate verify --base "$KIRO_BASE_REV"'
# -> None   ※全角コロンで同一行

'Verify command: pytest -q'
# -> None   ※英語ラベルが同一行
```
今回の完了条件（ラベル行とコマンド行が別行の2行形式）は現状のコードで満たされているため
本タスクでは未対応のまま報告するに留める。同一行対応を今後入れる場合は、上記
「3. 壊れうる既存ケース」のクォート内コロン・イメージタグコロンのサンプルを回帰に必ず含めること。

## 検証内容と結果

完了条件のシェルコマンドを実際に実行し、exit code 0 を確認した。

```
$ cd /Users/nitto/Workspace/sandbox && PYTHONPATH=tools/agent-project python3 -c '...(完了条件のコマンド)...'
$ echo $?
0
```

また、上記 4-1〜4-6 の全サンプルを実際に `_first_command_line` へ通し、本ドキュメントに記載した
戻り値と一致することを確認済み（実行環境: `/Users/nitto/Workspace/sandbox` 上の Python 3、
`tools/agent-project` を `PYTHONPATH` に追加）。

## 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 本タスクは「調査 + 回帰入力サンプルの用意」のみが範囲であり、抽出規則の実装変更は
  行っていない（完了条件は現状のコードで既に満たされているため、コード変更なしで完了と判断した）。
- **未解決事項**: ラベルとコマンドが同一行にある形式（`検証コマンド: <command>` /
  `Verify command: <command>`）は現状 `None` を返す。今回の完了条件には含まれないため
  修正はしていないが、もし今後の要求に含まれるなら「3. 壊れうる既存ケース」のクォート内コロン
  サンプルを回帰テストとして先に固定してから着手すべきと考える。
- **範囲外で見つけた問題**: `_KNOWN_COMMAND_WORDS`（verify.py:303-308）に `curl` が含まれていない
  ため、`curl -H "Content-Type: application/json" http://example.test` のような素のコマンドは
  フェンスなしだと `None` になる（本タスクの範囲外のため未修正。要望があれば別タスク化を推奨）。
