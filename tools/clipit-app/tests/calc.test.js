"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { evaluate } = require("../src/calc");

test("arithmetic precedence, percentages, decimals and unary signs", () => {
  const cases = [
    ["1+2*3", 7],
    ["(12 + 8) × 50%", 10],
    ["50%", 0.5],
    ["16 ÷ 4 / 2", 2],
    [".25 + 1.5", 1.75],
    ["−(2 + 3) + +1", -4],
    ["1 - -2", 3],
  ];
  for (const [input, expected] of cases) assert.equal(evaluate(input), expected, input);
});

test("invalid expressions report the reason", () => {
  const cases = [
    ["1+a", /使用できない文字/],
    ["(1+2", /括弧が対応/],
    ["1+2)", /括弧が対応/],
    ["1/0", /計算できない式/],
    ["1+ ", /計算できない式/],
    ["1 2", /計算できない式/],
    ["2**3", /使用できない演算子/],
    ["50%%", /% の前/],
    ["9".repeat(309), /計算できない式/],
    ["", /1～500文字/],
    ["1".repeat(501), /1～500文字/],
  ];
  for (const [input, expected] of cases) assert.throws(() => evaluate(input), expected, input);
});
