"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("color normalizes all supported notations", () => {
  for (const [input, hex] of [
    ["#f00", "#ff0000"], ["#FF0000", "#ff0000"], ["#ff000080", "#ff000080"],
    ["rgb(255,0,0)", "#ff0000"], ["rgba(255,0,0,0.5)", "#ff000080"],
    ["hsl(0,100%,50%)", "#ff0000"], ["hsla(0,100%,50%,0.5)", "#ff000080"],
    ["hsv(360,100%,100%)", "#ff0000"],
  ]) {
    const result = runCli(["color", "--json", input]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).hex, hex);
  }
});

test("color clamps lightness and reports mix and WCAG grades", () => {
  const result = runCli(["color", "#f00", "--lighten", "100", "--mix", "#000", "--contrast", "#000", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.hex, "#ffffff");
  assert.equal(output.mix.hex, "#808080");
  assert.deepEqual(output.contrast, { ratio: 21, normal: { AA: "pass", AAA: "pass" }, large: { AA: "pass", AAA: "pass" } });
  assert.match(runCli(["color", "#777", "--contrast", "#fff"]).stdout, /通常テキスト: AA fail, AAA fail/);
  assert.match(runCli(["color", "#f00", "--darken", "100"]).stdout, /#000000/);
  for (const input of ["#abcd", "rgb(256,0,0)", "hsl(0,50,50%)", "rgba(0,0,0,2)"]) {
    assert.equal(runCli(["color", input]).status, 2);
  }
  assert.equal(runCli(["color", "#f00", "--lighten", "101"]).status, 2);
});
