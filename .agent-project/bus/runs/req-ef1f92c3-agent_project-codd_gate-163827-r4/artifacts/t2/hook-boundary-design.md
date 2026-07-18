# 汎用フック境界の設計メモ（r4 / t2）

**切り口: 決定軸を「sibling 検出 vs 設定明示」から「外部名の所有権は誰か」へ置き換える。名前を設定へ譲り、既定は名前ではなく能力（属性の集合）で引き当てる。**

対象ツリー: `/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-flow-ws-81332-8rja6_t1/sandbox`（HEAD `d5e03f4`）

---

## 1. 結論

二択のどちらにも寄せない。**設定明示を第一・sibling 能力スキャンを第二とする単一解決器**へ一本化し、パッケージ内から `codd_gate` の文字列を 0 にする。

二択のままではどちらを選んでも要件のどちらかが落ちる。

- sibling 検出方式（現行 HEAD）は連携を保つが、`doctor.py:302` の `provider = "codd_gate_wiring"` が残る。タスクの条件は「codd_gate 名を一切残さない」なので不合格。
- 設定明示方式は名前を追い出せるが、既存インストールは `hooks:` を書いていない。既定値を持たせないと配線所見が黙って消える。非破壊の条件に反する。

分けるべき問いは二つあった。**「名前を誰が持つか」（設定）と「既定でどう見つけるか」（能力スキャン）** で、これを分けると両方立つ。

### 決定した3系統

| 系統 | 境界の種類 | フック名 | 解決経路 |
|---|---|---|---|
| 配線 | Python module | 能力キー `wiring.detect` / 必須属性 `detect_wiring` | 設定 → sibling 能力スキャン |
| findings 収集 | Python module | 能力キー `wiring.findings` / 必須属性 `doctor_findings` | 同上 |
| debt | **プロセス境界** | 設定キー `intake_cmd` | 設定のみ。module 解決を持たない |

debt だけ種類が違う。理由は §3.4。

---

## 2. 決定の根拠

判定基準はタスク指定の二つ。「振る舞い等価」「codd_gate 連携の非破壊」。

| 案 | codd_gate 名 0 | 振る舞い等価 | 連携の非破壊 | 判定 |
|---|---|---|---|---|
| A. sibling 検出のみ（現行 HEAD） | ✗ 3 行残る | ✓ | ✓ | 不合格 |
| B. 設定明示のみ | ✓ | ✗ 未設定環境で所見が消える | ✗ | 不合格 |
| C. **設定 → 能力スキャン**（採用） | ✓ | ✓ 実測で findings 一致 | ✓ | **採用** |

C の等価性は実測で確認した。sibling を能力スキャンで引き当て、そのプロバイダで組んだ findings が現行 `doctor_wiring_findings` の出力と一致する（§6）。

### 名前の所有権を移すと何が変わるか

現行は本体が `"codd_gate_wiring"` という**外部の版管理下にある名前**を握っている。プロバイダ側が module 名を変えれば本体が壊れる。名前を設定へ譲れば、本体が知るのは「`detect_wiring` を持つ何か」だけになる。これが「本体は無改造・差し込み点のみ」をコードで真にするということで、grep 条件の充足はその副作用にすぎない。

---

## 3. 却下した案とその理由

### 3.1 現状維持（文字列リテラルは grep に掛からないので残す）

t1 が採用した読み。受入 grep は確かに通る。却下する理由は、t2 の完了条件が grep ではなく「codd_gate 名を一切残さない」だから。厳しい方の grep を引くと現行は 3 行ヒットする。

```
$ git grep -nE 'codd_gate' -- tools/agent-project/agent_project
doctor.py:288   """任意の sibling 配線プロバイダ（…『codd_gate_wiring』module。
doctor.py:302       provider = "codd_gate_wiring"
doctor.py:324   …プロバイダ（`codd_gate_wiring`）が使えない環境では空リストへ…
```

t1 は「ここを消すと差し込み先の指定手段が失われる」と書いたが、それは名前を消す場合の話で、名前を設定へ移す選択肢が検討されていない。指定手段は `hooks:` として残る。

### 3.2 環境変数によるプロバイダ指定（`AGENT_PROJECT_WIRING_PROVIDER`）

名前は追い出せる。却下理由は、設定が config ファイルと env の二箇所に散り、doctor が「なぜ解決に失敗したか」を説明できなくなること。agent-project は設定の単一アンカーを root 直下の yaml に置く設計（`configfile.py:201` の `resolve_config`）で、そこに env を足すのは既存の設定モデルを壊す。

### 3.3 sibling 側にマーカー属性を追加して宣言させる（`AGENT_PROJECT_HOOKS = {...}`）

発見が明示的になり、能力スキャンより速い。却下理由は、`codd_gate_wiring.py` の編集が必要になること。プロバイダは本体の外にあるという前提が崩れ、「本体は差し込み点のみ」と言いながら差し込む側に本体都合の記述を強制することになる。しかも既存の `required = ("detect_wiring", "doctor_findings")` が事実上のマーカーとして既に機能しており、追加する必要がない。

### 3.4 debt にも module フックを再導入する

3系統を対称に揃えられる。却下理由は二つ。

一つ目。debt の差し込み点はすでに `intake_cmd`（プロセス境界）にあり、そこを通る以上、本体が受けるのは JSON テキストだけになる。Python module を足しても、`intake_cmd` で指定した外部ツールと二重の差し込み点ができるだけで、片方は必ず遊ぶ。

二つ目。r0 が `_codd_gate_debt_module` を内蔵パーサ `_parse_intake_records` へ置き換えたのは正しい方向だった。プロバイダ欠落時のフォールバックが「緩いパース」だった main の二経路構成が一本化され、挙動が環境に依存しなくなった。ここに module 解決を戻すのは後退になる。

ただし置き換えは等価でなかった。§5 に実測した非等価と、その埋め方を書く。

---

## 4. インターフェース定義

### 4.1 単一解決器

パッケージ内に新しい断片 `agent_project/hooks.py` を置き、`_FRAGMENTS` の `_head` の直後（`model` より前）へ挿入する。フラグメントは共有名前空間へ exec されるので、`doctor` からも `model` からも `_hook_provider(...)` として素で呼べる。

```python
def _hook_provider(capability: str) -> "object | None":
    """能力キーから任意フックのプロバイダ module を解決する。全フックの唯一の入口。

    例外を投げない。解決できなければ None を返し、呼び出し側は no-op へ縮退する。
    プロセス内でキャッシュする（能力キー毎に1回だけ解決する）。
    """
```

副作用はキャッシュ書き込みと `sys.path` への sibling ディレクトリ追加のみ。返り値の module に対する呼び出しは行わない（呼ぶのは各系統の呼び出し元）。

### 4.2 能力表

能力キーと必須属性の対応は `hooks.py` 内の 1 つの定数に集約する。ここが「本体が外部へ求める契約の全部」になる。

| 能力キー | 必須属性 | 呼び出し元 | 呼び出し時のシグネチャ |
|---|---|---|---|
| `wiring.detect` | `detect_wiring` | `doctor_wiring_findings` | `detect_wiring(regression_cmd=, intake_cmd=, repos_path=, which=, run=) -> judgment` |
| `wiring.findings` | `doctor_findings` | `doctor_wiring_findings` | `doctor_findings(judgment) -> list[dict]` |

`judgment` は本体にとって**不透明**。中身の型・属性へ触れない（現行 `doctor.py:333` も触っていない。この不可視性を契約として明文化する）。

配線と findings を別の能力キーにするのは、片方だけ持つプロバイダを許すためではなく、**解決失敗の粒度を系統ごとに独立させる**ため。実運用では同じ module が両方を満たすが、`detect_wiring` だけ改名されたときに「配線系統だけ no-op、findings 系統は生存」と誤動作するのを防ぐため、`doctor_wiring_findings` は両方の解決が揃ったときだけ動く（§4.4）。

### 4.3 設定キー

`CONFIG_DEFAULTS` へ 1 件足す。

```python
"hooks": {},   # 任意フックのプロバイダ指定（能力キー -> module 名）。既定は sibling 自動検出
```

yaml 側の書き方（`agent-project.yaml.example` に例示。ここはパッケージ外なので名前を書いてよい）:

```yaml
hooks:
  wiring: codd_gate_wiring        # 配線判定と findings 収集の両方を担う
```

キーは能力キーの**前半（ドットの前）** で受ける。`wiring` 一語で `wiring.detect` と `wiring.findings` の両方に効く。`wiring.detect` のようなフルキーも受け、フルキーが優先。運用の最短形を既定にしつつ、片方だけ差し替える余地を残す。

型が dict でない値（誤記）は空 dict として扱い、doctor で warn を出す。

### 4.4 doctor 側の呼び出し形

```python
def doctor_wiring_findings(cfg, which=shutil.which, run=subprocess.run) -> "list[dict]":
    detect = _hook_provider("wiring.detect")
    render = _hook_provider("wiring.findings")
    if detect is None or render is None:
        return _hook_misconfig_findings(cfg)      # 明示指定の失敗のときだけ非空
    judgment = detect.detect_wiring(
        regression_cmd=cfg.regression_cmd, intake_cmd=cfg.intake_cmd,
        repos_path=repo_registry_path(cfg), which=which, run=run)
    return render.doctor_findings(judgment)
```

---

## 5. 解決順序

`_hook_provider(capability)` は次の順で試し、最初に成立した 1 件を返す。

1. **キャッシュ**。能力キー毎にプロセス内で 1 回だけ解決する。
2. **設定明示**。`cfg.hooks` のフルキー → 前半キーの順に引き、値が非空なら `importlib.import_module(値)`。成功して必須属性を全部持てば採用。**失敗しても 3 へ落ちない**（明示した意図を黙って別の何かで置き換えない）。`None` を返し、doctor が warn を出す材料を残す。
3. **sibling 能力スキャン**。`Path(__file__).resolve().parent.parent`（`__init__.py` の exec 合成により常に `tools/agent-project/`）直下の `*.py` を**ファイル名昇順**で走査。
   1. `_` 始まり、および `str.isidentifier()` が偽の名前を除く（`agent-project.py` はここで落ちる）。
   2. **ソーステキストの前置フィルタ**: 必須属性すべてについて `^def <属性>\s*\(` が本文に含まれるファイルだけを候補にする。無関係な sibling を import しない。
   3. 候補を `importlib.import_module`。例外は捕捉して次の候補へ。
   4. `all(hasattr(mod, a) for a in 必須属性)` を満たす最初の 1 件を採用。
4. 全滅なら `None`。

`sys.path` への sibling 追加は 3-3 の直前に行う（今の `doctor._wiring_module` と同じ）。

### 前置フィルタを入れる理由

能力スキャンの素朴な実装は「全 sibling を import して属性を見る」になるが、これは import 副作用を無差別に起こす。ソーステキストで先に絞れば、実際に import されるのは契約を満たす見込みがあるものだけになる。実測では `codd_gate_wiring` のみが直接 import され、同時に sys.modules へ現れた `codd_gate_base` / `codd_gate_detect` / `codd_gate_routing` / `codd_gate_status` はプロバイダ自身の依存だった。`codd_gate_debt` と `codd_gate_regression` は import されない。

### 現行との差（意図した縮小）

現行 `importlib.import_module("codd_gate_wiring")` は sys.path 全体を探すので、sibling でない場所（site-packages 等）に置かれたプロバイダも拾えた。能力スキャンは sibling ディレクトリしか見ない。名前を知らない以上、sys.path 全体を能力で舐めることはできない（コストと副作用が非現実的）。

この縮小の逃げ道が §4.3 の `hooks:` 設定で、そこに module 名を書けば経路 2 で sys.path 全体から解決される。sibling 配置の標準インストールでは差が出ない。

---

## 6. フォールバック表

| # | 状況 | `_hook_provider` | 本体の挙動 | doctor の記録 |
|---|---|---|---|---|
| 1 | 設定明示あり・import 成功・契約充足 | module | 通常 | なし |
| 2 | 設定明示あり・import 失敗 | `None` | no-op（空リスト） | **warn**「指定した配線プロバイダを解決できない」 |
| 3 | 設定明示あり・契約不足（属性欠落） | `None` | no-op | **warn**（fix に必須属性名を出す） |
| 4 | 設定 `hooks` が dict でない | `None` | no-op | **warn**「hooks の型が不正」 |
| 5 | 設定なし・スキャン命中 1 件 | module | 通常 | なし |
| 6 | 設定なし・スキャン命中複数 | 昇順で先頭 | 通常 | なし（採用 module 名を journal へ 1 行） |
| 7 | 設定なし・命中 0 件 | `None` | no-op | **なし**（任意機能の不在は正常） |
| 8 | 候補の import が例外を投げる | 次候補へ。全滅で `None` | no-op | なし |
| 9 | sibling ディレクトリ自体が無い（zipapp 等） | `None` | no-op | なし |
| 10 | 同名の無関係 module が解決される | 契約チェックで棄却 → `None` | no-op | なし |

**2〜4 は warn、7 は無言**。ここが設計の要点で、「明示した意図が果たされなかった」と「任意機能を使っていない」を区別する。現行はどちらも一律に無言で、設定ミスが観測できない。

no-op 縮退の理由は現行 docstring の判断をそのまま引き継ぐ。配線所見が出ないのは任意機能の欠落だが、doctor が落ちるのは診断コマンドとして致命的で、失う情報が桁違いに多い。

---

## 7. テストからの注入点

**`_hook_provider` を patch すれば全系統が差し替わる。** これが唯一の注入点になる。

| 差し替えたいもの | patch 対象 | 備考 |
|---|---|---|
| 配線プロバイダ全体 | `mock.patch.object(km, "_hook_provider", lambda cap: fake)` | fake は `detect_wiring` / `doctor_findings` を持つ `SimpleNamespace` で足りる |
| プロバイダ不在 | `mock.patch.object(km, "_hook_provider", lambda cap: None)` | no-op 縮退の確認 |
| 系統ごとの片欠け | `lambda cap: fake if cap == "wiring.findings" else None` | §4.4 の「両方揃ったときだけ動く」の確認 |
| 解決順序そのもの | キャッシュを消して `cfg.hooks` を差し替え | `km._HOOK_CACHE.clear()` を setUp で呼ぶ |
| 実測 I/O | `doctor_wiring_findings(cfg, which=…, run=…)` | 既存の注入引数。変えない |
| intake のパース | `km._parse_intake_records(text)` を直接呼ぶ | 純関数。注入不要 |
| intake の全経路 | `intake_cmd="cat <fixture>.json"` | 既存 `TestIntake` と同形式 |

キャッシュはテスト間で漏れる。`_HOOK_CACHE` はモジュール辞書として公開し、`setUp` で `clear()` できるようにする（隠すと patch できない）。

---

## 8. 実測で判明した非等価 — intake の id 正規化が落ちている

r0 の `_codd_gate_debt_module` → `_parse_intake_records` 置き換えで、**id の型正規化が抜けた**。main の `DriftItem` は `str(raw_id).strip()` を通していたが、HEAD の `_parse_intake_records` は生の dict をそのまま spec として返す。

その結果、intake_cmd が `{"title": "...", "id": 123}` のような非文字列 id を吐くと、`_gen_task_id` → `_slug_id` が int に対して `.strip()` を呼び `AttributeError` になる。`run_intake` の except は `ValueError` だけを捕まえるので素通りし、呼び出し元（`loop.py:610` の idle ポーリング、`mr.py:558` の `_run_setup`）はどちらも無防備。**watch ループが落ちる。**

実測（リポジトリは変更していない。プロセス内で再現しただけ）:

```
入力: [{"title": "drift A", "id": 123}, {"title": "drift B", "id": "  spaced  "}]
main 相当（codd_gate_debt.parse_debt_output）
  -> [{'title': 'drift A', 'id': '123'}, {'title': 'drift B', 'id': 'spaced'}]  errors: []
HEAD（run_intake）
  -> AttributeError: 'int' object has no attribute 'strip'   ← ループを殺す
```

`run_intake` の docstring が掲げる「有限・無害。例外は journal に残して無視（ループは殺さない）」に対する違反でもある。

**埋め方**: `_parse_intake_records` に main と同じ正規化を戻す。title は `str(...).strip()`（すでに検証だけはしている）、id は `str(...).strip()` して falsy なら spec から落とす。他のキーは触らない。プロバイダは要らない。具体は実装契約 §3 に書いた。

---

## 9. 検証内容と結果

コード変更は行っていない（本タスクは境界の確定であり、実装は後続4系統の担当）。設計の妥当性はプロトタイプをプロセス内で走らせて確かめた。

| 検証 | 方法 | 結果 |
|---|---|---|
| 能力スキャンがプロバイダを引き当てる | 前置フィルタ + import + 属性チェックの試作を実行 | `wiring.detect` / `wiring.findings` とも `codd_gate_wiring` に解決 |
| 振る舞い等価（結線済み相当） | 試作で組んだ findings と現行 `doctor_wiring_findings(cfg)` を比較 | **一致**（ともに 0 件） |
| 振る舞い等価（未結線・repos.json あり） | `which=lambda …: None` を注入して同上 | **一致**（その実行では 2 件。title まで同一） |
| import 副作用の限定 | スキャン前後の `sys.modules` 差分 | 直接 import は `codd_gate_wiring` のみ。`codd_gate_debt` / `codd_gate_regression` は未 import |
| sibling 不在時の縮退 | 空ディレクトリを走査対象に指定 | `None`（→ no-op） |
| §8 の非等価 | 一時 root と fixture で `run_intake` を実行 | `AttributeError` を再現。main 経路は正常に正規化 |
| 厳格 grep の現状 | `git grep -nE 'codd_gate' -- tools/agent-project/agent_project` | 3 行ヒット（`doctor.py:288, 302, 324`）。この 3 行が本設計の除去対象 |

等価性の確認は同一プロセス内で現行と試作の両方を走らせて比較した。`detect_wiring` は codd-gate バイナリの実在と能力を実行時に実測するので、findings の**件数は環境で変わる**（別プロセス・別マシンで取った値と突き合わせても意味がない）。実装者向けの手順は実装契約 §6-5 に、固定値比較ではなく変更前後の diff として書いた。

未実行: 全体テストスイート（コード未変更のため実行意味がない。t1 が既存 3 failures を main 由来と切り分け済み）。

---

## 10. 採用した前提

1. **「codd_gate 名を一切残さない」は受入 grep より厳しい条件として読む。** 本文の指示を字義どおり取り、`doctor.py:302` の文字列リテラルも除去対象に含めた。t1 は逆の判断（維持）を採ったが、t1 の根拠は「消すと指定手段が失われる」であり、名前を設定へ移す第三の道が検討されていない。指定手段は `hooks:` として保存される。
2. **禁止対象は module 名 `codd_gate*` に限る。** CLI 名 `codd-gate`（ハイフン）の例示は help 文字列・docstring・`verify.py:356` の allowlist に残る。これらは module 名ではなく、消すと利用者が結線方法を知る手段が減る。文章の推敲はスコープ外でもある。
3. **`judgment` は本体にとって不透明。** 現行実装が中身へ触っていないので、その暗黙の前提を契約として固定した。
4. **HEAD が r0 適用済みである点をそのまま起点とした。** main への差し戻しは行わない。
5. **本タスクではコードを書かない。** 「確定する」を境界の定義と実装契約の作成と読み、実装は後続4系統に委ねた。

---

## 11. 未解決事項・範囲外で見つけた問題

- **[要対応・実装契約に反映済み]** §8 の id 正規化欠落。振る舞い等価の条件に直接抵触するので、model 実装者の必須項目に入れた。
- **[要対応・実装契約に反映済み]** t1 §5 が指摘した `doctor_wiring_findings` / `_wiring_module` のテスト 0 件。本設計で `_hook_provider` へ統合されるため、テストは新しい注入点に対して書く。intake+tests 実装者の担当。
- **[範囲外]** 全体スイートの既存 3 failures（`TestDaemonRouting` / `TestJournalRotation` / `TestProjectLayer`）は t1 が main 由来と実測済み。本設計と無関係。
- **[範囲外]** `hooks:` の CLI フラグ（`--hook wiring=…`）は今回定義しない。yaml だけで運用でき、CLI 面を増やすと `_add_common` の引数表が膨らむ。必要になってから足す。
  `@followup agent_project のフック指定を CLI からも上書きできるようにする（--hook <cap>=<module>） :: PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py TestHookResolution`
- **[範囲外]** `codd_gate_regression.py`（sibling CLI）は yaml へ `regression_cmd` を恒久注入する経路で、本設計の module フックとは独立。変更不要。
