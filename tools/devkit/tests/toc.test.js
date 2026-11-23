"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { runCliBuffer, BIN } = require("./helpers");

function fixture(t, source) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "devkit-toc-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, "input.md");
  if (source !== undefined) fs.writeFileSync(file, source);
  const run = (...args) => capture(directory, "", process.execPath, [BIN, "toc", file, ...args]);
  run.file = file;
  run.read = () => fs.readFileSync(file);
  return run;
}

function stdin(source, ...args) {
  // Inherit a file on fd 0 so stdin reaches EOF even in restricted sandboxes.
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "devkit-toc-stdin-"));
  try {
    return capture(directory, source, process.execPath, [BIN, "toc", "-", ...args]);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

function capture(directory, input, command, args) {
  const files = ["stdin", "stdout", "stderr"].map((name) => path.join(directory, name));
  fs.writeFileSync(files[0], input);
  const fds = files.map((file, index) => fs.openSync(file, index === 0 ? "r" : "w"));
  let result;
  try {
    result = spawnSync(command, args, { stdio: fds });
  } finally {
    fds.forEach((fd) => fs.closeSync(fd));
  }
  return { ...result, stdout: fs.readFileSync(files[1]), stderr: fs.readFileSync(files[2]) };
}

test("generate and update", (t) => {
  const source = "# Title\n\n  <!-- toc --> \nold\n <!-- /toc -->\n\n## One\n### Two ###\n";
  const run = fixture(t, source);
  assert.equal(runCliBuffer(["toc", run.file]).status, 0);
  assert.equal(run.read().toString(),
    source.replace("old\n", "- [One](#one)\n  - [Two](#two)\n"));
});

test("ignore fences and comments", () => {
  const source = "````md\n```\n## Hidden\n````\n" +
    "~~~\n<!-- toc -->\n## Hidden\n~~~\n" +
    "<!--\n## Hidden\n<!-- toc -->\n" +
    "<!-- toc -->\n<!-- /toc -->\n## Shown <!-- note -->\n";
  const result = stdin(source);
  assert.equal(result.status, 0, result.stderr.toString());
  assert.ok(result.stdout.includes("- [Shown](#shown)"));
  assert.ok(!result.stdout.includes("- [Hidden]"));
});

test("close marker inside fence in TOC region", () => {
  const source = ["# Title", "", "<!-- toc -->", "```", "<!-- /toc -->", "```",
    "<!-- /toc -->", "", "## Shown", ""].join("\n");
  const expected = ["# Title", "", "<!-- toc -->", "- [Shown](#shown)",
    "<!-- /toc -->", "", "## Shown", ""].join("\n");
  const result = stdin(source);
  assert.equal(result.status, 0, result.stderr.toString());
  assert.equal(result.stdout.toString(), expected);
});

test("duplicate and Japanese anchors", () => {
  const source = "<!-- toc -->\n<!-- /toc -->\n" + "## 日本語 Test_!?\n".repeat(3);
  const result = stdin(source);
  assert.equal(result.status, 0);
  const expected = ["", "-1", "-2"].map((suffix) => `- [日本語 Test_!?](#日本語-test_${suffix})\n`).join("");
  assert.equal(result.stdout.toString(), source.replace("<!-- /toc -->", expected + "<!-- /toc -->"));
});

test("level limits and stdout", (t) => {
  const source = "<!-- toc -->\n<!-- /toc -->\n## Same\n### Same\n#### Four\n##### Five\n";
  const run = fixture(t, source);
  const result = run("--min-level", "3", "--max-level", "4", "--stdout");
  assert.equal(result.status, 0);
  assert.deepEqual(result.stdout, Buffer.from(
    source.replace("<!-- /toc -->", "- [Same](#same-1)\n  - [Four](#four)\n<!-- /toc -->")));
  assert.deepEqual(run.read(), Buffer.from(source));
});

test("check", (t) => {
  const source = Buffer.from("<!-- toc -->\n<!-- /toc -->\n## One\n");
  const run = fixture(t, source);
  const stale = run("--check");
  assert.equal(stale.status, 1);
  assert.ok(stale.stderr.includes("out of date"));
  assert.deepEqual(run.read(), source);
  assert.equal(run().status, 0);
  assert.equal(run("--check").status, 0);
});

test("missing markers and create", (t) => {
  const source = Buffer.from("# Title\n## One\n");
  const run = fixture(t, source);
  const failure = run();
  assert.equal(failure.status, 2);
  assert.ok(failure.stderr.includes("markers are missing"));
  assert.equal(run("--check").status, 1);
  assert.deepEqual(run.read(), source);
  assert.equal(run("--create").status, 0);
  assert.deepEqual(run.read(), Buffer.from("# Title\n\n<!-- toc -->\n- [One](#one)\n<!-- /toc -->\n\n## One\n"));
  assert.equal(run("--check").status, 0);
});

test("create at end of stream", () => {
  const result = stdin("## Heading", "--create");
  assert.equal(result.status, 0);
  assert.deepEqual(result.stdout, Buffer.from("## Heading\n\n<!-- toc -->\n- [Heading](#heading)\n<!-- /toc -->\n"));
});

test("CRLF and BOM", (t) => {
  const source = "\uFEFF<!-- toc -->\r\nold\r\n<!-- /toc -->\r\n## One";
  const run = fixture(t, source);
  assert.equal(run().status, 0);
  assert.deepEqual(run.read(), Buffer.from(source.replace("old", "- [One](#one)")));
});

test("errors", async (t) => {
  const cases = [
    ["<!-- toc -->\n", [], "closing marker"],
    ["<!-- /toc -->\n", ["--create"], "opening marker"],
    ["Plain text", ["--create"], "no ATX heading"],
    [Buffer.from([255]), [], "utf-8"],
    ["", ["--min-level", "5", "--max-level", "2"], "must not exceed"],
  ];
  for (const [source, args, message] of cases) {
    await t.test(message, () => {
      const result = stdin(source, ...args);
      assert.equal(result.status, 2);
      assert.ok(result.stderr.includes(message), result.stderr.toString());
    });
  }
  assert.equal(fixture(t)().status, 2);
});
