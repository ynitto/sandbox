"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("table renders Markdown from quoted CSV and pads short rows", () => {
  const result = runCli(["table"], { input: 'name,note\r\nAlice,"a,b ""quoted"""\r\nBob\r\nEve,a|b\r\n' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, '| name | note |\n| --- | --- |\n| Alice | a,b "quoted" |\n| Bob |  |\n| Eve | a\\|b |\n');
});

test("table aligns fullwidth TSV cells in ASCII and rejects empty or broken CSV", () => {
  const result = runCli(["table", "--format", "ascii"], { input: "名\tID\n東京\t1\nA\t42\n" });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "+------+----+\n| 名   | ID |\n+------+----+\n| 東京 | 1  |\n| A    | 42 |\n+------+----+\n");
  assert.equal(runCli(["table"]).status, 2);
  assert.equal(runCli(["table"], { input: 'a,"missing' }).status, 2);
});

test("table ignores tabs inside quoted CSV fields when detecting the delimiter", () => {
  const result = runCli(["table"], { input: "\"a\tb\",c\n1,2\n" });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "| a\tb | c |\n| --- | --- |\n| 1 | 2 |\n");
});
