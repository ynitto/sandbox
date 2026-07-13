from __future__ import annotations
# coddgate.py — codd-gate 自動検出・差分ゲート起動引数の統合断片（tools/kiro-project 配下）。
#
# 単体 import しない。kiro_project/__init__.py が _FRAGMENTS 経由でこの断片を共有名前空間へ
# exec 合成する前提で書かれている（_head.py が os/json/re/shutil/subprocess/sys/pathlib.Path/
# dataclasses.dataclass,field を共有名前空間へ import 済みのため、本断片はモジュールレベル
# import を一切持たない）。
#
# パッケージ外に孤立していた次の5ファイル（_FRAGMENTS に載らないため一度も import されず、
# `grep -rq "codd_gate" kiro_project/` が恒久的に満たせなかった原因）の責務をそのまま統合する:
#   tools/kiro-project/codd_gate_base.py    → 差分ゲートの base rev 解決
#   tools/kiro-project/codd_gate_detect.py  → codd-gate バイナリの実在・バージョン・能力の生判定
#   tools/kiro-project/codd_gate_debt.py    → `tasks --debt` 出力のレコード単位パース・正規化
#   tools/kiro-project/codd_gate_routing.py → --repos / --repo-dir 実引数の組み立て
#   tools/kiro-project/codd_gate_status.py  → 検出結果の値オブジェクトと no-op 縮退
# 上記5ファイル自体はまだ tools/kiro-project/tests/test_codd_gate_{detect,routing}.py などが
# `import codd_gate_detect` の形で直接参照しており、削除すると既存テストが壊れるため本断片作成では
# 削除しない（削除・test 側の import 差し替えは _FRAGMENTS 登録とあわせて行う後続タスクの責務）。
#
# codd_gate_hooks.py / codd_gate_invoke.py（上記5ファイルを合成する「合流点」と呼び出し結果の
# 値オブジェクト）は t1/t2 の棚卸し対象（5ファイル）に含まれないため本断片への統合対象外。
# 両ファイルは現状 `from codd_gate_base import ...` 等でこの5ファイルを import しており、
# パッケージ内への取り込み・_FRAGMENTS 登録・regression/acceptance/enqueue への実結線は
# 後続タスクの責務（backlog feedback の手順1後半・2・4）。
#
# 旧版の本ファイル（前回 run r0 で作成。codd_gate_enabled/CoddGateNoopResult/CoddGateDebtStatus/
# codd_gate_debt_status/codd_gate_summary_text という別系統の実装）はどこからも import されておらず
# （grep で使用箇所ゼロを確認済み）、実際に codd_gate_hooks.py・後続の結線調査（t4）が前提とする
# detect_status/CoddGateStatus/parse_debt_output の系統と競合していた。本タスクでは棚卸し済みの
# 5ファイル系統を正典として全面置換し、旧関数群は削除した（後段の評価で要再確認）。


# ---------------------------------------------------------------------------
# base: 差分ゲート（regression_cmd）向け base rev 解決
# ---------------------------------------------------------------------------

FALLBACK_BASE_REV = "HEAD~1"


def resolve_base_rev(
    task_base_branch: "str | None" = None,
    env: "dict[str, str] | None" = None,
) -> str:
    """差分ゲートの基準 rev を解決する。

    優先順位（前段が空ならすぐ次段へ）:
      1. `KIRO_BASE_REV` 環境変数 — 既に注入済み（`git_change_baseline` 等）か
         人/呼び出し元が明示指定したなら、それを常に優先する。
      2. タスクの base ブランチ — charter の repo エントリが持つ `base=`（例 `main`）。
         KIRO_BASE_REV が未注入の場合でも、担当リポジトリの基準ブランチとの差分は取れる。
      3. `HEAD~1` — 上記いずれも得られない最終フォールバック（直前1コミットとの差分）。

    例外は投げない（`env` は plain dict 前提。I/O は行わずローカル判断のみ）。
    """
    env = os.environ if env is None else env
    explicit = (env.get("KIRO_BASE_REV") or "").strip()
    if explicit:
        return explicit
    branch = (task_base_branch or "").strip()
    if branch:
        return branch
    return FALLBACK_BASE_REV


# ---------------------------------------------------------------------------
# detect: codd-gate CLI の実在・能力検出
# ---------------------------------------------------------------------------

CODD_GATE_BINARY_NAME = "codd-gate"
BINARY_NAME = CODD_GATE_BINARY_NAME
# kiro-project.py の wslpath 存在確認等、軽量プローブと同じ値
PROBE_TIMEOUT = 5
_VERSION_RE = re.compile(r"codd-gate (\d+)\.(\d+)\.(\d+)")
_SUBCOMMANDS_RE = re.compile(r"\{([\w,]+)\}")


def resolve_codd_gate(explicit: "str | None" = None, which=shutil.which) -> "list[str] | None":
    """codd-gate の起動 argv prefix を解決する。見つからなければ None。

    resolve_kiro_flow と対称の解決連鎖（explicit → PATH → 同梱パス）を辿るが、
    kiro-flow と異なり codd-gate は任意機能（無くても kiro-project は動く）なので、
    同梱パスにも実体が無ければ「不明な起動コマンドを組み立てない」意味で None を返す。
    戻り値が str ではなく argv prefix の list なのは、同梱パス経由の起動時に
    Python インタプリタを明示する必要があるため（resolve_kiro_flow と同じ理由）。
    """
    if explicit:
        return [sys.executable, explicit] if explicit.endswith(".py") else [explicit]
    found = which(BINARY_NAME)
    if found:
        return [found]
    local = Path(__file__).resolve().parent.parent.parent / "codd-gate" / "codd-gate.py"
    if local.exists():
        return [sys.executable, str(local)]
    return None


def resolve_codd_gate_bin(
    config_bin: "str | None" = None, env: "dict[str, str] | None" = None, which=shutil.which
) -> "str | None":
    """codd-gate 実行ファイルを 環境変数 → 設定ファイル → PATH の優先順で検出する。見つからなければ None。

    `resolve_codd_gate` の explicit 引数（起動 argv 構築の最優先入力）に何を渡すかを決める
    前段の解決連鎖。呼び出し側は
    `resolve_codd_gate(resolve_codd_gate_bin(cfg.codd_gate_bin), which=shutil.which)` のように
    合成すれば、CLI 明示指定が無い環境でも env→config→PATH の順で自動検出した実行ファイルが
    同梱パス解決の手前に差し込まれる。

    `which`（環境依存の I/O）を含め、想定外の例外はすべて外へ漏らさず None（未検出）へ縮退させる
    ——「実行ファイルが見つからない」と「検出処理自体が壊れている」を呼び出し側で区別する
    必要をなくす（「不明・不足はすべて連携しない側に倒す」方針）。
    """
    try:
        active_env = os.environ if env is None else env
        via_env = (active_env.get("CODD_GATE_BIN") or "").strip()
        if via_env:
            return via_env
        via_config = (config_bin or "").strip()
        if via_config:
            return via_config
        found = which(BINARY_NAME)
        return found or None
    except Exception:
        return None


def get_version(
    binary: "list[str]", run=subprocess.run, timeout: int = PROBE_TIMEOUT
) -> "tuple[int, int, int] | None":
    """`<binary> --version` の出力からバージョンタプルを得る。

    `--version` は argparse の `action="version"` で exit 0 直終了する経路（サブコマンド未指定時の
    通常エラー exit 2 とは別）。timeout・非 0 終了・パース不能はすべて「不明」（None）に倒す——
    「わからない」を「大丈夫」に丸めない。
    """
    try:
        proc = run([*binary, "--version"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    m = _VERSION_RE.search(proc.stdout)
    return tuple(int(g) for g in m.groups()) if m else None


def check_repos_schema_compat(repos_path: "str | Path") -> "tuple[bool, str]":
    """repos.json（`export_repo_registry` の出力）が `repos.schema.json` の最小要件
    （トップレベル object、`_` 始まり以外の値が object）を満たすか。

    schemas/ 実データにバージョンフィールドは無いため、semver 比較の代わりに構造チェックで
    代替する。読み込み・パース失敗も非互換として扱う（理由は戻り値の2要素目に残す）。
    """
    try:
        data = json.loads(Path(repos_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"repos.json を読み込めない: {exc}"
    if not isinstance(data, dict):
        return False, "repos.json のトップレベルが object ではない"
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if not isinstance(value, dict):
            return False, f"repos.json のエントリ '{key}' が object ではない"
    return True, ""


def detect_capabilities(
    binary: "list[str]", run=subprocess.run, timeout: int = PROBE_TIMEOUT
) -> "dict[str, bool]":
    """`--help` / `<サブコマンド> --help` を実プローブし、verify・tasks サブコマンドと
    --debt フラグの利用可能性を能力フラグ（`{"verify": bool, "tasks": bool, "debt": bool}`）
    として返す。

    `get_version` はバージョン文字列からの間接的な対応関係だが、こちらは実バイナリの
    argparse 出力に直接問い合わせるため、`--version` が壊れていても独立に成立する。
    プローブ失敗（timeout・非 0 終了・出力不一致）はすべて False に倒す。
    """
    capabilities = {"verify": False, "tasks": False, "debt": False}
    subcommands = _list_subcommands(binary, run=run, timeout=timeout)
    capabilities["verify"] = "verify" in subcommands
    capabilities["tasks"] = "tasks" in subcommands
    debt_checks = [
        _subcommand_supports_flag(binary, sub, "--debt", run=run, timeout=timeout)
        for sub in ("verify", "tasks") if capabilities[sub]
    ]
    capabilities["debt"] = bool(debt_checks) and all(debt_checks)
    return capabilities


def _list_subcommands(binary: "list[str]", run=subprocess.run, timeout: int = PROBE_TIMEOUT) -> "set[str]":
    try:
        proc = run([*binary, "--help"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()
    m = _SUBCOMMANDS_RE.search(proc.stdout)
    return set(m.group(1).split(",")) if m else set()


def _subcommand_supports_flag(
    binary: "list[str]", subcommand: str, flag: str, run=subprocess.run, timeout: int = PROBE_TIMEOUT
) -> bool:
    try:
        proc = run([*binary, subcommand, "--help"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and flag in proc.stdout


# ---------------------------------------------------------------------------
# debt: `codd-gate tasks --debt` 出力のパースとドリフト項目の正規化
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DriftItem:
    """schemas/task.schema.json に正規化した1件のドリフト項目（プロセス内の中間表現）。

    `title`（必須）と `id`（重複投入防止キーとして直接使う想定なので昇格）だけを明示フィールドに
    し、それ以外の既知/未知キーは `fields` にそのまま保持する（additionalProperties: true＝
    前方互換を型の面でも崩さない）。
    """
    title: str
    id: "str | None" = None
    fields: "dict" = field(default_factory=dict)

    def to_spec(self) -> dict:
        """`enqueue_task(cfg, spec)` / `run_intake` がそのまま受け取れる dict へ戻す。"""
        spec = {"title": self.title}
        if self.id:
            spec["id"] = self.id
        spec.update(self.fields)
        return spec


@dataclass(frozen=True)
class DebtParseResult:
    """パース結果。`items` は正規化済みドリフト項目、`errors` は棄却したレコードの理由
    （1レコード1文字列。呼び出し側はこれを journal 等へそのまま流せる）。"""
    items: "list[DriftItem]"
    errors: "list[str]"


def _normalize_record(raw: object, index: int) -> "tuple[DriftItem | None, str | None]":
    if not isinstance(raw, dict):
        return None, f"[{index}] レコードが object ではない（{type(raw).__name__}）"
    title = str(raw.get("title", "") or "").strip()
    if not title:
        return None, f"[{index}] title が空/欠落している（task.schema.json の required を満たさない）"
    raw_id = raw.get("id")
    rid = str(raw_id).strip() or None if raw_id not in (None, "") else None
    fields = {k: v for k, v in raw.items() if k not in ("title", "id")}
    return DriftItem(title=title, id=rid, fields=fields), None


def parse_debt_output(text: str) -> DebtParseResult:
    """`codd-gate tasks --debt`（差分モードの `tasks` も同形式）の stdout を
    `DriftItem` のリストへ正規化する。

    トップレベルは object（1件）でも array（複数件）でもよい——`codd-gate.py` の
    `_emit_tasks` は常に array を吐くが、task.schema.json は「`enqueue --json` と同形式」を
    契約にしており、kiro-project.py の `run_intake` も
    `data if isinstance(data, list) else [data]` で両方を吸収している（それと対称に扱う）。
    空文字列・空白のみは「0件」として扱う（codd-gate 側に該当する負債が無いだけの正常系）。
    1件の不備（非 object・title 欠落）で全体を捨てず、その1件だけ errors に落として残りは
    処理を続ける（呼び出し側のループを止めない）。
    """
    stripped = (text or "").strip()
    if not stripped:
        return DebtParseResult(items=[], errors=[])
    try:
        data = json.loads(stripped)
    except ValueError as exc:
        return DebtParseResult(items=[], errors=[f"JSON として解釈できない: {exc}"])
    records = data if isinstance(data, list) else [data]
    items: "list[DriftItem]" = []
    errors: "list[str]" = []
    for i, raw in enumerate(records):
        item, err = _normalize_record(raw, i)
        if item is not None:
            items.append(item)
        else:
            errors.append(err)
    return DebtParseResult(items=items, errors=errors)


# ---------------------------------------------------------------------------
# routing: repos.json パスと --repo-dir マッピングの引数ビルダ
# ---------------------------------------------------------------------------

DEFAULT_REPO_DIR = "."


def resolve_repos_arg(repos_path: "str | Path", vcwd: "str | Path | None" = None) -> str:
    """`--repos` に渡す値を解決する。

    `vcwd`（regression/acceptance/enqueue が実行される cwd＝解決済みワークスペースのクローン
    ルート）配下に `repos_path` があれば `vcwd` からの相対パス（例 `./.kiro-project/repos.json`）、
    配下になければ絶対パスへフォールバックする。相対パスが解決できるのは repos.json 自体が
    対象リポジトリに git 追跡されている「self-hosted」構成のときだけ——それ以外の構成では
    新規クローンの中に repos.json が存在しないため、絶対パスでなければ壊れる。

    `vcwd` を渡さない呼び出し（kiro-project プロセス自身が repos_path と同じ cwd で動く場合等）
    では `repos_path` をそのまま文字列化する。存在確認は行わない（純粋関数。ファイルが実在するかは
    codd-gate 側の起動時チェックに委ねる）。
    """
    if vcwd is None:
        return str(repos_path)
    rp = Path(repos_path)
    try:
        rel = rp.resolve().relative_to(Path(vcwd).resolve())
    except (ValueError, OSError):
        return str(rp.resolve())
    return f"./{rel.as_posix()}"


def resolve_repo_dir_arg(name: str, dir: str = DEFAULT_REPO_DIR) -> str:
    """`--repo-dir` に渡す `NAME=DIR` の1エントリを組み立てる。

    `dir` の既定値 `.` は、regression/acceptance/enqueue が常に解決済みワークスペースの
    クローンルートを cwd として実行される規約（`_task_verify_cwd`）を前提にしたもの——
    絶対パスを焼き込むとクローン先が変わるたびに壊れるため、vcwd 自体を指す `.` で足りる。
    """
    return f"{name}={dir}"


def build_routing_args(
    repos_path: "str | Path",
    name: str,
    vcwd: "str | Path | None" = None,
    dir: str = DEFAULT_REPO_DIR,
) -> "list[str]":
    """regression/acceptance/enqueue の3フック共通で使う引数ビルダ。

    `status.command("verify", *build_routing_args(repos_path, name, vcwd), "--base", ..., "--strict")`
    のように `CoddGateStatus.command()` へそのまま展開できる
    `["--repos", <値>, "--repo-dir", "<name>=<dir>"]` を返す。
    """
    return [
        "--repos", resolve_repos_arg(repos_path, vcwd),
        "--repo-dir", resolve_repo_dir_arg(name, dir),
    ]


# ---------------------------------------------------------------------------
# status: codd-gate 検出結果の値オブジェクトと no-op 縮退
# ---------------------------------------------------------------------------

MIN_SUPPORTED_VERSION = (1, 0, 0)


@dataclass(frozen=True)
class CoddGateStatus:
    """codd-gate 検出結果のプロセス内一過性の値オブジェクト。

    ディスクにも schemas/ にも乗らない。findings が1件でもあれば usable は自動的に False になる
    ——failure の種類（未インストール・バージョン不明・バージョン下限未満・schema 不適合）を
    呼び出し側が区別する必要はなく、これが no-op 縮退の中核をなす不変条件。
    """
    binary: "list[str] | None"
    version: "tuple[int, int, int] | None" = None
    findings: "list[dict]" = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.binary is not None and not self.findings

    def command(self, *args: str) -> "list[str] | None":
        """引数を付けた argv を返す。usable でなければ None（呼び出し側の if 分岐を1行にする）。"""
        return [*self.binary, *args] if self.usable else None

    @property
    def reason(self) -> str:
        """スキップ理由の一文（journal 等、doctor 以外のログ向け）。usable なら空文字列。"""
        return self.findings[0]["title"] if self.findings else ""


def _finding_not_found() -> dict:
    return {
        "category": "env", "severity": "info",
        "title": "codd-gate が見つからない（PATH・同梱パスのいずれにも無い）",
        "evidence": "shutil.which('codd-gate') と tools/codd-gate/codd-gate.py のいずれも解決できなかった",
        "fix": "codd-gate をインストールするか --codd-gate で実体を指定する（連携は任意機能）"}


def _finding_version_unknown(binary: "list[str]") -> dict:
    return {
        "category": "env", "severity": "warn",
        "title": "codd-gate のバージョンを取得できない",
        "evidence": f"`{' '.join(binary)} --version` が timeout・非0終了・パース不能のいずれか",
        "fix": "codd-gate のインストールを確認する"}


def _finding_version_too_old(binary: "list[str]", version: "tuple[int, int, int]") -> dict:
    return {
        "category": "env", "severity": "warn",
        "title": "codd-gate のバージョンが対応下限未満",
        "evidence": (f"検出バージョン {'.'.join(map(str, version))} < "
                     f"下限 {'.'.join(map(str, MIN_SUPPORTED_VERSION))}"),
        "fix": f"codd-gate を {'.'.join(map(str, MIN_SUPPORTED_VERSION))} 以上へ更新する"}


def _finding_schema_incompatible(detail: str = "") -> dict:
    return {
        "category": "config", "severity": "critical",
        "title": "repos.json の出力契約が repos.schema.json を満たさない",
        "evidence": detail or "export_repo_registry の出力が最小要件（トップレベル object 等）を満たさない",
        "fix": "export_repo_registry の出力を確認する（kiro-project 側の不具合）"}


def build_status(
    binary: "list[str] | None",
    version: "tuple[int, int, int] | None" = None,
    version_known: bool = True,
    schema_ok: bool = True,
    schema_detail: str = "",
) -> CoddGateStatus:
    """生の判定結果を実在 → バージョン → schema の短絡順で finding 化し、no-op 縮退済みの
    CoddGateStatus を組み立てる。純粋関数で例外は投げない。

    前段が失敗していれば後段は評価しない（「不明・不足はすべて連携しない側に倒す」方針）。
    どの経路で失敗しても findings が1件積まれ usable=False → command() は None になるため、
    呼び出し側はバージョン/schema 実測や結線側の失敗理由を区別せず同じ no-op 経路へ合流できる。
    """
    if binary is None:
        return CoddGateStatus(binary=None, version=None, findings=[_finding_not_found()])
    if not version_known:
        return CoddGateStatus(binary=binary, version=None, findings=[_finding_version_unknown(binary)])
    if version is not None and version < MIN_SUPPORTED_VERSION:
        return CoddGateStatus(binary=binary, version=version,
                               findings=[_finding_version_too_old(binary, version)])
    if not schema_ok:
        return CoddGateStatus(binary=binary, version=version,
                               findings=[_finding_schema_incompatible(schema_detail)])
    return CoddGateStatus(binary=binary, version=version, findings=[])


def detect_status(
    explicit: "str | None" = None, which=shutil.which, run=subprocess.run
) -> CoddGateStatus:
    """codd-gate の実在＋バージョン（resolve_codd_gate・get_version）をまとめて確認し、
    任意依存として安全に検出した結果を CoddGateStatus で返す（PATH 探索＋バージョン確認を
    1回で完結させる合流点）。

    schemas 互換判定は repos_path（呼び出し側の文脈）が要るためここでは行わない。
    schema 適合まで確定したい呼び出し側は、この関数を経由せず
    build_status(binary, version=..., version_known=..., schema_ok=...) を直接呼べば
    同じ no-op 縮退へ合流できる——このモジュールが提供するのは「合流点」であって
    「唯一の入口」ではない。

    resolve_codd_gate・get_version はいずれも例外を投げない設計だが、環境依存の I/O
    （shutil.which / Path.exists / subprocess）が予期しない例外を出す可能性に備えてここでも
    捕捉し、検出のどの段階で失敗しても「未検出」（binary=None）または「バージョン不明」
    （version_known=False）へ縮退させる——未インストール環境で例外を外へ漏らさない。
    これにより kiro-project 本体は codd-gate 連携の失敗を一切意識せず、既存挙動のまま
    動き続けられる。
    """
    try:
        binary = resolve_codd_gate(explicit, which=which)
    except Exception:
        binary = None
    if binary is None:
        return build_status(None)
    try:
        version = get_version(binary, run=run)
    except Exception:
        version = None
    return build_status(binary, version=version, version_known=version is not None)
