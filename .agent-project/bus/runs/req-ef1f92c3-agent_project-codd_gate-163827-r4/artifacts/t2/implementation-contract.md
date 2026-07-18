# 後続4系統への実装契約（r4 / t2）

設計の根拠は同ディレクトリの `hook-boundary-design.md`。ここは実装者が守る仕様だけを書く。

---

## 0. 全員が守る不変条件

1. **パッケージ内に `codd_gate` という文字列を書かない。** 識別子・文字列リテラル・docstring・コメントすべて。完了判定はこの grep が空になること。

   ```
   ! git grep -nE 'codd_gate' -- tools/agent-project/agent_project
   ```

   受入 grep（backlog の verify）より厳しい。受入 grep も当然通る。

2. CLI 名 `codd-gate`（ハイフン）は禁止対象ではない。help・docstring・`verify.py:356` の allowlist はそのまま残す。

3. **フックの解決は `_hook_provider` 以外で行わない。** 各所で `importlib` を呼ばない。

4. **フックの失敗で本体を落とさない。** 解決失敗は `None` → 呼び出し側 no-op。プロバイダ呼び出しが例外を投げても、その系統だけ空へ畳む。

5. 変更してよいのは `tools/agent-project` 配下。`tools/codd-gate` と sibling の `codd_gate_*.py` は変更しない（sibling へマーカー属性を足す案は却下済み。§却下理由は設計メモ 3.3）。

---

## 1. 共通シグネチャ

新規フラグメント `agent_project/hooks.py` が提供する。実装は cfg 担当（§2）。

```python
_HOOK_CACHE: "dict[str, object | None]" = {}     # テストが clear() できるよう公開する

HOOK_CAPABILITIES = {                             # 本体が外部へ求める契約の全部
    "wiring.detect":   ("detect_wiring",),
    "wiring.findings": ("doctor_findings",),
}


def _hook_provider(capability: str, cfg: "Config | None" = None) -> "object | None":
    """能力キーからプロバイダ module を解決する。全フックの唯一の入口。例外を投げない。"""
```

- 返すのは module（か module 相当のオブジェクト）か `None`。
- 解決した module のメソッドはここでは呼ばない。呼ぶのは各系統の呼び出し元。
- キャッシュのキーは能力キー。`cfg.hooks` を見るので、cfg が変わるテストは `_HOOK_CACHE.clear()` する。
- `cfg` は省略可。省略時は設定明示を飛ばして sibling スキャンだけ行う。

補助（doctor が使う）:

```python
def _hook_resolution_error(capability: str, cfg: "Config") -> "str | None":
    """明示指定があるのに解決できなかったときだけ理由文字列を返す。既定（未指定）は None。"""
```

---

## 2. configfile 実装者

### やること

1. `CONFIG_DEFAULTS` へ 1 件追加。

   ```python
   "hooks": {},   # 任意フックのプロバイダ指定（能力キー -> module 名）。既定は sibling 自動検出
   ```

2. `Config` に `hooks: dict` を持たせ、`build_config` で `getattr(args, "hooks", None) or {}` を設定する。dict 以外が来たら `{}` へ落とす（例外を投げない。doctor が warn を出す）。

3. **新フラグメント `agent_project/hooks.py` を作り、`__init__.py` の `_FRAGMENTS` へ `_head` の直後・`model` の前に挿入する。** 先頭に `from __future__ import annotations` を置く（フラグメントの規約）。

4. `agent-project.yaml.example` に `hooks:` の例を足す。ここはパッケージ外なので module 名を書いてよい。

   ```yaml
   # hooks:
   #   wiring: codd_gate_wiring     # 未指定なら sibling を自動検出する（通常は書かなくてよい）
   ```

### `_hook_provider` の解決順序（この順のとおりに実装する）

1. `_HOOK_CACHE` に能力キーがあればそれを返す。
2. `cfg.hooks` からフルキー（`"wiring.detect"`）→ 前半キー（`"wiring"`）の順に module 名を引く。非空なら `importlib.import_module(名)` を try で囲んで実行し、必須属性を全部持てば採用。**失敗したら 3 へ落ちず `None` を返す**（明示した意図を黙って別物で置き換えない）。
3. sibling ディレクトリ = `Path(__file__).resolve().parent.parent`。`is_dir()` が偽なら `None`。
4. 直下の `*.py` を **`sorted()` の昇順** で走査。
   - `name.startswith("_")` を除く。
   - `name.isidentifier()` が偽を除く（`agent-project.py` はここで落ちる）。
   - ソーステキストを読み、必須属性すべてについて `re.compile(r"^def %s\s*\(" % re.escape(attr), re.M)` が一致するものだけを候補にする（**無関係な sibling を import しないための前置フィルタ。省略しないこと**）。
   - `sys.path` に sibling が無ければ先頭へ挿入。
   - `importlib.import_module(name)` を try で囲む。例外は捕まえて次の候補へ。
   - `all(hasattr(mod, a) for a in 必須属性)` を満たす最初の 1 件を採用し、採用 module 名を journal へ 1 行残す。
5. 全滅なら `None`。

結果は成否によらず `_HOOK_CACHE` へ入れる（`None` もキャッシュする。毎回スキャンし直さない）。

### 守ること

- `_apply_codd_gate_auto_wiring` 相当の自動配線を復活させない。既存テスト `TestCoddGateNoAutoWiring.test_configfile_has_no_codd_gate_auto_wiring_hook` が `hasattr` で禁止している。`build_config` は `regression_cmd` / `intake_cmd` を補わない。
- ただしそのテストのクラス名・メソッド名に `codd_gate` が入っている。**テストファイルは grep 対象外**（`agent_project` 配下ではない）なので改名は必須でないが、intake+tests 担当が §5 で扱う。

---

## 3. model 実装者

### やること（必須・振る舞い等価の回復）

`_parse_intake_records` に **id と title の型正規化**を戻す。現行は生の dict を素通しするため、非文字列 id で `AttributeError` が出て watch ループが落ちる（設計メモ §8 に実測）。

正規化の仕様は main の `DriftItem.to_spec()` と等価にする。

| キー | 現行 | 直したあと |
|---|---|---|
| `title` | 検証のみ（生の値を spec に残す） | `str(...).strip()` した値を spec に入れる |
| `id` | 素通し（int や空白付きがそのまま） | `str(...).strip()`。結果が空なら **spec からキーごと落とす** |
| その他 | 素通し | 素通し（変えない） |

期待する入出力:

```
[{"title": "drift A", "id": 123}, {"title": " B ", "id": "  x  "}, {"title": "C", "id": ""}]
  -> [{"title": "drift A", "id": "123"}, {"title": "B", "id": "x"}, {"title": "C"}]
  errors: []
```

`errors` の文言と分類（非 object / title 空欠落）は変えない。`run_intake` 側の冪等ロジック（`existing` 集合、`_slug_id` による突合）も変えない。

### やらないこと

- debt に module フックを足さない。debt の差し込み点は `intake_cmd`（プロセス境界）で確定した（設計メモ §3.4）。`_hook_provider` を model から呼ぶ必要はない。
- `_parse_intake_records` の「レコード単位で落とす」方針を変えない。1 件の不備で全体を捨てない。
- `run_intake` の except 節を広げて誤魔化さない。正規化で根本を直す。

---

## 4. doctor 実装者

### やること

1. `_wiring_module` を**削除**する。役割は `_hook_provider` が引き取る。
2. `doctor_wiring_findings` を次の形にする。名前とシグネチャ（`which` / `run` の注入引数）は変えない。呼び出し元 `cmd_doctor` も変えない。

   ```python
   def doctor_wiring_findings(cfg, which=shutil.which, run=subprocess.run) -> "list[dict]":
       detect = _hook_provider("wiring.detect", cfg)
       render = _hook_provider("wiring.findings", cfg)
       if detect is None or render is None:
           return _hook_misconfig_findings(cfg)
       try:
           judgment = detect.detect_wiring(
               regression_cmd=cfg.regression_cmd, intake_cmd=cfg.intake_cmd,
               repos_path=repo_registry_path(cfg), which=which, run=run)
           return render.doctor_findings(judgment)
       except Exception:
           return []          # プロバイダ由来の例外で doctor 全体を落とさない
   ```

3. `_hook_misconfig_findings(cfg)` を足す。**明示指定があるのに解決できなかったときだけ**非空を返す。未指定（既定）で見つからないのは任意機能の不在なので **空リスト・無言**。

   ```python
   {"category": "config", "severity": "warn",
    "title": "指定した配線プロバイダを解決できない",
    "evidence": "hooks.wiring = '<指定値>' が import できない（または detect_wiring / doctor_findings を持たない）",
    "fix": "agent-project.yaml の hooks.wiring を修正するか、行ごと削除して自動検出に戻す"}
   ```

   `hooks` が dict でない場合も warn を 1 件出す（title は「hooks の設定型が不正」）。

4. **docstring から `codd_gate_wiring` を消す。** `doctor.py:288` と `doctor.py:324`。「sibling の配線プロバイダ」「能力で解決する任意フック」のような一般名で書き直す。docstring の判断内容（解決失敗が import 失敗に限らないこと、no-op 縮退の理由）は残す価値があるので、内容は保ったまま名前だけ落とす。

### 守ること

- `judgment` の中身に触らない。`detect_wiring` の返り値は本体にとって不透明。属性を読んだり型を検査したりしない。
- `detect` と `render` の**両方が揃ったときだけ**プロバイダ経路に入る。片方だけで動かすと、属性が片方改名されたときに半端な状態で走る。
- `cmd_doctor` の `deterministic` 合成行（`doctor.py:540`）は変えない。

---

## 5. intake + tests 実装者

t1 が見つけた「改名後の配線経路にテストが 1 件も無い」を、新しい境界に対して埋める。

### 新設 `TestHookResolution`

`setUp` で `km._HOOK_CACHE.clear()` を呼ぶ（キャッシュがテスト間で漏れる）。

| # | ケース | 期待 |
|---|---|---|
| 1 | sibling スキャンが素の環境でプロバイダを引き当てる | `_hook_provider("wiring.detect")` が非 None、`detect_wiring` を持つ |
| 2 | 設定明示が sibling より優先される | `cfg.hooks = {"wiring": "<テスト用 module>"}` で指定した module が返る |
| 3 | 設定明示が解決できないとき自動検出へ落ちない | `hooks.wiring = "no_such_module"` → `None`（sibling があっても拾わない） |
| 4 | 契約不足の module は棄却される | `detect_wiring` を持たない module を明示 → `None` |
| 5 | sibling 不在で `None` | 走査対象を空ディレクトリにして `None` |
| 6 | 前置フィルタが無関係 sibling を import しない | 走査前後の `sys.modules` 差分に契約を満たさない sibling が現れない |
| 7 | 結果がキャッシュされる | 2 回目の呼び出しでスキャンが走らない（走査回数をカウンタで観測） |

### 新設 `TestDoctorWiringFindings`

| # | ケース | patch | 期待 |
|---|---|---|---|
| 1 | プロバイダ有 | `_hook_provider` → fake（`detect_wiring` / `doctor_findings` を持つ `SimpleNamespace`） | fake の返した findings がそのまま返る |
| 2 | プロバイダ無・設定も無 | `_hook_provider` → `None` | **空リスト**（無言縮退） |
| 3 | 明示指定の解決失敗 | `cfg.hooks = {"wiring": "no_such_module"}` | severity=warn の finding が 1 件 |
| 4 | 片方だけ解決 | `lambda cap, cfg=None: fake if cap == "wiring.findings" else None` | 空リスト（半端に走らない） |
| 5 | プロバイダが例外を投げる | fake の `detect_wiring` が `raise RuntimeError` | 空リスト。`cmd_doctor` は落ちない |
| 6 | 注入引数が届く | fake が受けた kwargs を記録 | `which` / `run` / `repos_path` / `regression_cmd` / `intake_cmd` が渡っている |

### 既存 `TestIntake` への追加

| # | ケース | 期待 |
|---|---|---|
| 1 | 非文字列 id | `{"title": "A", "id": 123}` → 例外を投げず、id `"123"` のタスクが 1 件できる |
| 2 | 空白付き id | `{"title": "B", "id": "  x  "}` → id は `"x"` |
| 3 | 空 id | `{"title": "C", "id": ""}` → 自動採番。`AttributeError` を出さない |
| 4 | 非文字列 id の冪等 | 同じ入力を 2 回流して 2 回目は 0 件 |

1 と 4 は設計メモ §8 の回帰ガード。**現行 HEAD ではケース 1 が `AttributeError` で落ちる**ので、model の修正前に書けば赤になるのが正しい。

### 既存テストの扱い

`TestCoddGateNoAutoWiring` はクラス名に `codd_gate` を含むがテストファイルは grep 対象外。**改名は任意**。改名するなら `TestNoAutoWiring` にし、`test_configfile_has_no_codd_gate_auto_wiring_hook` の `hasattr(km, "_apply_codd_gate_auto_wiring")` アサートは**そのまま残す**（禁止する対象の名前なので、この文字列は残ってよい）。

### 実行コマンド

```
cd <repo>/tools/agent-project
PYTHONPATH=. python3 tests/test_agent_project.py TestHookResolution TestDoctorWiringFindings TestIntake
PYTHONPATH=. python3 tests/test_agent_project.py            # 全体
```

全体スイートは `TestDaemonRouting.test_kf_base_passes_flow_config` /
`TestJournalRotation.test_rotation_archives_and_starts_fresh` /
`TestProjectLayer.test_version_inherits_master_charter` の 3 件が main 由来で失敗する（t1 が別 worktree で再現確認済み）。**この 3 件だけが残る状態を合格とし、直そうとしないこと**（スコープ外）。

---

## 6. 完了判定（4系統ぶんを合わせた最終確認）

```
# 1. 厳格 grep（パッケージ内に module 名を残さない）
! git grep -nE 'codd_gate' -- tools/agent-project/agent_project

# 2. 受入 grep（backlog の verify 逐語）
! git grep -n -E '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' \
    -- tools/agent-project/agent_project

# 3. 新旧テスト
PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py \
    TestHookResolution TestDoctorWiringFindings TestIntake TestCoddGateNoAutoWiring

# 4. 受入テスト 3 件
PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py \
    TestIntake.test_run_intake_enqueues_and_dedups_by_id \
    TestLoopEngineering.test_regression_gate_blocks_on_failure \
    TestLoopEngineering.test_regression_gate_passes

# 5. 振る舞い等価（sibling が実在する環境で doctor が所見を出し続ける）
#    ※ 出力の literal は環境依存（codd-gate バイナリの実在と能力検出の実測結果で件数が変わる）。
#      固定値と比較せず、**変更前後の同一環境で同じ出力になること**を確認する。
cat > /tmp/wiring_probe.py <<'EOF'
import json, tempfile, types
from pathlib import Path
import agent_project as km
with tempfile.TemporaryDirectory() as d:
    (Path(d) / "repos.json").write_text(json.dumps({"a": {"url": "git@h:t/a.git"}}), encoding="utf-8")
    ns = types.SimpleNamespace(root=d, config=None); km.resolve_config(ns)
    cfg = km.build_config(ns)
    print(json.dumps(km.doctor_wiring_findings(cfg, which=lambda n, path=None: None),
                     ensure_ascii=False, sort_keys=True))
EOF
git stash && PYTHONPATH=tools/agent-project python3 /tmp/wiring_probe.py > /tmp/before.json
git stash pop && PYTHONPATH=tools/agent-project python3 /tmp/wiring_probe.py > /tmp/after.json
diff /tmp/before.json /tmp/after.json     # 差分なしが合格
```

※ `git stash` は本タスク群では agent-flow が commit を握るため、実装者は代わりに未変更の worktree
（`git_worktree.py provision`）で before を取ること。共有チェックアウトへ書き込まない。

5 は境界が正しく引けたことの直接の証拠になる。本体から module 名が消えても、プロバイダが実在する環境では所見が 1 文字も変わらない。finding の文言に `codd-gate` が出るのはプロバイダ側が持つ文字列なので正しい。
