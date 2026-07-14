# synth 統合報告: agent-project × codd-gate 連携

対象リポジトリ: `/Users/nitto/Workspace/sandbox`（branch `main`、未コミット差分あり）

## (a) 成果

agent-project から codd-gate（ドキュメント・コード・テスト整合の決定的ゲート）を呼び出す配線が、
**検証コマンド経路に限り**有効化されている。

| 要素 | 状態 |
|---|---|
| `.agent/agent-project.yaml` の `regression_cmd`/`intake_cmd` | codd-gate 起動文字列で設定済み（未コミット差分） |
| `tools/agent-project/codd_gate_{detect,status,routing,base,debt}.py` | 実装済み（前ラウンド r0 由来、既にコミット済みファイル） |
| `tools/agent-project/tests/test_codd_gate_{detect,routing}.py` への追加テスト | 3メソッド追加（後述、未コミット差分） |
| `docs/designs/codd-gate-design.md` への反映 | **未反映**（addendum 案のみ artifacts に存在、本体は無変更） |
| `tools/agent-project/README.md` への注意書き追記 | **未反映**（addendum 案のみ、本体は無変更） |

## (b) 検証内容と結果（証跡）

依存タスク（loop／docs）の報告を鵜呑みにせず、完了条件コマンド4本を対象リポジトリで自分で再実行した。

```
$ cd /Users/nitto/Workspace/sandbox
$ ( grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml && \
    grep -E '^[[:space:]]*intake_cmd:.*codd-gate tasks' .agent/agent-project.yaml && \
    PYTHONPATH=tools/agent-project python3 -c 'from codd_gate_status import detect_status; s=detect_status(); assert s.usable and s.command("verify","--base","HEAD")' && \
    python3 -m pytest tools/agent-project/tests/test_codd_gate_detect.py tools/agent-project/tests/test_codd_gate_routing.py -q ); \
  echo "FINAL_RC=$?"

regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'
................................                                         [100%]
32 passed in 0.04s
FINAL_RC=0
```

4本すべて exit 0、連結チェーンも `FINAL_RC=0`。loop タスクの報告（同一チェーンで `FINAL_RC=0`）と一致した。

**追加テストの実体確認**（`git diff` で直接確認。「2本」という当初の想定より実際は多い）:
- `test_codd_gate_detect.py`: `test_command_builds_verify_base_head_argv`（`command("verify","--base","HEAD")` の argv 組み立てを完了条件と同じ形で検証）、`test_empty_path_env_with_real_which_degrades_to_noop`（PATH 空・同梱パス無しの実環境相当で no-op 縮退することを検証）の2メソッド追加。
- `test_codd_gate_routing.py`: `TestAgentProjectYamlWiring` クラスを新設し `test_regression_cmd_and_intake_cmd_route_to_codd_gate` を1メソッド追加（agent_project パッケージの config loader を経由せず、素の YAML 読み書きで `regression_cmd`/`intake_cmd` の実引数を検証。理由はテストdocstringに明記: `agent_project` の import が cwd 上の設定探索・watch/state-git 等の副作用を伴い、過去に実リポジトリへの誤コミットを引き起こしたため）。

合計3メソッド・2ファイルへの追加であり、依頼文言の「2本のテスト」とは数が食い違う。実体を優先し、ここでは正確な数を報告する。

## (c) 未インストール環境でのフォールバック挙動

`codd_gate_status.py`/`codd_gate_detect.py` が実装する検出・no-op 縮退の設計は健全（単体テスト29件超で担保）。ただし**今日実際にライブな結線はこの縮退ロジックを経由していない**。

- **検出モジュール側（単体では完成）**: `resolve_codd_gate` が explicit → PATH → 同梱パス（`tools/codd-gate/codd-gate.py`）の順で起動経路を探し、いずれも無ければ `None`。`CoddGateStatus.usable=False` のとき `command()` は例外を投げず `None` を返す——呼び出し側は `if status.command(...):` の1行で「使えない環境では何もしない」を実現できる。
- **実際の静的結線側（今日ライブ）**: `.agent/agent-project.yaml` の `regression_cmd`/`intake_cmd` は codd-gate 起動文字列を直書きした汎用フックであり、上記の検出・縮退ロジックを**経由しない**。`grep -rn "codd_gate" tools/agent-project/agent-project.py tools/agent-project/agent_project/*.py` はヒットなし（テスト以外）——ランタイムからは未参照であることを実測で確認済み。
- **結論**: codd-gate が未インストール／非互換の環境にこの `.agent/agent-project.yaml` をそのまま持ち込むと、**no-op へ静かに縮退するのではなく、regression_cmd のシェル実行自体が非0で失敗し、当該タスクは規約どおり人（`review: human` 相当）へ回る**。intake 側は codd-gate 不在時に stdout が非JSONとなり無視される（致命的失敗にはならないが、負債ラチェットは機能しない）。
- 検出/縮退レイヤをランタイムへ実際に結線する作業（設計上 b3・c1・e1 と呼ばれる箇所）は**別タスクの担当として残っている**。今回のスコープはこの結線ではなく、既存の静的結線と検出モジュールの整合確認・テスト追加・ドキュメント差分の洗い出しである。

## (d) charter（v1）目標への充足状況

`charters/v1.md` の記載を根拠に評価する。

| 目標／受入基準 | 状態 | 根拠 |
|---|---|---|
| goal: codd-gateと連携できること | **満たす（検証コマンド経路のみ）** | `.agent/agent-project.yaml` の `regression_cmd`/`intake_cmd` が codd-gate を実際に起動する文字列で設定済み。上記(b)で exit 0 を実測。ただし(c)の通り no-op 縮退は未結線 |
| accept: 検証コマンドに codd-gate が組み込める | **満たす** | 完了条件コマンド4本すべて exit 0（上記ログ） |
| goal: 設計書を整理して人間にとって読みやすくすること | **未達** | `docs/designs/codd-gate-design.md` に §4.1 相当の追記は**存在しない**（`grep -n "^### 4\.1"` はヒットなし、`git status` も無変更）。docs タスクが作成したのは artifacts 内の addendum**案**（`codd-gate-design-detection-addendum.md`）のみで、本体ファイルへの反映は行われていない |
| accept: 設計書と実装に乖離がない | **未達** | 5本の検出/縮退モジュール（`codd_gate_{detect,status,routing,base,debt}.py`）と追加テストが実装・単体テスト済みである一方、設計書はこれらの存在にも、静的結線が縮退ロジックを経由しない実態にも一切触れていない。実装と設計書の乖離が現に存在する状態 |

## 依存タスク成果の突き合わせで見つけた矛盾・欠落

1. **依頼文言との齟齬（欠落）**: 本タスクの依頼文は「設計書への反映」を既に完了した成果の一つとして前提しているが、実態は addendum 案の起草に留まり、`docs/designs/codd-gate-design.md` 本体は無変更。charter の受入基準「設計書と実装に乖離がない」に照らすと、この乖離こそが未解消のまま残っている。**次アクション**: `codd-gate-design-detection-addendum.md`（および任意で `agent-project-readme-caveat-addendum.md`）を実際に該当ファイルへ適用するタスクが必要。
2. **テスト本数の齟齬**: 依頼文言「追加した2本のテスト」に対し、実体は2ファイルにまたがる3メソッド。数の食い違いを実測で訂正した（上記(b)）。
3. **loop と docs の報告に矛盾なし**: 両者とも「静的結線はライブだが検出/縮退レイヤはランタイム未結線」という同一の実装像に独立に到達しており、事実関係の重複はあるが矛盾はない。docs 報告はさらに t2 の独立確認（フォールバックは「PATH→リポジトリ内スクリプト→スキル配置ディレクトリ」の3段ではなく実装上は「explicit→PATH→同梱パス」である）と整合させており、この訂正版の記述を本報告でも採用した。
4. **範囲外の問題**: 新規には見つからず。上記1・2は本タスク（synth）の統合作業で顕在化した整理事項であり、範囲外の欠陥ではない。
