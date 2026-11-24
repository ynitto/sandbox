"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { runCli } = require("./helpers");

const cli = (...args) => runCli(["ip", ...args]);
function readInfo(cidr) {
  const result = cli("info", cidr, "--json");
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");
  const exact = result.stdout.replace(/("(?:total_addresses|usable_host_count)": )([0-9]+)/g,
    (_, key, value) => key + JSON.stringify(value));
  return JSON.parse(exact, (key, value) => {
    if (key !== "total_addresses" && key !== "usable_host_count") return value;
    assert.equal(typeof value, "string");
    const count = BigInt(value);
    return count > Number.MAX_SAFE_INTEGER ? count : Number(count);
  });
}

test("IPv4 JSON info", () => {
  assert.deepEqual(readInfo("192.168.1.10/24"), {
    version: 4,
    network_address: "192.168.1.0",
    broadcast_address: "192.168.1.255",
    netmask: "255.255.255.0",
    prefix_length: 24,
    total_addresses: 256,
    first_usable_host: "192.168.1.1",
    last_usable_host: "192.168.1.254",
    usable_host_count: 254,
    wildcard_mask: "0.0.0.255",
    is_private: true,
    is_loopback: false,
    is_link_local: false,
    is_multicast: false,
    is_global: false,
  });
});

for (const [prefix, count, first] of [[31, 2, "192.0.2.0"], [32, 1, "192.0.2.1"]]) {
  test("small IPv4 network /" + prefix, () => {
    const data = readInfo("192.0.2.1/" + prefix);
    assert.equal(data.total_addresses, count);
    assert.equal(data.usable_host_count, count);
    assert.equal(data.first_usable_host, first);
    assert.equal(data.last_usable_host, "192.0.2.1");
  });
}

for (const [cidr, count, first, last] of [
  ["192.0.2.0/30", 2, "192.0.2.1", "192.0.2.2"],
  ["0.0.0.0/0", 2 ** 32 - 2, "0.0.0.1", "255.255.255.254"],
]) {
  test("IPv4 usable endpoints for " + cidr, () => {
    const data = readInfo(cidr);
    assert.equal(data.usable_host_count, count);
    assert.equal(data.first_usable_host, first);
    assert.equal(data.last_usable_host, last);
  });
}

test("IPv6 info", () => {
  const data = readInfo("2001:db8::/64");
  assert.equal(data.version, 6);
  assert.equal(data.network_address, "2001:db8::");
  assert.equal(data.prefix_length, 64);
  assert.equal(data.broadcast_address, null);
  assert.equal(data.netmask, null);
  assert.equal(data.wildcard_mask, null);
  assert.equal(data.total_addresses, 2n ** 64n);
  assert.equal(data.usable_host_count, 2n ** 64n);
  assert.equal(data.first_usable_host, "2001:db8::");
  assert.equal(data.last_usable_host, "2001:db8::ffff:ffff:ffff:ffff");
  const result = cli("info", "2001:db8::/64");
  assert.equal(result.status, 0, result.stderr);
  assert.ok(result.stdout.includes("ブロードキャストアドレス: なし"));
});

for (const [cidr, ip, code, output] of [
  ["192.168.1.10/24", "192.168.1.255", 0, "含まれる"],
  ["192.168.1.10/24", "192.168.2.0", 1, "含まれない"],
  ["192.168.1.0/24", "192.168.1.0", 0, "含まれる"],
  ["2001:db8::/64", "2001:db8::1", 0, "含まれる"],
  ["2001:db8::/64", "2001:db8:0:1::1", 1, "含まれない"],
]) {
  test("contains " + cidr + " " + ip, () => {
    const result = cli("contains", cidr, ip);
    assert.equal(result.status, code, result.stderr);
    assert.equal(result.stdout.trim(), output);
  });
}

for (const [first, second, code, output] of [
  ["192.168.1.0/24", "192.168.1.129/25", 0, "重なる: 192.168.1.128/25 (192.168.1.128 ～ 192.168.1.255)"],
  ["192.168.1.129/25", "192.168.1.0/24", 0, "重なる: 192.168.1.128/25 (192.168.1.128 ～ 192.168.1.255)"],
  ["2001:db8::/64", "2001:db8::/64", 0, "重なる: 2001:db8::/64 (2001:db8:: ～ 2001:db8::ffff:ffff:ffff:ffff)"],
  ["192.168.1.0/24", "192.168.2.0/24", 1, "重ならない"],
  ["2001:db8::/64", "2001:db8:0:1::/64", 1, "重ならない"],
]) {
  test("overlap " + first + " " + second, () => {
    const result = cli("overlap", first, second);
    assert.equal(result.status, code, result.stderr);
    assert.equal(result.stdout.trim(), output);
  });
}

for (const [args, reason] of [
  [["info", "192.0.2.1"], "無効な CIDR"],
  [["info", "192.0.2.1/33"], "無効な CIDR"],
  [["contains", "192.0.2.0/24", "invalid"], "無効な IP アドレス"],
  [["overlap", "192.0.2.0/24", "invalid/24"], "無効な CIDR"],
  [["contains", "192.0.2.0/24", "::1"], "IP バージョンが異なります"],
  [["overlap", "::/0", "192.0.2.0/24"], "IP バージョンが異なります"],
]) {
  test("invalid IP arguments " + args.join(" "), () => {
    const result = cli(...args);
    assert.equal(result.status, 2);
    assert.equal(result.stdout, "");
    assert.ok(result.stderr.includes(reason), result.stderr);
  });
}
