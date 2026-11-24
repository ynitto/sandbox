"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { runCli } = require("./helpers");

const cli = (...args) => runCli(["url", ...args]);
function output(...args) {
  const result = cli(...args);
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
}
const json = (...args) => JSON.parse(output("--json", ...args));

test("components, userinfo and default port", () => {
  const url = "https://user:pass@Example.com/path#section";
  assert.ok(output(url).includes("port: 443 (既定)"));
  assert.deepEqual(json(url).components, {
    scheme: "https", username: "user", password: "pass",
    host: "example.com", port: 443, port_is_default: true,
    path: "/path", fragment: "section",
  });
});

test("IPv6 and explicit port", () => {
  const { components } = json("http://[::1]:8080/");
  assert.equal(components.host, "::1");
  assert.equal(components.port, 8080);
  assert.equal(components.port_is_default, false);
  assert.equal(components.username, null);
  assert.equal(components.password, null);
});

test("escaped userinfo and rebuilt URL spelling", () => {
  const text = output("--add", "a=1", "HTTPS://user%2Fname@Example.com/path#section");
  assert.ok(text.includes("username: user%2Fname\n"));
  assert.ok(text.includes("host: example.com\n"));
  assert.ok(text.includes("rebuilt: HTTPS://user%2Fname@Example.com/path?a=1#section\n"));
});

for (const [url, host, port, isDefault] of [
  ["ws://example.com/", "example.com", 80, true],
  ["wss://example.com/", "example.com", 443, true],
  ["ftp://example.com/", "example.com", 21, true],
  ["http://[::1]/", "::1", 80, true],
  ["http://[::1]:80/", "::1", 80, false],
]) {
  test("port defaults for " + url, () => {
    const { components } = json(url);
    assert.equal(components.host, host);
    assert.equal(components.port, port);
    assert.equal(components.port_is_default, isDefault);
  });
}

test("missing port without default", () => {
  const url = "ssh://example.com/";
  assert.ok(!output(url).includes("port:"));
  assert.equal(json(url).components.port, null);
  assert.equal(json(url).components.port_is_default, false);
});

test("ordered query decoding", () => {
  const url = "https://example.com/?%61=x+y&a=x%20z&flag&empty=";
  assert.deepEqual(json(url).params, [
    { key: "a", value: "x y" }, { key: "a", value: "x z" },
    { key: "flag", value: null }, { key: "empty", value: "" },
  ]);
  const text = output(url);
  assert.ok(text.includes("'flag' (値なし)"));
  assert.ok(text.includes("'empty' = ''"));
});

test("edits", () => {
  const url = "https://example.com/?a=1&b=2&a=3&b=4&flag&empty=#top";
  const options = ["--add", "a=last", "--set", "a=new value", "--set", "b=/", "--set", "c=é", "--drop", "a"];
  const expected = "https://example.com/?b=%2F&flag&empty=&a=new+value&c=%C3%A9&a=last#top";
  assert.ok(output(...options, url).includes("rebuilt: " + expected + "\n"));
  assert.equal(json(...options, url).rebuilt, expected);
});

test("string modes", () => {
  assert.equal(output("--decode", "a+b%20c%2F"), "a+b c/\n");
  assert.equal(output("--encode", "a/b c+"), "a%2Fb%20c%2B\n");
  assert.equal(output("--encode", "a/b c", "--safe", "/"), "a/b%20c\n");
});

test("drop decodes key", () => {
  const url = "https://example.com/?a+b=1&a%20b=2&a%2Bb=3";
  assert.equal(json("--drop", "a+b", url).rebuilt, "https://example.com/?a%2Bb=3");
});

test("set and add decode keys and preserve values", () => {
  const url = "https://example.com/?a+b=1&keep=2";
  const data = json("--set", "a%20b=x+y%2F", "--set", "new+key=3", "--add", "c%2Bd=4", url);
  assert.equal(data.rebuilt, "https://example.com/?a+b=x%2By%252F&keep=2&new+key=3&c%2Bd=4");
});

test("decode invalid percent escape", () => {
  const result = cli("--decode", "a+b%20c%");
  assert.equal(result.status, 0);
  assert.equal(result.stdout, "a+b%20c%\n");
  assert.ok(result.stderr.includes("warning:"));
});

test("bad percent encoding", () => {
  const result = cli("--json", "https://example.com/?a=%GG%20&b=%FF");
  assert.equal(result.status, 0);
  assert.deepEqual(JSON.parse(result.stdout).params.map(({ value }) => value), ["%GG%20", "%FF"]);
  assert.equal((result.stderr.match(/warning:/g) || []).length, 2);
});

for (const url of ["example.com:8080/path", "//example.com:8080/path"]) {
  test("fix scheme for " + url, () => {
    assert.ok(output("--fix-scheme", url).includes("scheme: https"));
    assert.ok(output("--fix-scheme", url).includes("port: 8080"));
  });
}

for (const args of [
  ["example.com/path"], ["https:///path"], ["http://[::1"], ["http://::1/"], ["--safe", "/"],
  ["http://example.com:65536"], ["http://example.com:no"], ["https://example.com", "--set", "key"],
  ["--decode", "x", "http://x"],
  ["--decode", "x", "--encode", "y"], ["--encode", "--decode"],
]) {
  test("invalid URL arguments " + args.join(" "), () => {
    const result = cli(...args);
    assert.equal(result.status, 2);
    assert.ok(result.stderr.includes("error:"), result.stderr);
  });
}
