"use strict";

function detectSource(text) {
  return /[\u3040-\u30ff\u3400-\u9fff]/.test(text) ? "ja" : "en";
}

async function translate(text, fetchImpl = globalThis.fetch) {
  const source = detectSource(text);
  const query = new URLSearchParams({
    client: "gtx",
    sl: source,
    tl: source === "ja" ? "en" : "ja",
    dt: "t",
    q: text,
  });
  const response = await fetchImpl(`https://translate.googleapis.com/translate_a/single?${query}`, {
    method: "GET",
    headers: { "User-Agent": "Mozilla/5.0" },
    signal: AbortSignal.timeout(15000),
  });
  if (response.ok === false) throw new Error(`翻訳に失敗しました (HTTP ${response.status})。`);
  const data = await response.json();
  const result = (data?.[0] ?? []).map((segment) => segment?.[0] ?? "").join("");
  if (!result.trim()) throw new Error("翻訳結果が空です。");
  return result;
}

module.exports = { translate, detectSource };
