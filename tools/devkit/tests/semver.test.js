"use strict";

// 元テスト: tools/semver-tool/tests/test_semver_tool.py

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

function assertCli(t, args, { code = 0, output = "", error = false, errorText = null } = {}) {
  const result = runCli(["semver", ...args]);
  assert.equal(result.status, code, `${args.join(" ")}: ${result.stderr}`);
  assert.equal(result.stdout, output ? output + "\n" : "");
  assert.equal(Boolean(result.stderr), error, `${args.join(" ")}: stderr=${result.stderr}`);
  if (errorText !== null) assert.ok(result.stderr.includes(errorText));
}

function assertRanges(t, cases) {
  for (const [version, expression, matches] of cases) {
    assertCli(t, ["satisfies", version, expression], {
      code: matches ? 0 : 1,
      output: matches ? "true" : "false",
    });
  }
}

test("check accepts valid versions", (t) => {
  for (const version of ["0.0.0", "v1.2.3-0.alpha-1+001.BUILD", "1.0.0-01a"]) {
    assertCli(t, ["check", version], { output: "valid" });
  }
});

test("check rejects invalid versions", (t) => {
  for (const version of [
    "01.2.3", "1.2", "1.2.3-01", "1.2.3+a..b",
    "1.2.3-", "1.2.3+", "1.2.3-é", "1.2.3\n", "VV1.2.3",
  ]) {
    assertCli(t, ["check", version], { code: 1, error: true });
  }
});

test("official precedence chain", (t) => {
  const versions = [
    "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
    "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0",
  ];
  for (let i = 0; i + 1 < versions.length; i += 1) {
    assertCli(t, ["compare", versions[i], versions[i + 1]], { output: "-1" });
    assertCli(t, ["compare", versions[i + 1], versions[i]], { output: "1" });
  }
});

test("compare core and build metadata", (t) => {
  assertCli(t, ["compare", "2.0.0", "10.0.0"], { output: "-1" });
  assertCli(t, ["compare", "1.0.0", "1.0.0"], { output: "0" });
  assertCli(t, ["compare", "v1.0.0+001", "1.0.0+abc"], { output: "0" });
});

test("compare prerelease prefix", (t) => {
  assertCli(t, ["compare", "1.0.0-alpha", "1.0.0-alpha.0"], { output: "-1" });
});

test("bump core", (t) => {
  for (const [part, expected] of [["major", "2.0.0"], ["minor", "1.3.0"], ["patch", "1.2.10"]]) {
    assertCli(t, ["bump", "v1.2.9-rc.3+build", "--part", part], { output: expected });
  }
});

test("bump prerelease", (t) => {
  const cases = [
    ["1.2.3", [], "1.2.3-rc.1"],
    ["1.2.3+build", ["--pre-id", "beta"], "1.2.3-beta.1"],
    ["1.2.3-alpha.9", [], "1.2.3-alpha.10"],
    ["1.2.3-alpha", [], "1.2.3-alpha.1"],
    ["1.2.3-9", [], "1.2.3-10"],
  ];
  for (const [version, options, expected] of cases) {
    assertCli(t, ["bump", version, "--part", "pre", ...options], { output: expected });
  }
});

test("large numbers", (t) => {
  const large = "9".repeat(4500);
  const bumped = "1" + "0".repeat(4500);
  assertCli(t, ["bump", `1.2.${large}`, "--part", "patch"], { output: `1.2.${bumped}` });
  assertCli(t, ["compare", `${large}.0.0`, `${bumped}.0.0`], { output: "-1" });
});

test("caret ranges", (t) => {
  assertRanges(t, [
    ["1.9.0", "^1.2.3", true], ["2.0.0", "^1.2.3", false],
    ["0.2.3", "^0.2.3", true], ["0.2.2", "^0.2.3", false],
    ["0.3.0-rc.1", "^0.2.3", true],
    ["0.3.0", "^0.2.3", false], ["0.0.3", "^0.0.3", true],
    ["0.0.4", "^0.0.3", false],
    ["0.0.0", "^0.0.0", true], ["0.0.1", "^0.0.0", false],
  ]);
});

test("partial caret ranges", (t) => {
  assertRanges(t, [
    ["0.9.9", "^1", false], ["1.0.0", "^1", true], ["2.0.0", "^1", false],
    ["1.1.9", "^1.2", false], ["1.2.0", "^1.2", true],
    ["1.9.0", "^1.2", true], ["2.0.0", "^1.2", false],
    ["0.0.0", "^0", true], ["0.9.0", "^0", true], ["1.0.0", "^0", false],
    ["0.1.9", "^0.2", false], ["0.2.0", "^0.2", true], ["0.3.0", "^0.2", false],
    ["0.0.0", "^0.0", true], ["0.0.1", "^0.0", true], ["0.1.0", "^0.0", false],
    ["1.2.0", "^v1.2", true],
  ]);
});

test("tilde ranges", (t) => {
  assertRanges(t, [
    ["1.2.2", "~1.2.3", false], ["1.2.3", "~1.2.3", true],
    ["1.2.9", "~1.2.3", true], ["1.3.0", "~1.2.3", false],
    ["0.2.2", "~0.2.3", false], ["0.2.9", "~0.2.3", true],
    ["0.3.0", "~0.2.3", false], ["0.2.0", "~0.2", true],
    ["0.3.0", "~0.2", false], ["0.9.0", "~0", true],
    ["1.0.0", "~0", false], ["1.2.0", "~1.2", true], ["1.5.0", "~1", true],
  ]);
});

test("wildcard ranges", (t) => {
  assertRanges(t, [
    ["1.2.9", "1.2.x", true], ["1.3.0", "1.2.x", false],
    ["1.0.0", "1.x", true], ["2.0.0", "1.x", false],
    ["0.0.0-rc.1", "*", true], ["9.0.0", "x", true],
  ]);
});

test("exact and comparator ranges", (t) => {
  assertRanges(t, [
    ["v1.2.3+one", "1.2.3", true], ["1.2.3", "v1.2.3+two", true],
    ["1.2.3-rc.1", "1.2.3", false], ["1.2.3", "=1.2.3", true],
    ["1.1.9", ">=1.2.0", false], ["1.2.0", ">=1.2.0", true],
    ["1.0.0", ">1.0.0", false], ["1.0.1", ">1.0.0", true],
    ["1.2.0", ">=1.2.0 <2.0.0", true], ["1.1.9", ">=1.2.0 <2.0.0", false],
    ["2.0.0", ">=1.2.0 <2.0.0", false],
    ["2.0.0-rc.1", ">=1.2.0 <2.0.0", true],
    ["2.0.0", ">1.0.0 <=2.0.0", true], ["1.0.0", ">1.0.0 <=2.0.0", false],
  ]);
});

test("invalid arguments return two", (t) => {
  const cases = [
    [], ["check"], ["compare", "1.2.3", "bad"], ["compare", "bad", "1.2.3"],
    ["bump", "bad", "--part", "major"], ["bump", "1.2.3"],
    ["bump", "1.2.3", "--part", "build"],
    ["bump", "1.2.3", "--part", "pre", "--pre-id", "01"],
    ["satisfies", "bad", "*"],
    ["satisfies", "1.2.3", ">2.0.0 <bad"], ["satisfies", "1.2.3", "~01"],
    ["satisfies", "1.2.3", "1.0.0 || 1.2.3"], ["satisfies", "1.2.3", "1.02.x"],
  ];
  for (const args of cases) {
    assertCli(t, args, { code: 2, error: true });
  }
});

test("empty range reports reason", (t) => {
  assertCli(t, ["satisfies", "1.2.3", ""], {
    code: 2, error: true, errorText: "empty version range",
  });
});
