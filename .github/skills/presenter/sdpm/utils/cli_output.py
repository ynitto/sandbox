"""子プロセスの出力（bytes）を文字列にする helper。"""


# decode_cli_output の写し。原本は tools/winauto/winauto.py（単体で配るので共有モジュールに
# 依存できない）。直すときは原本と全部の写しを揃える（本体の一致は
# tools/ci/tests/test_check_subprocess_encoding.py が検査する）。
def decode_cli_output(data) -> str:
    """子プロセスの出力（bytes）を文字列にする。Windows ネイティブの CLI は CP932、WSL は
    UTF-8 で吐くので、どちらが来ても読む。JS 版 decodeCliOutput
    （tools/agent-dashboard/src/features/cowork/main/loopProvider.js:51）と同じ段取り。

    解決の順:
      1. None・空なら ''、すでに str ならそのまま返す
      2. UTF-8 として読めればそれ
      3. 読めなければ CP932
      4. CP932 でも読めなければ UTF-8 の置換文字つき（例外は投げない）
    """
    if not data:
        return ""
    if isinstance(data, str):
        return data
    data = bytes(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp932")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")
