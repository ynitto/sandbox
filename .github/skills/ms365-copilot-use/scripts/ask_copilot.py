#!/usr/bin/env python3
"""Send a prompt to Microsoft 365 Copilot Chat via Playwright and capture the answer.

Uses a persistent Chromium profile so corporate SSO sessions are reused.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

def _import_playwright():
    try:
        from playwright.sync_api import (  # noqa: F401
            BrowserContext,
            Page,
            TimeoutError as PWTimeoutError,
            sync_playwright,
        )
    except ImportError:
        sys.stderr.write(
            "playwright がインストールされていません。\n"
            "  pip install playwright && playwright install chromium\n"
        )
        sys.exit(2)
    return sync_playwright, PWTimeoutError

DEFAULT_URL = "https://m365.cloud.microsoft/chat"
DEFAULT_PROFILE = str(Path.home() / ".ms365_copilot_profile")

# 入力欄候補。先頭から順に試す。
INPUT_SELECTORS: list[str] = [
    'div[contenteditable="true"][role="textbox"]',
    'textarea[aria-label*="Copilot" i]',
    'textarea[aria-label*="message" i]',
    'textarea[placeholder*="Ask" i]',
    'textarea[placeholder*="メッセージ"]',
    'div[contenteditable="true"]',
    '[data-testid="chat-input"]',
    'textarea',
]

# 回答メッセージ候補。最後の要素を回答として扱う。
ASSISTANT_MESSAGE_SELECTORS: list[str] = [
    '[data-testid*="assistant-message"]',
    '[data-author-role="assistant"]',
    'div[role="article"]',
    '[class*="assistant" i][class*="message" i]',
]

# 「停止」ボタン候補（応答ストリーミング中のみ表示）。
STOP_BUTTON_SELECTORS: list[str] = [
    'button[aria-label*="Stop" i]',
    'button[aria-label*="停止"]',
    'button[title*="Stop" i]',
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt:
        return args.prompt
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    if args.login:
        return ""
    sys.stderr.write("プロンプトを --prompt, --prompt-file, 標準入力のいずれかで渡してください。\n")
    sys.exit(2)


def first_visible(page: Any, selectors: list[str], timeout_ms: int) -> Any | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=500):
                    return loc
            except Exception:
                continue
        page.wait_for_timeout(250)
    return None


def wait_for_input(page: Any, ui_timeout_s: int) -> Any:
    loc = first_visible(page, INPUT_SELECTORS, ui_timeout_s * 1000)
    if loc is None:
        raise RuntimeError("Copilot の入力欄が見つかりません。UI 変更か未サインインの可能性があります。")
    return loc


def send_prompt(page: Any, input_loc: Any, prompt: str) -> None:
    input_loc.click()
    # textarea / contenteditable 両対応。fill が効かない場合は type() にフォールバック。
    try:
        input_loc.fill(prompt)
    except Exception:
        input_loc.type(prompt, delay=10)
    page.keyboard.press("Enter")


def get_last_assistant_text(page: Any) -> str | None:
    for sel in ASSISTANT_MESSAGE_SELECTORS:
        try:
            items = page.locator(sel)
            count = items.count()
            if count == 0:
                continue
            return items.nth(count - 1).inner_text(timeout=1000).strip()
        except Exception:
            continue
    return None


def is_streaming(page: Any) -> bool:
    for sel in STOP_BUTTON_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    return False


def wait_for_response_complete(
    page: Any,
    response_timeout_s: int,
    stable_seconds: float,
    initial_text: str | None,
) -> str:
    """Poll the last assistant message until it stops changing for `stable_seconds`."""
    deadline = time.monotonic() + response_timeout_s
    last_text = initial_text or ""
    last_change = time.monotonic()
    saw_streaming = False

    while time.monotonic() < deadline:
        text = get_last_assistant_text(page) or ""
        streaming = is_streaming(page)
        if streaming:
            saw_streaming = True

        if text and text != last_text:
            last_text = text
            last_change = time.monotonic()

        # 応答が初期テキストから変わっていて、ストリーミング表示が消え、
        # かつ stable_seconds の間更新がなければ完了とみなす。
        idle = time.monotonic() - last_change
        if (
            last_text
            and last_text != (initial_text or "")
            and not streaming
            and idle >= stable_seconds
            and (saw_streaming or idle >= max(stable_seconds * 2, 6))
        ):
            return last_text

        page.wait_for_timeout(500)

    if last_text and last_text != (initial_text or ""):
        sys.stderr.write(
            f"[warn] 応答完了の検出がタイムアウト ({response_timeout_s}s)。"
            "現在見えている最後の回答を採用します。\n"
        )
        return last_text
    raise RuntimeError(f"応答が取得できません (timeout={response_timeout_s}s)")


def extract_markdown(page: Any) -> str | None:
    """Try to convert the last assistant message DOM into Markdown.

    Best-effort: headings, lists, code blocks, links. Falls back to inner_text.
    """
    js = r"""
    (selectors) => {
      const pickLast = (selList) => {
        for (const s of selList) {
          const els = document.querySelectorAll(s);
          if (els.length) return els[els.length - 1];
        }
        return null;
      };
      const root = pickLast(selectors);
      if (!root) return null;

      const toMd = (node) => {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent;
        if (node.nodeType !== Node.ELEMENT_NODE) return '';
        const tag = node.tagName.toLowerCase();
        const kids = () => Array.from(node.childNodes).map(toMd).join('');
        switch (tag) {
          case 'br': return '\n';
          case 'strong': case 'b': return `**${kids()}**`;
          case 'em': case 'i': return `*${kids()}*`;
          case 'code':
            if (node.parentElement && node.parentElement.tagName.toLowerCase() === 'pre') return kids();
            return '`' + kids() + '`';
          case 'pre': {
            const code = node.querySelector('code');
            const lang = code && (code.className.match(/language-([\w-]+)/) || [])[1] || '';
            const body = (code ? code.innerText : node.innerText).replace(/\n+$/, '');
            return '\n```' + lang + '\n' + body + '\n```\n';
          }
          case 'a': {
            const href = node.getAttribute('href') || '';
            return `[${kids()}](${href})`;
          }
          case 'h1': return `\n# ${kids()}\n`;
          case 'h2': return `\n## ${kids()}\n`;
          case 'h3': return `\n### ${kids()}\n`;
          case 'h4': return `\n#### ${kids()}\n`;
          case 'li': {
            const parent = node.parentElement && node.parentElement.tagName.toLowerCase();
            const idx = Array.from(node.parentElement?.children || []).indexOf(node) + 1;
            const prefix = parent === 'ol' ? `${idx}. ` : '- ';
            return prefix + kids().trim() + '\n';
          }
          case 'ul': case 'ol': return '\n' + kids() + '\n';
          case 'p': return kids() + '\n\n';
          case 'blockquote': return kids().split('\n').map(l => l ? '> ' + l : l).join('\n') + '\n';
          case 'table': {
            const rows = Array.from(node.querySelectorAll('tr'));
            if (!rows.length) return kids();
            const out = [];
            rows.forEach((tr, i) => {
              const cells = Array.from(tr.children).map(c => c.innerText.trim().replace(/\|/g, '\\|'));
              out.push('| ' + cells.join(' | ') + ' |');
              if (i === 0) out.push('| ' + cells.map(() => '---').join(' | ') + ' |');
            });
            return '\n' + out.join('\n') + '\n';
          }
          default: return kids();
        }
      };
      return toMd(root).replace(/\n{3,}/g, '\n\n').trim();
    }
    """
    try:
        return page.evaluate(js, ASSISTANT_MESSAGE_SELECTORS)
    except Exception:
        return None


def extract_citations(page: Any) -> list[dict[str, str]]:
    """Extract citations / source links from the last assistant message."""
    js = r"""
    (selectors) => {
      const pickLast = (selList) => {
        for (const s of selList) {
          const els = document.querySelectorAll(s);
          if (els.length) return els[els.length - 1];
        }
        return null;
      };
      const root = pickLast(selectors);
      if (!root) return [];
      const links = Array.from(root.querySelectorAll('a[href]'));
      const seen = new Set();
      const out = [];
      for (const a of links) {
        const url = a.href;
        const title = (a.innerText || a.getAttribute('aria-label') || a.title || url).trim();
        if (!url || seen.has(url)) continue;
        seen.add(url);
        out.push({ title, url });
      }
      return out;
    }
    """
    try:
        return page.evaluate(js, ASSISTANT_MESSAGE_SELECTORS) or []
    except Exception:
        return []


def launch_context(p: Any, args: argparse.Namespace) -> Any:
    Path(args.user_data_dir).mkdir(parents=True, exist_ok=True)
    launch_kwargs: dict[str, Any] = {
        "user_data_dir": args.user_data_dir,
        "headless": (not args.headed) and (not args.login),
        "viewport": {"width": 1280, "height": 900},
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if args.channel == "msedge":
        launch_kwargs["channel"] = "msedge"
    return p.chromium.launch_persistent_context(**launch_kwargs)


def build_markdown_doc(prompt: str, answer_md: str, citations: list[dict[str, str]]) -> str:
    lines = [
        "# Microsoft 365 Copilot 回答",
        "",
        f"- 日時: {now_iso()}",
        f"- プロンプト: {prompt}",
        "",
        "## 回答",
        "",
        answer_md.strip(),
        "",
    ]
    if citations:
        lines.append("## 引用")
        lines.append("")
        for i, c in enumerate(citations, 1):
            lines.append(f"{i}. [{c.get('title') or c.get('url')}]({c.get('url')})")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", help="送信するプロンプト")
    parser.add_argument("--prompt-file", help="プロンプトを読み込むファイル")
    parser.add_argument("--url", default=DEFAULT_URL, help="Copilot Chat の URL")
    parser.add_argument("--user-data-dir", default=DEFAULT_PROFILE, help="永続プロファイルの場所")
    parser.add_argument("--output-md", help="回答 Markdown の保存先")
    parser.add_argument("--output-json", help="会話 JSON の保存先")
    parser.add_argument("--headed", action="store_true", help="headed モードで起動")
    parser.add_argument("--login", action="store_true", help="サインイン用に headed で開き、入力欄が見えるまで待って終了")
    parser.add_argument("--response-timeout", type=int, default=180)
    parser.add_argument("--ui-timeout", type=int, default=60)
    parser.add_argument("--stable-seconds", type=float, default=3.0)
    parser.add_argument("--channel", choices=["chromium", "msedge"], default="chromium")
    parser.add_argument("--screenshot", help="回答画面のスクリーンショット保存先")
    args = parser.parse_args()

    prompt = read_prompt(args)

    sync_playwright, _PWTimeoutError = _import_playwright()
    with sync_playwright() as p:
        ctx = launch_context(p, args)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=args.ui_timeout * 1000)

            input_loc = wait_for_input(page, args.ui_timeout)

            if args.login:
                sys.stderr.write("サインイン完了を検知しました。プロファイルを保存して終了します。\n")
                return 0

            initial_text = get_last_assistant_text(page)
            send_prompt(page, input_loc, prompt)

            answer_text = wait_for_response_complete(
                page,
                response_timeout_s=args.response_timeout,
                stable_seconds=args.stable_seconds,
                initial_text=initial_text,
            )
            answer_md = extract_markdown(page) or answer_text
            citations = extract_citations(page)

            if args.screenshot:
                Path(args.screenshot).parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=args.screenshot, full_page=True)

            md_doc = build_markdown_doc(prompt, answer_md, citations)

            if args.output_md:
                Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
                Path(args.output_md).write_text(md_doc, encoding="utf-8")
                sys.stderr.write(f"Markdown を保存しました: {args.output_md}\n")

            if args.output_json:
                payload = {
                    "url": args.url,
                    "timestamp": now_iso(),
                    "messages": [
                        {"role": "user", "text": prompt},
                        {
                            "role": "assistant",
                            "text": answer_text,
                            "markdown": answer_md,
                            "citations": citations,
                        },
                    ],
                }
                Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
                Path(args.output_json).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                sys.stderr.write(f"JSON を保存しました: {args.output_json}\n")

            if not args.output_md and not args.output_json:
                sys.stdout.write(md_doc + "\n")

            return 0
        finally:
            ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
