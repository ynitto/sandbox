"""agent-project の単体テスト — 設定キーの構造（`CONFIG_DEFAULTS` → `Config` の到達性）。

**なぜこのファイルがあるか**（P0-4）: 「`CONFIG_DEFAULTS` にキーはあるのに `Config` へ
渡していない」という欠落を 2 度踏んだ。

1. S5 の `verifier` / `verifier_skill` / `verify_side_effects`（S6 の設定追加時に発覚）
2. S4 の `remote_review` — プロジェクト yaml に `observe` と書いても常に `settle` で、
   移行用スイッチが到達不能の死んだコードになっていた

どちらも「読み出し側が `getattr(cfg, key, 既定)` で庇っていたので、設定が効かないまま
静かに動き続ける」という同じ形をしている。個別に直すだけでは 3 度目が来るので、
**キーを足したら必ずこのテストが落ちる**形で構造を固定する。

    python -m unittest tests.test_config_keys
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


# ---------------------------------------------------------------------------
# 除外リスト（値 = 理由）。**理由の無い除外は書けない**——空文字はテストが落とす。
# ここへ足すときは「なぜ Config を経由しないのが正しいのか」を 1 行で説明できること。
# ---------------------------------------------------------------------------

# (1) 存在検査の除外: `Config` のフィールドを持たないキー。
FIELD_EXEMPT = {
    "root": "パスの起点。Config には root 自体でなく under() で展開した個別パスが載る",
}

# (2) 到達検査の除外: フィールドはあるが、設定ファイルから値を届ける経路が無いキー。
REACH_EXEMPT = {
    "state_repo": "clone 前に `_resolve_state_root` が host.yaml projects[] から直接読む。"
                  "Config のフィールドは --state-repo（CLI 明示）の記録用で、"
                  "`_host_layer` は意図的にこのキーを流さない",
    "state_repo_branch": "同上（projects[].branch が唯一の置き場）",
}

# (3) 消費検査の除外: `Config` へは届くが、**その先で誰も読まない**キー。
# 到達検査（2）は必要条件でしかない——`verify_side_effects` は Config まで届いていたのに
# 存在しない charter 属性から読まれており、`network` と宣言しても制約文は 1 文字も
# 変わらなかった（2026-08-20）。読み手が `getattr` の既定で庇うと例外も出ないので、
# 「誰かが実際に読むか」を別の検査で見る。
CONSUME_EXEMPT = {
    "verifier": "撤去済み機能の残骸（`_INERT_PROJECT_KEYS`）。読み手が無いのが正しい状態で、"
                "CONFIG_DEFAULTS と Config に残しているのは (1)(2) の契約を一様に保つため",
    "verifier_skill": "同上（検証プロンプトの供給は agent-flow 側の責務）",
    "spec_threshold": "`spec_threshold_full` の旧名。`_spec_thresholds` が configfile.py の中で"
                      "新名へ畳むので、消費側は新名しか知らない",
}

# 型から作った番兵では値が変わらないキー（値域・クランプ・正規化があるもの）。
# 「番兵も除外理由も無いキーはテストが落ちる」ので、ここに足すか除外に足すかを必ず選ばせる。
SENTINELS = {
    # 値域を持つ（`_one_of` / 明示リスト）
    "remote_review": "observe",
    "verify_side_effects": "network",
    "plan_sections": "warn",
    "location": "remote",
    "level": "report",
    # 1〜3 にクランプされる（`_spec_thresholds`）
    "spec_threshold": 1,
    "spec_threshold_full": 1,
    "spec_threshold_light": 1,
    # 形のある dict（正規化で落とされない値を渡す）
    "hooks": {"wiring": "probe_wiring"},
    "agents": {"plan": {"agent_cli": "codex"}},
}

# host.yaml だけが値を持つキー（`HOST_SOURCED_KEYS`）を host.yaml のどこへ書くか。
# `update_*` は `update:` マッピング配下、それ以外はトップレベル。
def _host_entry_for(key, value):
    if key.startswith("update_"):
        return {"update": {key[len("update_"):]: value}}
    return {key: value}


def _sentinel(key, dflt):
    if key in SENTINELS:
        return SENTINELS[key]
    if isinstance(dflt, bool):
        return not dflt
    if isinstance(dflt, int):
        return dflt + 7
    if isinstance(dflt, float):
        return dflt + 7.0
    if isinstance(dflt, str):
        return f"{dflt}-sentinel" if dflt else "sentinel"
    if isinstance(dflt, list):
        return ["sentinel"]
    if isinstance(dflt, dict):
        return {"sentinel": "x"}
    return "sentinel"


class ConfigKeyReachabilityTests(unittest.TestCase):
    """`CONFIG_DEFAULTS` の全キーが `Config` へ届くこと。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="config-keys-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _build(self, project: dict, host: "dict | None" = None):
        """設定ファイル → resolve_config → build_config の実チェーンを 1 回通す。
        `Config(**kw)` を直接組むと配線の欠落を検出できない（それが今回の穴）。"""
        root = Path(tempfile.mkdtemp(prefix="config-keys-root-", dir=self.tmp))
        mk_state_repo(root)
        use_host_config(self, dict(host or {}),
                        home=Path(tempfile.mkdtemp(prefix="config-keys-home-", dir=self.tmp)))
        path = root / "agent-project.json"
        path.write_text(json.dumps({"root": str(root), **project}, ensure_ascii=False),
                        encoding="utf-8")
        ns = types.SimpleNamespace(config=str(path))
        # 層の警告・値域の警告は本テストの対象外なので飲む（検査するのは Config の値）
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            km.resolve_config(ns)
            return km.build_config(ns)

    def test_exemptions_carry_a_reason(self):
        # 除外は「理由を書かせる」ためのリスト。理由が空なら除外として認めない。
        for name, table in (("FIELD_EXEMPT", FIELD_EXEMPT), ("REACH_EXEMPT", REACH_EXEMPT)):
            for key, why in table.items():
                self.assertTrue(str(why).strip(), f"{name}[{key}] に理由がありません")
                self.assertIn(key, km.CONFIG_DEFAULTS,
                              f"{name}[{key}] は CONFIG_DEFAULTS に無い（消し忘れ）")

    def test_every_config_default_has_a_config_field(self):
        """存在検査: `CONFIG_DEFAULTS` ⊆ `Config` のフィールド ∪ FIELD_EXEMPT。

        `remote_review` はここで落ちていた——キーはあるのにフィールドが無く、
        読み手の `getattr` 既定に落ちて常に settle になっていた。"""
        fields = {f.name for f in dataclasses.fields(km.Config)}
        missing = sorted(set(km.CONFIG_DEFAULTS) - fields - set(FIELD_EXEMPT))
        self.assertEqual(missing, [],
                         "CONFIG_DEFAULTS にあるのに Config フィールドが無いキー: "
                         f"{missing}。Config へ足すか、FIELD_EXEMPT へ理由付きで登録すること")
        stale = sorted(set(FIELD_EXEMPT) & fields)
        self.assertEqual(stale, [], f"FIELD_EXEMPT に載っているがフィールドがある: {stale}")

    def test_every_config_default_reaches_the_config(self):
        """到達検査: キーごとに非既定値を宣言して `Config` の値が**変わる**ことを確かめる。

        等値ではなく差分を見るのは、クランプ（`_spec_thresholds`）・小文字化（`agent_cli`）・
        パス解決（`under()`）で値が変換されても落ちないようにするため。値域を持つキーは
        `SENTINELS` で有効な別値を与える（番兵も除外も無いキーはここで落ちる）。"""
        fields = {f.name for f in dataclasses.fields(km.Config)}
        base = self._build({})
        stuck = []
        for key, dflt in km.CONFIG_DEFAULTS.items():
            if key in FIELD_EXEMPT or key in REACH_EXEMPT or key not in fields:
                continue
            value = _sentinel(key, dflt)
            if key in km.HOST_SOURCED_KEYS:
                cfg = self._build({}, host=_host_entry_for(key, value))
            else:
                cfg = self._build({key: value})
            if getattr(cfg, key) == getattr(base, key):
                stuck.append(f"{key}（{value!r} を宣言しても {getattr(cfg, key)!r} のまま）")
        self.assertEqual(stuck, [],
                         "設定しても Config へ届かないキー:\n  " + "\n  ".join(stuck)
                         + "\nbuild_config で配線するか、SENTINELS へ有効な別値を、"
                           "経路が無いなら REACH_EXEMPT へ理由付きで登録すること")

    def test_every_config_default_is_read_by_someone(self):
        """消費検査: `Config` へ届いた先で、**誰かが実際にそのキーを読む**こと。

        (2) の到達検査は `Config` までしか見ない。届いた値を誰も読まなければ設定は
        黙って無効のままで、しかも読み手が `getattr(x, key, 既定)` で庇うので例外も
        出ない（`verify_side_effects` はこの隙間に落ちた。2026-08-20）。

        判定は `configfile.py` **以外**のパッケージ内ソースにキー名が現れるかどうか。
        文字列リテラル（`"key"` / `'key'`）と属性アクセス（`.key`）の両方を見る。
        名前を動的に組み立てて読むキーは検出できない（偽陰性）が、それは
        「読み手がいるのに落ちる」側ではないので、検査としては安全側に倒れている。"""
        root = Path(__file__).resolve().parents[1] / "agent_project"
        sources = [p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.py"))
                   if p.name != "configfile.py" and "__pycache__" not in p.parts]
        self.assertTrue(sources, "パッケージのソースを 1 つも読めていない（テストの前提が壊れている）")
        unread = []
        for key in km.CONFIG_DEFAULTS:
            if key in CONSUME_EXEMPT:
                continue
            quoted = "['\"]" + re.escape(key) + "['\"]"
            attr = r"\." + re.escape(key) + r"\b"
            pattern = re.compile(quoted + "|" + attr)
            if not any(pattern.search(text) for text in sources):
                unread.append(key)
        self.assertEqual(sorted(unread), [],
                         "Config へ届くのに誰も読まないキー:\n  " + "\n  ".join(sorted(unread))
                         + "\n読み手を配線するか、意図的に読まないなら CONSUME_EXEMPT へ"
                           "理由付きで登録すること")

    def test_consume_exemptions_carry_a_reason(self):
        for key, why in CONSUME_EXEMPT.items():
            self.assertTrue(str(why).strip(), f"CONSUME_EXEMPT[{key}] に理由がありません")
            self.assertIn(key, km.CONFIG_DEFAULTS,
                          f"CONSUME_EXEMPT[{key}] は CONFIG_DEFAULTS に無い（消し忘れ）")


class NodeNameNormalizationTests(unittest.TestCase):
    """`Config.node` の綴りが板・状態ファイル名と 1 つに揃うこと（P0-3）。

    以前は `Config.node` だけが独自のサニタイズ（小文字化しない）で導出されており、
    大文字を含むホスト名の PC が `status/DESKTOP-X.json`（プロジェクト状態）と
    `nodes/desktop-x.json`（板）の 2 名義になっていた。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="node-name-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for key in ("AGENT_PROJECT_NODE",):
            self._restore(key)

    def _restore(self, key):
        old = os.environ.get(key)
        self.addCleanup(lambda k=key, v=old: os.environ.__setitem__(k, v)
                        if v is not None else os.environ.pop(k, None))
        os.environ.pop(key, None)

    def _build(self, host=None, **cli):
        root = Path(tempfile.mkdtemp(prefix="node-name-root-", dir=self.tmp))
        mk_state_repo(root)
        use_host_config(self, dict(host or {}),
                        home=Path(tempfile.mkdtemp(prefix="node-name-home-", dir=self.tmp)))
        path = root / "agent-project.json"
        path.write_text(json.dumps({"root": str(root)}), encoding="utf-8")
        ns = types.SimpleNamespace(config=str(path), **cli)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            km.resolve_config(ns)
            return km.build_config(ns), err.getvalue()

    def test_uppercase_hostname_yields_the_same_name_everywhere(self):
        with mock.patch.object(km.socket, "gethostname", return_value="DESKTOP-X"):
            cfg, _ = self._build()
            host = km.HostConfig({})
        self.assertEqual(cfg.node, "desktop-x")
        self.assertEqual(host.node_id, "desktop-x", "板の名義と食い違っている")
        self.assertEqual(km.node_status_path(cfg).name, "desktop-x.json")

    def test_env_var_is_normalized_and_reported(self):
        os.environ["AGENT_PROJECT_NODE"] = "MyPC"
        cfg, err = self._build()
        self.assertEqual(cfg.node, "mypc")
        self.assertIn("MyPC", err, "黙って綴りを変えない（指定した名前で動いていないと気付けない）")

    def test_cli_node_is_normalized(self):
        cfg, _ = self._build(node="My PC")
        self.assertEqual(cfg.node, "my-pc")

    def test_declared_node_id_passes_through_unchanged(self):
        cfg, err = self._build(host={"node_id": "pc-a"})
        self.assertEqual(cfg.node, "pc-a")
        self.assertNotIn("正規形へ揃えました", err, "既に正規形なら黙っている")

    def test_task_assignment_matches_across_spelling(self):
        # 人は板の端末一覧（小文字）を見て `- node:` を書く。正規化前に書かれたタスク
        # （大文字）も、同じ PC のものなら拾えること。
        cfg, _ = self._build(node="Desktop-X")
        task = km.Task(id="T1", title="x")
        task.set("node", "desktop-x")
        self.assertTrue(km.task_runnable_here(cfg, task))
        task.set("node", "DESKTOP-X")
        self.assertTrue(km.task_runnable_here(cfg, task))
        task.set("node", "other-pc")
        self.assertFalse(km.task_runnable_here(cfg, task))


class RemoteReviewWiringTests(unittest.TestCase):
    """`remote_review`（S4-5）の配線。属性を手で生やさず、設定ファイル経由で確かめる。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="remote-review-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _build(self, value):
        root = Path(tempfile.mkdtemp(prefix="rr-root-", dir=self.tmp))
        mk_state_repo(root)
        use_host_config(self, {}, home=Path(tempfile.mkdtemp(prefix="rr-home-", dir=self.tmp)))
        path = root / "agent-project.json"
        body = {"root": str(root)}
        if value is not None:
            body["remote_review"] = value
        path.write_text(json.dumps(body), encoding="utf-8")
        ns = types.SimpleNamespace(config=str(path))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            km.resolve_config(ns)
            cfg = km.build_config(ns)
        return cfg, err.getvalue()

    def test_default_is_settle(self):
        cfg, _ = self._build(None)
        self.assertEqual(cfg.remote_review, "settle")

    def test_observe_reaches_config(self):
        cfg, _ = self._build("observe")
        self.assertEqual(cfg.remote_review, "observe")

    def test_unknown_value_falls_back_loudly(self):
        # 黙って settle へ倒すと「observ」の綴り間違いに気付けない（S1 の設計動機）。
        cfg, err = self._build("observ")
        self.assertEqual(cfg.remote_review, "settle")
        self.assertIn("remote_review", err)
