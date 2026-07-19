from __future__ import annotations
# hooks.py — 任意フック（本体の外にあるプロバイダ module）の解決。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
#
# 本体は「能力（capability）」だけを知り、それを満たす module が何という名前かは知らない。
# 名前の所有権は設定（`hooks:`）にあり、未指定なら sibling ディレクトリを能力で走査して
# 引き当てる。この分離により、本体側にプロバイダの固有名を一切書かずに済む
# （＝差し込み点だけを持ち、プロバイダ実装へはハード依存しない）。
#
# フックはすべて任意機能。解決失敗は None へ畳み、呼び出し側は no-op へ縮退する。
# ここから例外を投げない——診断や配線の欠落で本体の実行が止まる方が失うものが桁違いに大きい。

# 本体がプロバイダへ求める契約の全部。能力キー -> そのプロバイダが持つべき属性名。
# 本体はこの表に無い属性へ触らない（プロバイダの返り値・内部は不透明として扱う）。
HOOK_CAPABILITIES = {
    "wiring.detect":   ("detect_wiring",),
    "wiring.findings": ("doctor_findings",),
}

# 能力キー -> 解決済み module（解決できなければ None）。None もキャッシュする＝プロバイダが
# 居ない環境で毎回 sibling を走査し直さない。cfg.hooks を読むので、cfg を差し替えるテストは
# _HOOK_CACHE.clear() してから呼ぶこと。
_HOOK_CACHE: "dict[str, object | None]" = {}


def _hook_sibling_dir() -> Path:
    """既定の走査先＝このパッケージを収めたディレクトリ（`tools/agent-project`）。

    __init__.py の exec 合成により、断片の中でも __file__ は常に
    `agent_project/__init__.py` の実パスを指す。その1階層上が sibling の置き場になる。"""
    return Path(__file__).resolve().parent.parent


def _hook_ensure_path(sib: Path) -> None:
    """sibling ディレクトリを import 可能にする（パッケージ内の他所と同じ sys.path 解決）。"""
    if str(sib) not in sys.path:
        sys.path.insert(0, str(sib))


def _hook_required(capability: str) -> "tuple[str, ...]":
    return HOOK_CAPABILITIES.get(capability, ())


def _hook_configured_name(capability: str, cfg) -> "str | None":
    """設定による明示指定を引く。フルキー（`wiring.detect`）→ 前半キー（`wiring`）の順。

    前半キーで系統をまとめて1つの module へ寄せられる（通常はこちらで足りる）。片方の能力だけ
    別プロバイダへ振り替えたいときにフルキーで上書きする。"""
    hooks = getattr(cfg, "hooks", None) if cfg is not None else None
    if not isinstance(hooks, dict):
        return None
    for key in (capability, capability.split(".", 1)[0]):
        name = hooks.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _hook_import(name: str, required: "tuple[str, ...]"):
    """module 名を import し、必須属性をすべて持つときだけ返す。それ以外は None。

    「解決失敗」は import できないことに限らない。プロバイダは本体の外にあり本体が版を握れない
    以上、(a) 同名の無関係な module が sys.path 先頭に居て別物へ解決される、(b) import 時に
    プロバイダ自身が例外を投げる、(c) 解決できても本体が呼ぶ関数を持たない、のいずれも起こる。
    どれも None へ畳む。"""
    import importlib
    try:
        mod = importlib.import_module(name)
    except Exception:                     # ImportError に限らない（上記 (b)）
        return None
    return mod if all(hasattr(mod, a) for a in required) else None   # 上記 (a)/(c)


def _hook_scan_siblings(required: "tuple[str, ...]", sib: "Path | None" = None):
    """sibling ディレクトリを走査し、必須属性をすべて満たす最初の module を返す（昇順・決定的）。

    import する前にソーステキストで前置フィルタをかける。無関係な sibling を総当たりで import
    すると、その import 副作用（グローバル初期化・パス操作・プロセス起動）を本体が浴びる。
    契約の関数を定義していないファイルは、そもそも読み込まない。"""
    sib = _hook_sibling_dir() if sib is None else sib
    if not required or not sib.is_dir():
        return None
    pats = [re.compile(r"^def %s\s*\(" % re.escape(a), re.M) for a in required]
    for path in sorted(sib.glob("*.py")):
        name = path.stem
        if name.startswith("_") or not name.isidentifier():
            continue                      # `agent-project.py` のような非 module 名はここで落ちる
        try:
            src = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not all(p.search(src) for p in pats):
            continue
        _hook_ensure_path(sib)
        mod = _hook_import(name, required)
        if mod is not None:
            return mod
    return None


def _hook_journal(cfg, line: str) -> None:
    """採用したプロバイダを journal へ1行残す（どの module が配線されたかを後から追える）。
    journal が書けない状況でフック解決を失敗させない。"""
    try:
        append_journal(cfg.journal, line)
    except Exception:
        pass


def _hook_provider(capability: str, cfg=None):
    """能力キーからプロバイダ module を解決する。全フックの唯一の入口。例外を投げない。

    解決順は 設定の明示指定 → sibling の能力スキャン。明示指定が解決できないときは自動検出へ
    落ちず None を返す——人が名前を書いた以上、その意図を黙って別物で置き換えない（設定ミスは
    doctor が `_hook_resolution_error` 経由で warn として可視化する）。未指定での不在は任意機能が
    無いだけなので無言で None。

    cfg 省略時は明示指定を飛ばして sibling スキャンだけ行う（cfg を組み立てる前の経路向け）。"""
    if capability in _HOOK_CACHE:
        return _HOOK_CACHE[capability]
    required = _hook_required(capability)
    name = _hook_configured_name(capability, cfg)
    if name:
        _hook_ensure_path(_hook_sibling_dir())   # 明示指定でも sibling を指せるようにする
        mod = _hook_import(name, required)
        _HOOK_CACHE[capability] = mod
        return mod
    mod = _hook_scan_siblings(required)
    if mod is not None and cfg is not None:
        _hook_journal(cfg, f"フック {capability} を {getattr(mod, '__name__', '?')} で解決")
    _HOOK_CACHE[capability] = mod
    return mod


def _hook_resolution_error(capability: str, cfg) -> "str | None":
    """明示指定があるのに解決できなかったときだけ理由文字列を返す。既定（未指定）は None。

    未指定で見つからないのは任意機能の不在＝所見にしない。人が名前を書いたのに効いていない場合
    だけ知らせる（現行はどちらも一律無言で、設定ミスが観測できなかった）。"""
    hooks = getattr(cfg, "hooks", None) if cfg is not None else None
    if hooks is not None and not isinstance(hooks, dict):
        return f"hooks の設定型が不正（{type(hooks).__name__}）。能力キー -> module 名の対応表を書く"
    name = _hook_configured_name(capability, cfg)
    if not name:
        return None
    if _hook_provider(capability, cfg) is not None:
        return None
    required = " / ".join(_hook_required(capability)) or "必須属性"
    return f"hooks.{capability.split('.', 1)[0]} = '{name}' が import できない（または {required} を持たない）"
