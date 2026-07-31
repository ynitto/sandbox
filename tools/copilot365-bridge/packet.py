#!/usr/bin/env python
"""
packet.py — copilot365-bridge の「エアロック・パケット」コーデック（依存ゼロ・コア）

世界B（ローカル: kiro-cli / GitHub Copilot / GitLab）と世界A（MS365: Copilot /
SharePoint / Outlook）の間にはソフトの橋が無く、越境は

  - Playwright によるブラウザ操作（B→A、結果を読んで持ち帰る）
  - 人間のコピー&ペースト（A→B / B→A）

に限られる。どちらの経路でも壊れにくいよう、ルーティング情報と本文を 1 個の
自己記述テキスト（=パケット）に固める。人間がコピペするとき本文が途中で切れても
気づけるよう、本文の CRC32 チェックサムを必ず付ける。

このモジュールは外部依存を持たない（標準ライブラリのみ）。CLI 本体
（copilot365_bridge.py）から import して使う一方、`python packet.py selftest`
で単体テストも回せる。

パケット書式（v1）:

    ===== COPILOT365 PACKET v1 BEGIN =====
    id: 01JABCDE...
    to: ms365
    from: kiro
    intent: ask
    created: 2026-06-13T02:30:00Z
    reply_to:
    crc32: a1b2c3d4
    ----- BODY -----
    <自由テキスト / Markdown 本文>
    ===== COPILOT365 PACKET v1 END =====

- ヘッダはフラットな `key: value`（ルーティング専用、YAML 不要）。
- `crc32` は BODY の UTF-8 バイト列に対する zlib.crc32（8 桁 16 進）。
- BEGIN/END マーカーで囲うので、人間が UI のごみ（周囲の文字）ごとコピーしても
  `extract_all()` がパケットだけを取り出せる。
"""

from __future__ import annotations

import re
import time
import uuid
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PROTOCOL_VERSION = "1"
BEGIN_MARKER = f"===== COPILOT365 PACKET v{PROTOCOL_VERSION} BEGIN ====="
END_MARKER = f"===== COPILOT365 PACKET v{PROTOCOL_VERSION} END ====="
BODY_MARKER = "----- BODY -----"

# 既知のヘッダ（順序を固定して人間が読みやすくする）。未知キーも保持される。
_KNOWN_HEADER_ORDER = ["id", "to", "from", "intent", "created", "reply_to", "crc32"]

# 越境の宛先（ルーティング）。世界Aの面と世界Bの面。
KNOWN_DESTINATIONS = {"ms365", "outlook", "sharepoint", "kiro", "gitlab", "human"}
# パケットの意図。
KNOWN_INTENTS = {"ask", "answer", "approve", "notify", "context", "error"}


class PacketError(ValueError):
    """パケットの整形・解析に関する不正を表す例外。"""


def _new_id() -> str:
    """時刻順にソートできる短い ID（ULID 風だが依存を足さない簡易版）。"""
    ms = int(time.time() * 1000)
    return f"{ms:013d}-{uuid.uuid4().hex[:8]}"


def body_checksum(body: str) -> str:
    """本文の CRC32 を 8 桁 16 進で返す（コピペ取りこぼし検知用）。"""
    return f"{zlib.crc32(body.encode('utf-8')) & 0xFFFFFFFF:08x}"


@dataclass
class Packet:
    """エアロック・パケット 1 通。"""

    body: str = ""
    to: str = "ms365"
    sender: str = "kiro"  # `from` は予約語なので sender 属性に対応づける
    intent: str = "ask"
    id: str = field(default_factory=_new_id)
    created: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    reply_to: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    # ── ヘッダ辞書との相互変換 ────────────────────────────────────────────
    def headers(self) -> Dict[str, str]:
        h = {
            "id": self.id,
            "to": self.to,
            "from": self.sender,
            "intent": self.intent,
            "created": self.created,
            "reply_to": self.reply_to,
            "crc32": body_checksum(self.body),
        }
        h.update(self.extra)
        return h

    # ── エンコード ────────────────────────────────────────────────────────
    def encode(self) -> str:
        """パケットを 1 個のテキストに整形する（クリップボード / ファイル用）。"""
        headers = self.headers()
        ordered_keys = _KNOWN_HEADER_ORDER + [
            k for k in headers if k not in _KNOWN_HEADER_ORDER
        ]
        lines = [BEGIN_MARKER]
        for key in ordered_keys:
            if key in headers:
                lines.append(f"{key}: {headers[key]}")
        lines.append(BODY_MARKER)
        lines.append(self.body)
        lines.append(END_MARKER)
        return "\n".join(lines) + "\n"

    # ── バリデーション ────────────────────────────────────────────────────
    def validate(self) -> List[str]:
        """軽い妥当性チェック。問題点のリスト（空なら OK）を返す。"""
        problems: List[str] = []
        if self.to not in KNOWN_DESTINATIONS:
            problems.append(f"未知の宛先 to={self.to!r}")
        if self.intent not in KNOWN_INTENTS:
            problems.append(f"未知の intent={self.intent!r}")
        if not self.body.strip():
            problems.append("本文が空")
        return problems


# ── デコード ──────────────────────────────────────────────────────────────
_PACKET_RE = re.compile(
    re.escape(BEGIN_MARKER) + r"\n(.*?)" + re.escape(END_MARKER),
    re.DOTALL,
)


def _decode_block(block: str) -> Packet:
    """BEGIN/END を取り除いた中身 1 個を Packet にする。"""
    if BODY_MARKER not in block:
        raise PacketError("BODY マーカーが見つかりません")
    header_part, body_part = block.split(BODY_MARKER, 1)

    headers: Dict[str, str] = {}
    for raw in header_part.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ":" not in line:
            raise PacketError(f"ヘッダ行を解析できません: {raw!r}")
        key, value = line.split(":", 1)
        headers[key.strip()] = value.strip()

    # 本文は BODY マーカー直後の改行 1 個を剥がし、末尾の改行も 1 個だけ落とす。
    body = body_part
    if body.startswith("\n"):
        body = body[1:]
    if body.endswith("\n"):
        body = body[:-1]

    expected = headers.pop("crc32", None)
    if expected is not None:
        actual = body_checksum(body)
        if actual != expected:
            raise PacketError(
                "チェックサム不一致（コピペ途中切れの可能性）: "
                f"crc32 ヘッダ={expected} 実際={actual}"
            )

    extra = {
        k: v for k, v in headers.items() if k not in _KNOWN_HEADER_ORDER
    }
    return Packet(
        body=body,
        to=headers.get("to", "ms365"),
        sender=headers.get("from", "kiro"),
        intent=headers.get("intent", "ask"),
        id=headers.get("id", _new_id()),
        created=headers.get("created", ""),
        reply_to=headers.get("reply_to", ""),
        extra=extra,
    )


def decode(text: str) -> Packet:
    """1 個のパケットを含むテキストを Packet にする（複数あれば最初の 1 個）。"""
    packets = extract_all(text)
    if not packets:
        raise PacketError("パケットが見つかりません（BEGIN/END マーカー無し）")
    return packets[0]


def extract_all(text: str) -> List[Packet]:
    """任意のテキスト（UI のごみ混じりでも可）から全パケットを取り出す。"""
    out: List[Packet] = []
    for m in _PACKET_RE.finditer(text):
        out.append(_decode_block(m.group(1)))
    return out


# ── 単体テスト（依存ゼロで実行可能） ────────────────────────────────────────
def _selftest() -> int:
    failures = 0

    def check(cond: bool, msg: str) -> None:
        nonlocal failures
        status = "ok  " if cond else "FAIL"
        if not cond:
            failures += 1
        print(f"  [{status}] {msg}")

    print("packet.py selftest")

    # 往復（roundtrip）
    p = Packet(body="請求書 API の仕様変更を実装して。\n合意済み。", to="ms365",
               sender="kiro", intent="ask")
    wire = p.encode()
    q = decode(wire)
    check(q.body == p.body, "roundtrip: 本文が一致")
    check(q.to == "ms365" and q.sender == "kiro" and q.intent == "ask",
          "roundtrip: ルーティングが一致")

    # 周囲にごみがあっても抽出できる
    noisy = "コピーしました\n" + wire + "\n[送信] [コピー] という UI ボタン"
    got = extract_all(noisy)
    check(len(got) == 1 and got[0].body == p.body, "ごみ混じりから 1 通抽出")

    # 複数パケット
    p2 = Packet(body="2 通目", to="gitlab", sender="ms365", intent="answer")
    multi = wire + "\n間に雑談\n" + p2.encode()
    got2 = extract_all(multi)
    check(len(got2) == 2 and got2[1].body == "2 通目", "複数パケットを順に抽出")

    # チェックサム改ざん（コピペ途中切れ）を検出
    truncated = wire.replace("実装して。", "実装し")  # 本文だけ壊す
    try:
        decode(truncated)
        check(False, "途中切れを検出（例外が出るべき）")
    except PacketError:
        check(True, "途中切れを CRC32 で検出")

    # 未知の宛先・空本文の検証
    bad = Packet(body="", to="nowhere", intent="ask")
    probs = bad.validate()
    check(len(probs) == 2, "validate: 未知宛先 + 空本文を検出")

    # 既知の宛先・intent の往復で extra ヘッダも保持
    p3 = Packet(body="x", extra={"thread": "T-42"})
    r3 = decode(p3.encode())
    check(r3.extra.get("thread") == "T-42", "extra ヘッダを保持")

    print(f"\n{'PASSED' if failures == 0 else 'FAILED'}: {failures} 件の失敗")
    return 1 if failures else 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(_selftest())
    print(__doc__)
