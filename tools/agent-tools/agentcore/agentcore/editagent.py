#!/usr/bin/env python3
"""agentcore.editagent — aider を使わない編集適用エージェント（対照実装）。

設計: docs/plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md §3.6・未決 5。

## 何をするか

「対象ファイルが決まった局所編集」だけを行う **single-shot** のエージェントである。
渡されたファイルを材料に載せ、SEARCH/REPLACE ブロックで直させ、`agentcore.editblock` で
当てる。探索も、シェルも、テスト実行も持たない——それらは engine とハーネスの仕事で、
aider にも持たせていない（2026-08-18 評価 §8.3 の分担表）。

## aider から引き継ぐ契約（去就を測るために揃える）

| 面 | どうしたか |
|---|---|
| 編集の綴り | SEARCH/REPLACE。`editblock` が aider と同じ 3 段で当てる |
| `--dry-run` | `--readonly` として持つ（`readonly: enforced` の根拠） |
| 実測 usage | `@agent-usage tokens_in=… tokens_out=…` を stderr へ |
| 言い直し | 当たらなかったときだけ、失敗の理由を添えて 1 回だけ投げ直す |

**言い直しの回数は 1 回に切る。** aider の reflection に当たるが、無制限にすると
「弱いモデルが同じ間違いを繰り返して壁時計を焼く」形になる。ここは編集適用エンジンで
あって、再投入の判断はハーネス（受入ゲート）の仕事である。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentcore import editblock, ollama_loop
from agentcore.hostenv import load_profile_env

# 編集の綴りだけを教える。手順・役割はプロンプト本文（呼び出し側）が持つ——ここが太ると
# 毎ターン再送される固定費になる（aider の system + reminder + few-shot で約 6.3 KB）。
SYSTEM_PROMPT = """あなたはコード編集者です。渡されたファイルだけを直します。

変更は **SEARCH/REPLACE ブロック**で書いてください。書式は厳密です:

パス/ファイル名.py
<<<<<<< SEARCH
（変更前の原文をそのまま。字下げも 1 文字ずつ一致させる）
=======
（変更後）
>>>>>>> REPLACE

規約:
1. SEARCH には**いま実際にファイルにある行**をそのまま写す。書き換えた後の姿ではない。
2. 変更する箇所ごとに 1 ブロック。1 ブロックへ詰め込まない。
3. ブロックの直前の行にファイルのパスを単独で書く。
4. 説明は短く。ブロック以外の文章は要らない。
5. 渡されていないファイルは変更しない。"""

_MAX_RETRIES = 1        # 当たらなかったときの言い直し（無制限にしない。上の docstring）


def _materials(files, reads, cwd: Path) -> str:
    """編集対象と参照を本文へ載せる。

    **編集対象がまだ無いのは正常**である——「このファイルを作れ」という依頼がその形で
    来る（aider も SEARCH が空のブロックで新規作成を受ける）。無い対象は「新規」と明示して
    載せ、空の SEARCH で書かせる。参照が読めないのは依頼の前提が崩れているので落とす。
    """
    out = []
    for rel in files:
        path = cwd / rel
        if path.is_file():
            out.append(f"{rel}\n```\n{path.read_text(encoding='utf-8')}\n```")
        else:
            out.append(f"{rel}（まだ存在しません。新規作成する場合は SEARCH を空にします）")
    for rel in reads:
        path = cwd / rel
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"参照のファイルを読めません: {rel}（{exc}）") from exc
        out.append(f"{rel}（参照。変更しない）\n```\n{body}\n```")
    return "\n\n".join(out)


def _usage(result: dict) -> "tuple[int, int]":
    return int(result.get("tokens_in") or 0), int(result.get("tokens_out") or 0)


def run_edit(*, model: str, message: str, files, reads, cwd, readonly: bool = False,
             think: "bool | None" = None, out=None, err=None) -> int:
    """1 回分の編集。戻り値は終了コード（0=当てた / 1=当てられなかった）。"""
    out = out or sys.stdout
    err = err or sys.stderr
    cwd = Path(cwd).resolve()
    prompt = (f"{SYSTEM_PROMPT}\n\n## 材料\n\n{_materials(files, reads, cwd)}\n\n"
              f"## 依頼\n\n{message}\n")
    tokens_in = tokens_out = 0
    feedback = ""
    for attempt in range(_MAX_RETRIES + 1):
        body = prompt if not feedback else f"{prompt}\n## 直前の失敗\n\n{feedback}\n"
        result = ollama_loop.run_plain(model, body, think=think)
        got_in, got_out = _usage(result)
        tokens_in += got_in
        tokens_out += got_out
        text = str(result.get("text") or "")
        try:
            blocks = editblock.find_blocks(text)
            if not blocks:
                raise editblock.ApplyError(
                    "SEARCH/REPLACE ブロックがありません（説明だけでは適用できません）")
            touched = editblock.apply_blocks(blocks, cwd, dry_run=readonly)
        except (editblock.ApplyError, RuntimeError) as exc:
            feedback = str(exc)
            if attempt >= _MAX_RETRIES:
                print(f"@agent-usage tokens_in={tokens_in} tokens_out={tokens_out}", file=err)
                print(f"[agent-error:env] editagent: {feedback}", file=err)
                out.write(text)
                return 1
            continue
        print(f"@agent-usage tokens_in={tokens_in} tokens_out={tokens_out}", file=err)
        out.write(text if text.endswith("\n") else text + "\n")
        out.write(("（dry-run: 書き込みませんでした）" if readonly else "適用しました")
                  + ": " + ", ".join(touched) + "\n")
        return 0
    return 1                                        # pragma: no cover - ループで返る


def main(argv=None) -> int:
    load_profile_env()                              # 非ログインシェルでも OLLAMA_* を効かせる
    parser = argparse.ArgumentParser(prog="agent-herd edit", add_help=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--message", default="")
    parser.add_argument("--file", action="append", default=[], help="編集対象（繰り返し可）")
    parser.add_argument("--read", action="append", default=[], help="参照のみ（繰り返し可）")
    parser.add_argument("--dir", default=None)
    parser.add_argument("--readonly", action="store_true", help="当たるか確かめるだけ（dry-run）")
    parser.add_argument("--think", choices=("on", "off"), default="off")
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code or 2)
    message = args.message or sys.stdin.read()
    if not message.strip():
        print("[agent-error:env] editagent: 依頼が空です", file=sys.stderr)
        return 2
    if not args.file:
        # 編集対象が無いと「読んで感想を言うだけ」になる。aider が同じ場面で
        # 「チャットに追加してくれ」と言って終わるのと同じ失敗なので、先に断る。
        print("[agent-error:env] editagent: --file がありません（編集対象を渡してください）",
              file=sys.stderr)
        return 2
    try:
        return run_edit(model=args.model, message=message, files=args.file,
                        reads=args.read, cwd=args.dir or Path.cwd(),
                        readonly=args.readonly, think=(args.think == "on"))
    except (RuntimeError, OSError) as exc:
        print(f"[agent-error:env] editagent: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
