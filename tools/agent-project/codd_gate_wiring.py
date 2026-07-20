#!/usr/bin/env python3
"""codd_gate_wiring — codd-gate の実測検出・結線の有無判定・推奨コマンド文字列・所見の整形
（tools/agent-project 配下。`codd_gate_detect`（a1）と `codd_gate_status`（a4）に続く「a2」相当の glue）。

`codd_gate_detect.py` は「実バイナリへ問い合わせる生の判定値」（`resolve_codd_gate` /
`get_version` / `check_repos_schema_compat` / `detect_capabilities`）を、`codd_gate_status.py` は
「実測値を受け取って no-op 縮退する合流点」（`build_status`）を提供するが、どちらも意図的に
「実測そのものの呼び出し配線」を含めない（各モジュールの docstring 参照）。本モジュールは
その配線を1箇所に実装し（`probe_wiring`）、`.agent/agent-project.yaml` の
`regression_cmd`/`intake_cmd`（手書き文字列）が既に codd-gate を指しているか＝**結線の有無**を
判定し（`regression_wired`/`intake_wired`）、未結線かつ codd-gate が使える状態なら実際に注入
できる推奨コマンド文字列を組み立て（`recommend_regression_cmd`/`recommend_intake_cmd`）、
その判定を所見の一覧へ整形する（`render_findings`）。CLI（末尾の `main`）は同じ所見を JSON で
標準出力へ出すだけで、どこへも書かない。

**本モジュールは自分を agent_project パッケージへ結線しない。** 本体（`agent_project/hooks.py`）は
能力キー -> 必須属性名の表だけを持ち、(1) 設定 `hooks:` の明示指定、(2) 未指定時の sibling 走査、
の順でプロバイダを引き当てる。本モジュールは (2) に載らない——契約名 `detect_wiring` /
`doctor_findings` を `def` ではなくファイル末尾の**別名**として公開しており、走査の前置フィルタ
（ソーステキストの `^def <属性名>(`）に一致しないため。結果、この検出レイヤが本体の doctor に
現れるのは「利用者が `hooks:` へ名前を書いた」ときと、上記 CLI を直接叩いたときだけになる
（有効化手順は README.md「一貫性ゲート」節）。零設定で勝手に繋がる経路を残すと、モジュールの
置き場（パッケージ外の sibling）と有効化手順（設定ファイル）が食い違い、利用者から
「なぜ動いているのか・どう止めるのか」が見えなくなる。この不一致は
`tests/test_codd_gate_wiring.py` の TestHookResolution が両方向で固定している。

このモジュールが意図的に含めないもの:
  - `.agent/agent-project.yaml` / `cfg.regression_cmd`・`cfg.intake_cmd` への実書き込み・永続化
    （`codd_gate_regression.py` の冪等 upsert が唯一の書き込み経路。本モジュールは推奨文字列を
    返すだけで、どこへも書かない）
  - `codd-gate tasks --debt` 出力の enqueue 経路統合（`agent_project/model.py` の `run_intake` が
    検出器非依存のパースで担う。本体は `intake_cmd` という差し込み点のみを持ち、codd-gate 固有の
    実装へは依存しない）
  - `mr.py`/`model.py` の実行時フックを `CoddGateStatus.command()` ベースの動的組み立てへ
    置き換えること（現行の `cfg.regression_cmd`/`cfg.intake_cmd` は静的文字列のまま動く前提を
    崩さない）

依存は標準ライブラリと同梱の `codd_gate_detect`（a1）/`codd_gate_status`（a4）/
`codd_gate_routing`（b2）のみ。

CLI:
    python3 codd_gate_wiring.py [--config .agent/agent-project.yaml] [--repos <path>]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SIBLING_DIR = Path(__file__).resolve().parent
if str(_SIBLING_DIR) not in sys.path:
    sys.path.insert(0, str(_SIBLING_DIR))

from codd_gate_detect import (  # noqa: E402
    PROBE_TIMEOUT,
    check_repos_schema_compat,
    detect_capabilities,
    get_version,
    resolve_codd_gate,
)
from codd_gate_routing import resolve_repos_arg  # noqa: E402
from codd_gate_status import CoddGateStatus, build_status  # noqa: E402

# 完了条件のgrep（`regression_cmd:.*codd-gate verify --base`）と同じ語順を判定に使う。
# 語順だけを見て `--repos` 等の追加引数の有無は問わない（手書き設定・自動生成のどちらでも一致させる）。
_REGRESSION_WIRED_RE = re.compile(r"\bcodd-gate\b[^\n]*\bverify\b[^\n]*--base\b")
_INTAKE_WIRED_RE = re.compile(r"\bcodd-gate\b[^\n]*\btasks\b[^\n]*--debt\b")


def regression_wired(regression_cmd: "str | None") -> bool:
    """cfg.regression_cmd が既に codd-gate の差分ゲートを指しているか。"""
    return bool(regression_cmd) and bool(_REGRESSION_WIRED_RE.search(regression_cmd))


def intake_wired(intake_cmd: "str | None") -> bool:
    """cfg.intake_cmd が既に codd-gate の負債取り込みを指しているか。"""
    return bool(intake_cmd) and bool(_INTAKE_WIRED_RE.search(intake_cmd))


def recommend_regression_cmd(repos_path: "str | Path", vcwd: "str | Path | None" = None) -> str:
    """未結線時に cfg.regression_cmd へ注入できる推奨コマンド文字列。

    `$KIRO_BASE_REV` はシェル変数参照のまま埋め込む（`codd_gate_base.py` の設計どおり、実行時に
    `_settle_task` が venv 経由で注入する値をそのまま展開させる。ここでは具体的な rev に解決しない）。
    """
    return f'codd-gate verify --base "$KIRO_BASE_REV" --repos {resolve_repos_arg(repos_path, vcwd)}'


def recommend_intake_cmd(repos_path: "str | Path", vcwd: "str | Path | None" = None) -> str:
    """未結線時に cfg.intake_cmd へ注入できる推奨コマンド文字列。"""
    return f'codd-gate tasks --debt --repos {resolve_repos_arg(repos_path, vcwd)}'


@dataclass(frozen=True)
class WiringJudgment:
    """codd-gate 検出結果 + 結線の有無の一過性の判定値（`CoddGateStatus` と同じくディスク・
    schemas/ には乗らない）。"""
    status: CoddGateStatus
    capabilities: "dict[str, bool]" = field(default_factory=dict)
    regression_wired: bool = False
    intake_wired: bool = False
    recommended_regression_cmd: "str | None" = None
    recommended_intake_cmd: "str | None" = None

    @property
    def usable(self) -> bool:
        return self.status.usable

    @property
    def fully_wired(self) -> bool:
        return self.regression_wired and self.intake_wired

    @property
    def actionable(self) -> bool:
        """codd-gate は使えるのに未結線 → 推奨コマンドを提示できる状態（doctor finding の対象）。"""
        return self.usable and not self.fully_wired


def judge_wiring(
    status: CoddGateStatus,
    regression_cmd: "str | None",
    intake_cmd: "str | None",
    capabilities: "dict[str, bool] | None" = None,
    repos_path: "str | Path | None" = None,
    vcwd: "str | Path | None" = None,
) -> WiringJudgment:
    """実測済みの `CoddGateStatus`・capabilities を受け取り、結線の有無と推奨コマンドを組み立てる
    純粋関数（I/O なし）。`probe_wiring` から実測値を渡されて呼ばれるほか、テストや別の実測経路
    からも直接呼べる（`build_status` と同じ「合流点は提供するが唯一の入口ではない」設計）。

    推奨コマンドを出すのは「usable（実在・バージョン・schema すべて OK）」かつ「repos_path が
    分かっている」かつ「該当サブコマンドが capabilities で使えると分かっている（未知なら楽観的に
    True 扱い＝capabilities 自体を実測しなかった呼び出し元向けの既定）」の3条件がすべて揃った
    ときだけ。未結線でも `status.usable` が False（未検出・非互換）なら推奨しない
    （使えないものへの配線を勧めない）。
    """
    capabilities = capabilities or {}
    reg_wired = regression_wired(regression_cmd)
    intake_is_wired = intake_wired(intake_cmd)
    can_recommend = status.usable and repos_path is not None
    rec_regression = (
        recommend_regression_cmd(repos_path, vcwd)
        if can_recommend and not reg_wired and capabilities.get("verify", True) else None)
    rec_intake = (
        recommend_intake_cmd(repos_path, vcwd)
        if can_recommend and not intake_is_wired and capabilities.get("debt", True) else None)
    return WiringJudgment(
        status=status, capabilities=capabilities,
        regression_wired=reg_wired, intake_wired=intake_is_wired,
        recommended_regression_cmd=rec_regression, recommended_intake_cmd=rec_intake)


def probe_wiring(
    regression_cmd: "str | None" = None,
    intake_cmd: "str | None" = None,
    repos_path: "str | Path | None" = None,
    vcwd: "str | Path | None" = None,
    explicit: "str | None" = None,
    which=shutil.which,
    run=subprocess.run,
    timeout: int = PROBE_TIMEOUT,
) -> WiringJudgment:
    """codd-gate の実在・バージョン・schemas 互換・能力を実測し（a1 に続く「a2」の配線）、
    結線の有無まで判定した `WiringJudgment` を返す。

    実在確認 → バージョン取得 → schema 互換（`repos_path` が実ファイルのときだけ）→ 能力検出、の
    短絡順で進み、`resolve_codd_gate` 自体が例外を投げなくても環境依存の I/O が予期しない例外を
    出す可能性に備えて実在確認だけは捕捉する（`codd_gate_status.detect_status` と同じ理由）。
    以降の各実測関数（`get_version`/`check_repos_schema_compat`/`detect_capabilities`）は自身が
    timeout・非0終了・パース不能を「不明」に丸める設計のため、ここでは追加の例外捕捉をしない。
    """
    try:
        binary = resolve_codd_gate(explicit, which=which)
    except Exception:
        binary = None
    if binary is None:
        return judge_wiring(build_status(None), regression_cmd, intake_cmd,
                             repos_path=repos_path, vcwd=vcwd)
    version = get_version(binary, run=run, timeout=timeout)
    schema_ok, schema_detail = True, ""
    if repos_path is not None and Path(repos_path).is_file():
        schema_ok, schema_detail = check_repos_schema_compat(repos_path)
    status = build_status(binary, version=version, version_known=version is not None,
                           schema_ok=schema_ok, schema_detail=schema_detail)
    capabilities = detect_capabilities(binary, run=run, timeout=timeout) if status.usable else {}
    return judge_wiring(status, regression_cmd, intake_cmd, capabilities=capabilities,
                         repos_path=repos_path, vcwd=vcwd)


def render_findings(judgment: WiringJudgment) -> "list[dict]":
    """`WiringJudgment` を doctor.py の finding 形式（category/severity/title/evidence/fix）へ
    変換する。完全結線済みなら空リスト（`doctor_audit_findings` が ok な check を畳むのと同じ
    方針）。未検出・非互換は `status.findings`（既に info/warn/critical で分類済み）をそのまま
    使う。usable だが未結線のときだけ、このモジュール独自の info finding を追加する
    （severity=info: codd-gate 連携は任意機能であり、未結線は壊れているわけではない）。
    """
    if not judgment.status.usable:
        return list(judgment.status.findings)
    out: "list[dict]" = []
    if judgment.recommended_regression_cmd:
        out.append({
            "category": "config", "severity": "info",
            "title": "codd-gate は検出済みだが regression_cmd が未結線",
            "evidence": "cfg.regression_cmd が codd-gate verify --base を指していない",
            "fix": f"agent-project.yaml に設定: regression_cmd: '{judgment.recommended_regression_cmd}'"})
    if judgment.recommended_intake_cmd:
        out.append({
            "category": "config", "severity": "info",
            "title": "codd-gate は検出済みだが intake_cmd が未結線",
            "evidence": "cfg.intake_cmd が codd-gate tasks --debt を指していない",
            "fix": f"agent-project.yaml に設定: intake_cmd: '{judgment.recommended_intake_cmd}'"})
    return out


# --- 本体（agent_project）から明示指定で呼ぶための別名 ---------------------------------------
# `agent_project/hooks.py` の HOOK_CAPABILITIES が求める属性名。`hooks: {wiring: codd_gate_wiring}`
# と書いた利用者に対してだけ、本体の doctor がここへ到達する。
#
# 別名で公開して `def detect_wiring(` / `def doctor_findings(` という行を持たないのは意図的
# （module docstring 参照）。本体の sibling 自動走査はソーステキストの前置フィルタでプロバイダを
# 選ぶため、`def` で書くと零設定でも当選してしまい、「パッケージ外に置いたのに設定なしで繋がる」
# という有効化手順との食い違いが戻る。
detect_wiring = probe_wiring
doctor_findings = render_findings


def _read_yaml_value(text: str, key: str) -> "str | None":
    """agent-project.yaml のトップレベル1行スカラを読む（`codd_gate_regression._ROOT_RE` と同じ、
    PyYAML に依存しない最小の行読み取り）。CLI が結線の有無を見るためだけに使う。"""
    m = re.search(r"^%s:\s*(.+?)\s*$" % re.escape(key), text, re.M)
    return (m.group(1).strip().strip("'\"") or None) if m else None


def main(argv: "list[str] | None" = None) -> int:
    """検出と結線状況を調べ、所見を JSON で標準出力へ出す（読むだけ・書き込まない）。

    `hooks:` を書かずに「いま codd-gate が使えるか／未結線なら何を貼ればよいか」だけ知りたい
    利用者向けの経路。所見の有無は終了コードに反映しない——一貫性ゲートは任意機能で、
    未結線は壊れている状態ではない（`render_findings` の severity=info と同じ理由）。
    """
    parser = argparse.ArgumentParser(
        description="codd-gate の検出と regression_cmd/intake_cmd の結線状況を所見として出力する")
    parser.add_argument("--config", default=".agent/agent-project.yaml",
                        help="結線の有無を読む agent-project.yaml（既定 .agent/agent-project.yaml）")
    parser.add_argument("--repos", default=None,
                        help="--repos に渡す repos.json パス（既定は設定の root: から推定）")
    parser.add_argument("--codd-gate", dest="codd_gate", default=None,
                        help="codd-gate の実体を明示指定（既定は PATH→同梱パスの順で自動解決）")
    args = parser.parse_args(argv)

    path = Path(args.config)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if args.repos:
        repos_path = args.repos
    else:
        # `root:` -> `<root>/repos.json` の推定規約は regression 側に1つだけ置く（再実装しない）。
        # ライブラリとして import されるときの依存を増やさないよう、CLI の中でだけ import する。
        from codd_gate_regression import infer_default_repos_path
        repos_path = infer_default_repos_path(text)

    judgment = probe_wiring(
        regression_cmd=_read_yaml_value(text, "regression_cmd"),
        intake_cmd=_read_yaml_value(text, "intake_cmd"),
        repos_path=repos_path, explicit=args.codd_gate)
    print(json.dumps({
        "usable": judgment.usable, "reason": judgment.status.reason,
        "regression_wired": judgment.regression_wired,
        "intake_wired": judgment.intake_wired,
        "findings": render_findings(judgment),
        "config": str(path), "repos": str(repos_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
