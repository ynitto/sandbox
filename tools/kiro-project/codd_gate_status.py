#!/usr/bin/env python3
"""codd_gate_status — codd-gate 検出結果の値オブジェクトと no-op 縮退（tools/kiro-project 配下）。

d2（.kiro-project/bus/runs/run-20260712-213419-5922/artifacts/d2/
codd-gate-status-interface-design.md）の CoddGateStatus データ構造・アクセサをそのまま実装し、
d1（同 run の artifacts/d1/codd-gate-autodetect-judgment-design.md）3節のフォールバック方針表を
finding 生成ロジックへ落とす。

責務は「codd-gate が未検出・非互換のいずれであっても、例外を外へ漏らさず usable=False の
CoddGateStatus を返す（no-op 縮退）」の1点に絞る。usable が False のとき command() は None を
返すため、呼び出し側（regression/acceptance/enqueue の3フック、b1-b3/c1-c2/e1-e2）は
`if status.command(...):` の1行だけで済み、codd-gate が使えない・非互換な環境でも自動配線せず
既存挙動のまま通過する。

このモジュールが意図的に含めないもの（同一 run の別タスクの責務）:
  - バージョン取得・schemas 互換判定の実測（subprocess 呼び出し・regex パース）（a2）
  - プロセス内キャッシュ（a3）
  - kiro-project.py 本体（cfg.codd_gate フィールド新設・3フックへの結線）（b1-b3/c1-c2/e1-e2）

依存は標準ライブラリと同梱の codd_gate_detect（a1）のみ。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from codd_gate_detect import resolve_codd_gate

MIN_SUPPORTED_VERSION = (1, 0, 0)


@dataclass(frozen=True)
class CoddGateStatus:
    """codd-gate 検出結果のプロセス内一過性の値オブジェクト（d2 2節）。

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
    """生の判定結果を d1 3節の短絡順（実在 → バージョン → schema）で finding 化し、
    no-op 縮退済みの CoddGateStatus を組み立てる。純粋関数で例外は投げない。

    前段が失敗していれば後段は評価しない（d1 の「不明・不足はすべて連携しない側に倒す」方針）。
    どの経路で失敗しても findings が1件積まれ usable=False → command() は None になるため、
    呼び出し側（a2 のバージョン/schema 実測、b 系のフック配線）は失敗理由を区別せず
    同じ no-op 経路へ合流できる。
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


def detect_status(explicit: "str | None" = None, which=shutil.which) -> CoddGateStatus:
    """codd-gate の実在（a1 の resolve_codd_gate）のみを根拠に CoddGateStatus を返す。

    バージョン取得・schemas 互換判定（a2）はまだ合流していないため、実在さえ確認できれば
    version_known=True・schema_ok=True の既定で build_status に渡す（usable=True になる）。
    a2/b 系が実測したバージョン・schema 適合を得たら、この関数を経由せず
    build_status(binary, version=..., version_known=..., schema_ok=...) を直接呼べば
    同じ no-op 縮退へ合流できる——このモジュールが提供するのは「合流点」であって
    「唯一の入口」ではない。

    resolve_codd_gate 自体は例外を投げない設計（a1）だが、環境依存の I/O
    （shutil.which / Path.exists）が予期しない例外を出す可能性に備えてここでも捕捉し、
    検出のどの段階で失敗しても「未検出」へ縮退させる。これにより kiro-project 本体は
    codd-gate 連携の失敗を一切意識せず、既存挙動のまま動き続けられる。
    """
    try:
        binary = resolve_codd_gate(explicit, which=which)
    except Exception:
        binary = None
    return build_status(binary)
