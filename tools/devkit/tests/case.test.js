"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("case splits acronyms, camel boundaries, and delimiters", () => {
  const result = runCli(["case", "myHTTPServer-test"]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, [
    "snake: my_http_server_test", "camel: myHttpServerTest", "pascal: MyHttpServerTest",
    "kebab: my-http-server-test", "upper-snake: MY_HTTP_SERVER_TEST",
    "lower: myhttpserver-test", "upper: MYHTTPSERVER-TEST", "",
  ].join("\n"));
});

test("case accepts stdin and validates the requested format", () => {
  const result = runCli(["case", "--to", "camel"], { input: "日本語 example\r\n" });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "日本語Example\n");
  assert.equal(runCli(["case", "--to", "title", "text"]).status, 2);
});
