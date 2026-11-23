"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { runCliBuffer, BIN } = require("./helpers");

function tempDirectory(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "devkit-csv-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

function run(t, data, options = [], stdin = false) {
  const directory = tempDirectory(t);
  const file = path.join(directory, "sample.csv");
  if (!stdin) fs.writeFileSync(file, data);
  return capture(directory, stdin ? data : "", process.execPath, [BIN, "csv", stdin ? "-" : file, ...options]);
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

function summary(t, data, options = [], stdin = false) {
  const result = run(t, data, ["--json", ...options], stdin);
  assert.equal(result.status, 0, result.stderr.toString());
  return [JSON.parse(result.stdout), result.stderr.toString()];
}

test("basic JSON", (t) => {
  const file = path.join(tempDirectory(t), "sample.csv");
  fs.writeFileSync(file, "name,score\nAlice,10\nBob,20\n");
  const cli = runCliBuffer(["csv", file, "--json"]);
  assert.equal(cli.status, 0, cli.stderr.toString());
  const result = JSON.parse(cli.stdout);
  assert.equal(result.row_count, 2);
  assert.equal(result.column_count, 2);
  assert.deepEqual(result.headers, ["name", "score"]);
  assert.equal(result.encoding, "utf-8");
  assert.equal(result.delimiter, ",");
  assert.equal(result.limit, null);
  assert.equal(result.uneven_row_count, 0);
  assert.deepEqual(result.sample_rows, [["Alice", "10"], ["Bob", "20"]]);
  assert.deepEqual(result.columns[1], {
    name: "score", type: "integer", empty_count: 0, unique_count: 2, min: 10, max: 20,
  });
});

test("types and counts", (t) => {
  const data = "score,date,name,flag,sparse,mixed\n" +
    "1,2025-01-01,Alice,True, ,word\n" +
    "2.5,2025-01-02,Bob,NO,,1\n" +
    "1,2025-01-03, Alice ,yes,9,\n";
  const [{ columns }] = summary(t, data);
  assert.deepEqual(columns.map((column) => column.type),
    ["decimal", "datetime", "string", "boolean", "integer", "mixed"]);
  assert.deepEqual([columns[0].min, columns[0].max], [1, 2.5]);
  assert.equal(columns[2].unique_count, 2);
  assert.equal(columns[4].empty_count, 2);
  assert.equal(columns[4].unique_count, 1);
});

test("Shift_JIS from file and stdin", async (t) => {
  const data = Buffer.from("6e616d652c636974790a91be98592c938c8b9e0a", "hex");
  for (const stdin of [false, true]) {
    await t.test(`stdin=${stdin}`, (subtest) => {
      const [result] = summary(subtest, data, [], stdin);
      assert.equal(result.encoding, "shift_jis");
      assert.deepEqual(result.sample_rows, [["太郎", "東京"]]);
    });
  }
});

test("UTF-8 BOM and tab inference", (t) => {
  const [result] = summary(t, "\uFEFF名前\t点数\n葵\t8\n");
  assert.equal(result.encoding, "utf-8-sig");
  assert.equal(result.delimiter, "\t");
  assert.deepEqual(result.headers, ["名前", "点数"]);
});

test("CP932 from file and stdin", async (t) => {
  const data = Buffer.from("6e616d652c636974790aeee08bb42c938c8b9e0a", "hex");
  assert.equal(run(t, data, ["--encoding", "shift_jis"]).status, 2);
  for (const stdin of [false, true]) {
    await t.test(`stdin=${stdin}`, (subtest) => {
      const [result] = summary(subtest, data, [], stdin);
      assert.equal(result.encoding, "cp932");
      assert.deepEqual(result.sample_rows, [["髙橋", "東京"]]);
    });
  }
});

test("limit ignores later malformed input", (t) => {
  const data = Buffer.from('a,b\n1,2\n3,4\n"\xff\n', "latin1");
  const [result] = summary(t, data, ["--limit", "2", "--sample", "1"]);
  assert.equal(result.row_count, 2);
  assert.equal(result.limit, 2);
  assert.equal(result.encoding, "utf-8");
  assert.deepEqual(result.sample_rows, [["1", "2"]]);
  const output = run(t, data, ["--limit", "2"]);
  assert.equal(output.status, 0);
  assert.ok(output.stdout.includes("先頭 2 行時点の概算"));
});

test("uneven rows", (t) => {
  const [result, stderr] = summary(t, "a,b\n1\n2,3,4\n5,6\n");
  assert.ok(stderr.includes("列数が揃っていない行: 2 件"));
  assert.equal(result.uneven_row_count, 2);
  assert.deepEqual(result.headers, ["a", "b", "col3"]);
  assert.deepEqual(result.sample_rows, [["1", "", ""], ["2", "3", "4"], ["5", "6", ""]]);
  assert.deepEqual(result.columns.map((column) => column.empty_count), [0, 1, 2]);
});

test("explicit options without header", (t) => {
  const data = Buffer.from('\uFEFF"青\n赤"|1\n白|0\n', "utf16le");
  const [result] = summary(t, data, ["--delimiter", "|", "--encoding", "utf-16", "--no-header"], true);
  assert.deepEqual(result.headers, ["col1", "col2"]);
  assert.equal(result.row_count, 2);
  assert.deepEqual(result.sample_rows, [["青\n赤", "1"], ["白", "0"]]);
});

test("input errors", async (t) => {
  const cases = [
    ["malformed encoding", Buffer.from("a,b\n\x81,1\n", "latin1")],
    ["unclosed quote", Buffer.from('a,b\n"open,1\n')],
  ];
  for (const [name, data] of cases) {
    await t.test(name, (subtest) => {
      const result = run(subtest, data);
      assert.equal(result.status, 2);
      assert.ok(result.stderr.includes("エラー"));
      assert.deepEqual(result.stdout, Buffer.alloc(0));
    });
  }
});

test("missing file", (t) => {
  const directory = tempDirectory(t);
  const result = capture(directory, "", process.execPath, [BIN, "csv", path.join(directory, "missing.csv")]);
  assert.equal(result.status, 2);
  assert.ok(result.stderr.includes("エラー"));
  assert.ok(result.stderr.includes("missing.csv"));
  assert.deepEqual(result.stdout, Buffer.alloc(0));
});

test("output encoding error", async (t) => {
  for (const options of [[], ["--json"]]) {
    // Node writes UTF-8 and has no equivalent of Python's PYTHONIOENCODING.
    await t.test(options.length ? "JSON" : "text", { skip: "PYTHONIOENCODING is Python-specific" }, () => {
      const result = runCliBuffer(["csv", "-", ...options], {
        input: Buffer.from("名前\n髙橋\n"), env: { PYTHONIOENCODING: "ascii" },
      });
      assert.equal(result.status, 2);
      assert.ok(result.stderr.includes("ascii"));
      assert.ok(!result.stderr.includes("Traceback"));
    });
  }
});

test("broken output pipe", (t) => {
  // Wait for the reader to exit before launching the CLI with the pipe as stdout.
  const result = capture(tempDirectory(t), "name\nAlice\n", "bash",
    ["-c", 'exec 3> >(exit)\nwait $!\nexec "$@" >&3', "bash", process.execPath, BIN, "csv", "-", "--json"]);
  assert.equal(result.status, 2);
  assert.ok(result.stderr.includes("出力できません"));
  assert.ok(!result.stderr.includes("Traceback"));
});
