"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { translate, detectSource } = require("../src/translate");

test("requests the correct directions and joins translated segments", async () => {
  for (const [input, source, target, segments, expected] of [
    ["こんにちは + 世界", "ja", "en", [["Hello "], null, ["world"]], "Hello world"],
    ["Hello & goodbye", "en", "ja", [["こんにちは。"], ["さようなら。"]], "こんにちは。さようなら。"],
  ]) {
    const result = await translate(input, async (url, options) => {
      const endpoint = new URL(url);
      assert.equal(endpoint.origin + endpoint.pathname, "https://translate.googleapis.com/translate_a/single");
      assert.deepEqual(Object.fromEntries(endpoint.searchParams), {
        client: "gtx", sl: source, tl: target, dt: "t", q: input,
      });
      assert.equal(options.method, "GET");
      assert.equal(options.headers["User-Agent"], "Mozilla/5.0");
      return { json: async () => [segments] };
    });
    assert.equal(detectSource(input), source);
    assert.equal(result, expected);
  }
});

test("empty and whitespace-only translation results fail", async () => {
  for (const data of [[[]], [[null, [""], ["  "]]]]) {
    await assert.rejects(translate("text", async () => ({ json: async () => data })), /翻訳結果が空/);
  }
});
