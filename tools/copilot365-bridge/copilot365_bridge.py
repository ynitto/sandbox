#!/usr/bin/env python
"""
copilot365_bridge.py — MS365 Copilot を「呼び出せる道具」に変える世界B側ブリッジ

【背景】社内では kiro-cli / GitHub Copilot(VSCode) / MS365 Copilot のみ利用可。
GitHub・他SaaS は不可。世界A（MS365）と世界B（ローカル + GitLab）の間にソフトの橋は
無く、越境は次しか無い:

  - B→A : Playwright で MS365 Web を人間のように操作し、回答を読んで持ち帰る（pull）
  - B→A : GitLab 通知 → Outlook メール（限定的）
  - A→B : 人間のコピー&ペースト（クリップボードは通る）

本ツールは世界B（kiro-cli が司令塔）から動き、MS365 Copilot を

    ask_org_context(prompt) -> answer

という関数のように扱えるようにする。閉じた対話製品をサブルーチン化するのが狙い。

【サブコマンド】
  ask              MS365 Copilot に 1 問投げて回答パケットを得る（Playwright / --mock）
  daemon           outbox を監視し to=ms365 のパケットを ask して inbox へ返す
  watch-approvals  Outlook の承認返信を検知して resume シグナルを出す（Playwright / --mock）
  clip-export      パケットをクリップボードへ載せる（人間が MS365 に貼る用）
  clip-import      クリップボードからパケットを取り込む（人間が MS365 からコピーした物）
  selftest         パケットコーデックの単体テスト

【設計上の注意（重要）】
  - MS365 の自動操作は社内ポリシー / Conditional Access に抵触しうる。本人の
    ログイン済みプロファイルを使うヘッドフル運用を既定とし、認証情報は保存しない。
  - DOM 変更でセレクタは壊れる。セレクタは設定ファイル 1 箇所に集約し、失敗時は
    スクリーンショット + （任意で）OCR フォールバックに落とす。

依存: Playwright（ask / watch-approvals の実操作時のみ）、pyperclip（clip-* のみ）、
      PyYAML（YAML 設定を使う場合のみ）。いずれも無くても --mock / JSON 設定で動く。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# コアのパケットコーデック（依存ゼロ）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import packet as pkt  # noqa: E402

STATE_HOME = Path(os.environ.get("COPILOT365_BRIDGE_HOME", "~/.copilot365-bridge")).expanduser()
DEFAULT_CONFIG_NAMES = [
    "copilot365-bridge.yaml",
    "copilot365-bridge.yml",
    "copilot365-bridge.json",
]


# ── 設定読み込み（YAML 任意 / JSON 可） ──────────────────────────────────────
def _load_config(explicit: Optional[str]) -> Dict[str, Any]:
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        for name in DEFAULT_CONFIG_NAMES:
            candidates.append(Path.cwd() / name)
            candidates.append(Path.home() / name)
    for path in candidates:
        if path.is_file():
            return _read_config_file(path)
    # 設定が無くても既定値で動けるようにする
    return {}


def _read_config_file(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            _die("YAML 設定を読むには PyYAML が必要です（pip install pyyaml）。"
                 "JSON 設定なら依存無しで使えます。")
        return yaml.safe_load(text) or {}
    return json.loads(text)


# 既定セレクタ。DOM が変わったらここ（または設定ファイル）だけ直す。
DEFAULT_SELECTORS = {
    "url": "https://m365.cloud.microsoft/chat/",
    "prompt_box": 'textarea[aria-label*="Copilot"], div[contenteditable="true"]',
    "send_button": 'button[aria-label*="Send"], button[type="submit"]',
    "response": '[data-content="message"], div.response-message-body',
    "response_done": '[data-testid="copilot-response-complete"]',
}


def _selectors(cfg: Dict[str, Any]) -> Dict[str, str]:
    sel = dict(DEFAULT_SELECTORS)
    sel.update(cfg.get("selectors", {}) or {})
    return sel


def _die(msg: str, code: int = 1) -> "None":
    print(f"[copilot365-bridge] ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _log(msg: str) -> None:
    print(f"[copilot365-bridge] {msg}", file=sys.stderr)


# ── MS365 Copilot ドライバ（Playwright） ────────────────────────────────────
class Copilot365Driver:
    """MS365 Copilot Web を Playwright で操作する薄いドライバ。

    実 DOM はテナント / 時期で変わるため、セレクタは設定で差し替える前提。
    ここでは「プロンプト投入 → 完了待ち → 回答テキスト回収」の骨格を提供する。
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.sel = _selectors(cfg)
        # 本人のログイン済みセッションを再利用する永続プロファイル（認証情報は保存しない）
        self.user_data_dir = Path(
            cfg.get("user_data_dir", STATE_HOME / "browser-profile")
        ).expanduser()
        self.headless = bool(cfg.get("headless", False))  # 既定はヘッドフル（本人セッション）
        self.timeout_ms = int(cfg.get("timeout_ms", 120_000))

    def ask(self, prompt: str) -> str:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            _die("Playwright が必要です: pip install playwright && playwright install chromium")

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:  # type: ignore
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
            )
            page = ctx.new_page()
            try:
                page.goto(self.sel["url"], timeout=self.timeout_ms)
                # 初回はここで人間が SSO/MFA を済ませる（ヘッドフルだから手で通せる）
                box = page.wait_for_selector(self.sel["prompt_box"], timeout=self.timeout_ms)
                box.click()
                box.fill(prompt)
                page.keyboard.press("Enter")
                # 応答完了マーカーを待つ。無ければ無音時間で完了とみなす。
                answer = self._collect_response(page)
                return answer
            finally:
                ctx.close()

    def _collect_response(self, page: Any) -> str:
        try:
            page.wait_for_selector(self.sel["response_done"], timeout=self.timeout_ms)
        except Exception:
            # 完了マーカーが取れないテナント向けの保険: 一定時間テキストが伸びなくなるまで待つ
            self._wait_until_settled(page)
        nodes = page.query_selector_all(self.sel["response"])
        if not nodes:
            # DOM が取れない時はスクショ + OCR フォールバック（任意）
            shot = STATE_HOME / "last-response.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot))
            return self._ocr_fallback(shot)
        return nodes[-1].inner_text().strip()

    def _wait_until_settled(self, page: Any, quiet_s: float = 2.5, max_s: float = 60) -> None:
        last = ""
        stable_since = time.time()
        deadline = time.time() + max_s
        while time.time() < deadline:
            nodes = page.query_selector_all(self.sel["response"])
            cur = nodes[-1].inner_text() if nodes else ""
            if cur != last:
                last = cur
                stable_since = time.time()
            elif time.time() - stable_since >= quiet_s:
                return
            time.sleep(0.5)

    def _ocr_fallback(self, image_path: Path) -> str:
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
        except ImportError:
            _log(f"DOM 取得失敗。スクショを保存: {image_path}（OCR は pytesseract+Pillow で有効化）")
            return f"[OCR 未導入: 回答は {image_path} を参照]"
        return pytesseract.image_to_string(Image.open(image_path), lang="jpn+eng").strip()


# ── サブコマンド実装 ────────────────────────────────────────────────────────
def _mock_answer(prompt: str) -> str:
    return (
        "【MOCK 回答】MS365 Copilot 応答のスタブです。\n"
        f"受け取ったプロンプト先頭: {prompt.strip()[:80]!r}\n"
        "本番では Playwright が m365.cloud.microsoft の Copilot から回収します。"
    )


def cmd_ask(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    prompt = args.prompt if args.prompt else sys.stdin.read()
    if not prompt.strip():
        _die("プロンプトが空です（引数か標準入力で渡してください）。")

    if args.mock:
        answer = _mock_answer(prompt)
    else:
        answer = Copilot365Driver(cfg).ask(prompt)

    out = pkt.Packet(body=answer, to="kiro", sender="ms365", intent="answer",
                     reply_to=args.reply_to or "")
    if args.packet:
        sys.stdout.write(out.encode())
    else:
        sys.stdout.write(answer + "\n")
    return 0


def _mailbox_dirs(cfg: Dict[str, Any]) -> Dict[str, Path]:
    base = Path(cfg.get("mailbox_dir", STATE_HOME / "mailbox")).expanduser()
    dirs = {
        "outbox": base / "outbox",   # B が「MS365 に聞きたい」依頼を置く
        "inbox": base / "inbox",     # MS365 の回答を返す
        "done": base / "done",       # 処理済みの退避
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def cmd_daemon(args: argparse.Namespace) -> int:
    """outbox を監視し to=ms365 のパケットを ask して inbox に回答を返す常駐。"""
    cfg = _load_config(args.config)
    dirs = _mailbox_dirs(cfg)
    driver = None if args.mock else Copilot365Driver(cfg)
    interval = float(args.interval or cfg.get("poll_interval_s", 5))
    _log(f"daemon 起動 outbox={dirs['outbox']} interval={interval}s mock={args.mock}")

    try:
        while True:
            for f in sorted(dirs["outbox"].glob("*.txt")):
                _process_outbox_file(f, dirs, driver, args.mock)
            if args.once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("停止しました（Ctrl+C）。")
    return 0


def _process_outbox_file(f: Path, dirs: Dict[str, Path], driver, mock: bool) -> None:
    text = f.read_text(encoding="utf-8")
    try:
        packets = pkt.extract_all(text)
    except pkt.PacketError as e:
        _log(f"パケット解析失敗 {f.name}: {e}")
        return
    if not packets:
        _log(f"パケット無し（スキップ） {f.name}")
        return
    for p in packets:
        if p.to not in ("ms365", "outlook", "sharepoint"):
            continue
        _log(f"ask 実行 id={p.id} intent={p.intent}")
        answer = _mock_answer(p.body) if mock else driver.ask(p.body)
        reply = pkt.Packet(body=answer, to=p.sender, sender="ms365",
                           intent="answer", reply_to=p.id)
        (dirs["inbox"] / f"{p.id}.txt").write_text(reply.encode(), encoding="utf-8")
    f.rename(dirs["done"] / f.name)


def cmd_watch_approvals(args: argparse.Namespace) -> int:
    """承認メール（人間が Outlook で返信）を検知して resume シグナルを出す。

    Playwright で Outlook Web を読むのが本番。--mock では擬似シグナルを 1 回出す。
    検知後は GitLab/issue-mailbox 側へ resume パケットを書き出す想定。
    """
    cfg = _load_config(args.config)
    dirs = _mailbox_dirs(cfg)
    keyword = args.keyword or cfg.get("approval_keyword", "approve")

    if args.mock:
        sig = pkt.Packet(body=f"approved (keyword={keyword})", to="kiro",
                         sender="outlook", intent="approve",
                         reply_to=args.reply_to or "")
        (dirs["inbox"] / f"approval-{sig.id}.txt").write_text(sig.encode(), encoding="utf-8")
        _log(f"[mock] 承認シグナルを inbox に書き出しました id={sig.id}")
        return 0

    # 本番: Outlook Web を Playwright で開き、未読返信から承認キーワードを探す（骨格）。
    _die("Outlook 承認検知の実装はセレクタ設定が必要です。"
         "--mock で配線を確認するか、selectors.outlook_* を設定してください。")
    return 1


def cmd_clip_export(args: argparse.Namespace) -> int:
    """パケットをクリップボードへ。人間がそのまま MS365 Copilot に貼れる。"""
    body = args.text if args.text else sys.stdin.read()
    p = pkt.Packet(body=body, to=args.to, sender="kiro", intent=args.intent)
    wire = p.encode()
    if _clipboard_set(wire):
        _log(f"クリップボードへコピーしました（id={p.id}, crc32={pkt.body_checksum(body)}）。"
             "MS365 にそのまま貼り付けてください。")
    else:
        sys.stdout.write(wire)  # クリップボード非対応環境では標準出力にフォールバック
    return 0


def cmd_clip_import(args: argparse.Namespace) -> int:
    """クリップボード（人間が MS365 からコピーした内容）からパケットを取り込む。"""
    text = _clipboard_get()
    if text is None:
        text = sys.stdin.read()
    packets = pkt.extract_all(text)
    if not packets:
        _die("クリップボードにパケットが見つかりません（BEGIN/END マーカー無し）。")
    cfg = _load_config(args.config)
    dirs = _mailbox_dirs(cfg)
    for p in packets:
        dest = dirs["inbox"] / f"{p.id}.txt"
        dest.write_text(p.encode(), encoding="utf-8")
        _log(f"取り込み: id={p.id} from={p.sender} intent={p.intent} -> {dest}")
    return 0


def _clipboard_set(text: str) -> bool:
    try:
        import pyperclip  # type: ignore
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _clipboard_get() -> Optional[str]:
    try:
        import pyperclip  # type: ignore
        return pyperclip.paste()
    except Exception:
        return None


def cmd_selftest(args: argparse.Namespace) -> int:
    return pkt._selftest()


# ── 引数パーサ ──────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="copilot365-bridge",
        description="MS365 Copilot を世界B（kiro-cli）から呼び出せる道具にするブリッジ",
    )
    parser.add_argument("--config", help="設定ファイル（YAML/JSON）パス")
    sub = parser.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("ask", help="MS365 Copilot に 1 問投げて回答を得る")
    sp.add_argument("prompt", nargs="?", help="プロンプト（省略時は標準入力）")
    sp.add_argument("--mock", action="store_true", help="MS365 に触れずスタブ回答")
    sp.add_argument("--packet", action="store_true", help="回答をパケット形式で出力")
    sp.add_argument("--reply-to", help="返信元パケット ID")
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("daemon", help="outbox を監視して MS365 へ中継する常駐")
    sp.add_argument("--mock", action="store_true")
    sp.add_argument("--interval", type=float, help="ポーリング間隔（秒）")
    sp.add_argument("--once", action="store_true", help="1 巡だけ実行して終了")
    sp.set_defaults(func=cmd_daemon)

    sp = sub.add_parser("watch-approvals", help="Outlook の承認返信を検知")
    sp.add_argument("--mock", action="store_true")
    sp.add_argument("--keyword", help="承認とみなすキーワード")
    sp.add_argument("--reply-to", help="承認対象のパケット ID")
    sp.set_defaults(func=cmd_watch_approvals)

    sp = sub.add_parser("clip-export", help="パケットをクリップボードへ")
    sp.add_argument("text", nargs="?", help="本文（省略時は標準入力）")
    sp.add_argument("--to", default="ms365", help="宛先（既定 ms365）")
    sp.add_argument("--intent", default="ask", help="意図（既定 ask）")
    sp.set_defaults(func=cmd_clip_export)

    sp = sub.add_parser("clip-import", help="クリップボードからパケットを取り込む")
    sp.set_defaults(func=cmd_clip_import)

    sp = sub.add_parser("selftest", help="パケットコーデックの単体テスト")
    sp.set_defaults(func=cmd_selftest)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
