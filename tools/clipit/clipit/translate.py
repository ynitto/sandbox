"""Google Translate client. Call from a worker thread."""

import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def translate(text):
    japanese = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text))
    source, target = ("ja", "en") if japanese else ("en", "ja")
    query = urlencode({"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text})
    request = Request(
        f"https://translate.googleapis.com/translate_a/single?{query}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=15) as response:
        data = json.load(response)
    result = "".join(segment[0] for segment in data[0] if segment and segment[0])
    if not result.strip():
        raise ValueError("翻訳結果が空です。")
    return result
