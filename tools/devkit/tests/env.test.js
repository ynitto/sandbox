"use strict";

// 元テスト: tools/env-diff/tests/test_env_diff.py

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { runCli } = require("./helpers");

function setup(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "devkit-env-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const a = path.join(dir, ".env.example");
  const b = path.join(dir, ".env");
  const run = (aText, bText, ...options) => {
    fs.writeFileSync(a, aText, "utf8");
    if (bText !== null) fs.writeFileSync(b, bText, "utf8");
    return runCli(["env", ...options, a, b]);
  };
  return { a, b, run };
}

test("keys present in only one file", (t) => {
  const { run } = setup(t);
  const result = run("ONLY_A=private-a\n", "ONLY_B=private-b\n");
  assert.equal(result.status, 1);
  assert.ok(result.stdout.includes("A にだけあるキー\n  ONLY_A"));
  assert.ok(result.stdout.includes("B にだけあるキー\n  ONLY_B"));
  for (const secret of ["private-a", "private-b"]) {
    assert.ok(!result.stdout.includes(secret));
  }
});

test("different values are hidden by default", (t) => {
  const { run } = setup(t);
  const result = run("TOKEN=first-secret\n", "TOKEN=second-secret\n");
  assert.equal(result.status, 1);
  assert.ok(result.stdout.includes("値が違うキー\n  TOKEN"));
  for (const secret of ["first-secret", "second-secret"]) {
    assert.ok(!(result.stdout + result.stderr).includes(secret));
  }
});

test("show values", (t) => {
  const { run } = setup(t);
  const result = run("TOKEN=first-secret\n", "TOKEN=second-secret\n", "--show-values");
  assert.equal(result.status, 1);
  assert.ok(result.stdout.includes("A = 'first-secret'"));
  assert.ok(result.stdout.includes("B = 'second-secret'"));
});

test("duplicates only use last definition in comparison", (t) => {
  const { run } = setup(t);
  const a = "K=first-secret\n# comment\nexport K=shared-secret\n";
  const b = "K=second-secret\nK=shared-secret\n";
  const result = run(a, b);
  assert.equal(result.status, 1);
  assert.ok(result.stdout.includes("A の重複キー\n  K (1, 3 行目)"));
  assert.ok(result.stdout.includes("B の重複キー\n  K (1, 2 行目)"));
  assert.ok(!result.stdout.includes("値が違うキー"));
  for (const secret of ["first-secret", "second-secret", "shared-secret"]) {
    assert.ok(!result.stdout.includes(secret));
  }
});

test("equivalent files", (t) => {
  const { run } = setup(t);
  const a = "  # comment\n\n export TOKEN = 'a=b'  \n EMPTY=  \n";
  const b = 'TOKEN="a=b"\nEMPTY=""\n';
  const result = run(a, b);
  assert.equal(result.status, 0);
  assert.equal(result.stdout.trim(), "差分なし");
});

test("empty values in text", (t) => {
  const { run } = setup(t);
  const result = run("ONLY_A=\nTOKEN=\n", "TOKEN=hidden-secret\n");
  assert.equal(result.status, 1);
  assert.ok(result.stdout.includes("ONLY_A (空)"));
  assert.ok(result.stdout.includes("TOKEN [A (空)]"));
  assert.ok(!result.stdout.includes("hidden-secret"));
});

test("json values are opt-in", (t) => {
  const { run } = setup(t);
  const a = "ONLY_A=\nTOKEN=discarded-secret\nTOKEN=first-secret\n";
  const b = "ONLY_B=other-secret\nTOKEN=\n";
  const result = run(a, b, "--json");
  assert.equal(result.status, 1);
  assert.deepEqual(JSON.parse(result.stdout), {
    only_in_a: [{ key: "ONLY_A", empty: true }],
    only_in_b: [{ key: "ONLY_B", empty: false }],
    different: [{ key: "TOKEN", a: { empty: false }, b: { empty: true } }],
    duplicates_a: [{ key: "TOKEN", lines: [2, 3] }],
    duplicates_b: [],
  });
  const shown = run(a, b, "--json", "--show-values");
  const data = JSON.parse(shown.stdout);
  assert.equal(data.only_in_a[0].value, "");
  assert.equal(data.only_in_b[0].value, "other-secret");
  assert.equal(data.different[0].a.value, "first-secret");
  assert.equal(data.different[0].b.value, "");
});

test("missing file", (t) => {
  const { b, run } = setup(t);
  const result = run("TOKEN=present\n", null);
  assert.equal(result.status, 2);
  assert.equal(result.stdout, "");
  assert.ok(result.stderr.includes(b));
});

test("invalid line does not echo input", (t) => {
  const { run } = setup(t);
  const result = run("# comment\nraw-secret\n", "", "--json");
  assert.equal(result.status, 2);
  assert.equal(result.stdout, "");
  assert.ok(result.stderr.includes("2 行目"));
  assert.ok(!result.stderr.includes("raw-secret"));
});

test("missing arguments", () => {
  const result = runCli(["env"]);
  assert.equal(result.status, 2);
  assert.ok(result.stderr.includes("ファイルA"));
});
