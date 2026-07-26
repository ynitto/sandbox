#!/usr/bin/env python3
"""codd_gate_status — codd-gate CLI の検出結果と所見（tools/agent-project 配下の sibling 部品）。

生の検出値を、CLI で表示できる finding と no-op 縮退済みの ``CoddGateStatus`` にまとめる。
実測は ``codd_gate_detect``、結線状況の表示は ``codd_gate_wiring``、YAML の更新は
``codd_gate_regression`` が担う。

このモジュールは ``agent_project`` パッケージを import せず、自動配線・設定書き込み・package
doctor への登録も行わない。依存は標準ライブラリと同梱の ``codd_gate_detect`` のみ。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from codd_gate_detect import resolve_codd_gate

MIN_SUPPORTED_VERSION = (1, 0, 0)


@dataclass(frozen=True)
class CoddGateStatus:
    """codd-gate CLI 検出結果の一過性の値オブジェクト。

    ディスクにも schemas/ にも乗らない。findings が1件でもあれば usable は自動的に False になる
    ため、CLI 呼び出し側は failure の種類を区別せず no-op にできる。
    """
    binary: "list[str] | None"
    version: "tuple[int, int, int] | None" = None
    findings: "list[dict]" = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.binary is not None and not self.findings

    def command(self, *args: str) -> "list[str] | None":
        """引数を付けた argv を返す。usable でなければ None。"""
        return [*self.binary, *args] if self.usable else None

    @property
    def reason(self) -> str:
        """CLI に表示するスキップ理由の一文。usable なら空文字列。"""
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
        "fix": "export_repo_registry の出力を確認する（agent-project 側の不具合）"}


def build_status(
    binary: "list[str] | None",
    version: "tuple[int, int, int] | None" = None,
    version_known: bool = True,
    schema_ok: bool = True,
    schema_detail: str = "",
) -> CoddGateStatus:
    """生の判定結果を実在 → バージョン → schema の短絡順で finding 化し、
    no-op 縮退済みの CoddGateStatus を組み立てる。純粋関数で例外は投げない。

    前段が失敗していれば後段は評価しない。不明・不足はすべて利用不可側に倒し、CLI は
    ``findings`` をそのまま表示できる。
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
    """codd-gate の実在（resolve_codd_gate）のみを根拠に CoddGateStatus を返す。

    バージョン取得・schemas 互換判定は行わないため、実在さえ確認できれば
    version_known=True・schema_ok=True の既定で build_status に渡す（usable=True になる）。
    実測済みのバージョン・schema 適合がある呼び出し側は、この関数を経由せず
    build_status(binary, version=..., version_known=..., schema_ok=...) を直接呼べば
    同じ no-op 縮退へ合流できる。

    resolve_codd_gate 自体は例外を投げない設計（a1）だが、環境依存の I/O
    （shutil.which / Path.exists）が予期しない例外を出す可能性に備えてここでも捕捉し、
    検出に失敗しても「未検出」の CLI 所見へ縮退させる。
    """
    try:
        binary = resolve_codd_gate(explicit, which=which)
    except Exception:
        binary = None
    return build_status(binary)
