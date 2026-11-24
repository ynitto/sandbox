"use strict";

const { ArgParser, ArgTypeError } = require("../core/args");
const { pyRepr, pyJsonStringify } = require("../core/py");

const help = "IPv4 と IPv6 のサブネットを計算する CLI。";
const usage = "devkit ip {info,contains,overlap} ...";
const labels = {
  version: "IP バージョン",
  network_address: "ネットワークアドレス",
  broadcast_address: "ブロードキャストアドレス",
  netmask: "ネットマスク",
  prefix_length: "プレフィックス長",
  total_addresses: "アドレス総数",
  first_usable_host: "最初の使用可能ホスト",
  last_usable_host: "最後の使用可能ホスト",
  usable_host_count: "使用可能ホスト数",
  wildcard_mask: "ワイルドカードマスク",
  is_private: "プライベート",
  is_loopback: "ループバック",
  is_link_local: "リンクローカル",
  is_multicast: "マルチキャスト",
  is_global: "グローバル",
};

function ipv4(value) {
  const octets = value.split(".");
  if (octets.length !== 4 || octets.some((s) => !/^(?:0|[1-9][0-9]{0,2})$/.test(s) || Number(s) > 255)) {
    throw new Error("invalid IPv4");
  }
  return octets.reduce((result, octet) => (result << 8n) + BigInt(octet), 0n);
}

function address(value) {
  if (!value.includes(":")) return { version: 4, number: ipv4(value) };
  let [literal, scope, ...extra] = value.split("%");
  if (scope === "" || extra.length || scope?.includes("/")) throw new Error("invalid scope");
  if (literal.includes(".")) {
    const lastColon = literal.lastIndexOf(":");
    const tail = ipv4(literal.slice(lastColon + 1));
    literal = literal.slice(0, lastColon + 1) + (tail >> 16n).toString(16) + ":" + (tail & 65535n).toString(16);
  }
  const [left, right, ...extraRuns] = literal.split("::");
  const before = left === "" ? [] : left.split(":");
  const after = right ? right.split(":") : [];
  const missing = 8 - before.length - after.length;
  if (extraRuns.length || (right === undefined ? missing !== 0 : missing < 1)) {
    throw new Error("invalid IPv6 length");
  }
  const words = [...before, ...Array(missing).fill("0"), ...after];
  if (words.some((word) => !/^[0-9a-f]{1,4}$/i.test(word))) throw new Error("invalid IPv6");
  return { version: 6, number: words.reduce((result, word) => (result << 16n) + BigInt("0x" + word), 0n), scope };
}

function format(number, version) {
  const bits = version === 4 ? 8n : 16n;
  const words = [];
  for (let index = 0; index < (version === 4 ? 4 : 8); index++) {
    words.unshift((number & ((1n << bits) - 1n)).toString(version === 4 ? 10 : 16));
    number >>= bits;
  }
  if (version === 4) return words.join(".");
  let longest = 1;
  let start = 0;
  let run = 0;
  for (let index = 0; index < words.length; index++) {
    run = words[index] === "0" ? run + 1 : 0;
    if (run > longest) {
      longest = run;
      start = index - run + 1;
    }
  }
  if (longest === 1) return words.join(":");
  return words.slice(0, start).join(":") + "::" + words.slice(start + longest).join(":");
}

function network(value) {
  const slash = value.indexOf("/");
  const suffix = value.slice(slash + 1);
  const reason = `無効な CIDR: ${pyRepr(value)}`;
  if (slash === -1 || !/^[0-9]+$/.test(suffix)) {
    throw new ArgTypeError(reason + "（IP アドレス/プレフィックス長を指定してください）");
  }
  try {
    const { number, version, scope } = address(value.slice(0, slash));
    const bits = version === 4 ? 32 : 128;
    const prefix = Number(suffix);
    if (prefix > bits) throw new Error("invalid prefix");
    const total = 1n << BigInt(bits - prefix);
    const first = number & -total;
    return { version, prefix, total, first, last: first + total - 1n, scope: first === number ? scope : undefined };
  } catch {
    throw new ArgTypeError(reason);
  }
}

const ranges = {
  4: {
    private: "0.0.0.0/8 10.0.0.0/8 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/29 192.0.0.170/31 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 240.0.0.0/4 255.255.255.255/32",
    loopback: "127.0.0.0/8",
    link_local: "169.254.0.0/16",
    multicast: "224.0.0.0/4",
    shared: "100.64.0.0/10",
  },
  6: {
    private: "::/128 ::1/128 ::ffff:0:0/96 100::/64 2001::/23 2001:2::/48 2001:db8::/32 2001:10::/28 fc00::/7 fe80::/10",
    loopback: "::1/128",
    link_local: "fe80::/10",
    multicast: "ff00::/8",
    shared: "",
  },
};
for (const table of Object.values(ranges)) {
  for (const [key, list] of Object.entries(table)) table[key] = list.split(" ").filter(Boolean).map(network);
}

function networkName(net) {
  return format(net.first, net.version) + (net.scope === undefined ? "" : "%" + net.scope);
}

function info(net) {
  const { version, first, last, prefix, total } = net;
  const ipv4 = version === 4;
  const exclude = ipv4 && prefix < 31 ? 1n : 0n;
  const within = (key) => ranges[version][key].some((range) => range.first <= first && last <= range.last);
  const isPrivate = within("private");
  return {
    version,
    network_address: networkName(net),
    broadcast_address: ipv4 ? format(last, version) : null,
    netmask: ipv4 ? format((1n << 32n) - total, version) : null,
    prefix_length: prefix,
    total_addresses: total,
    first_usable_host: format(first + exclude, version),
    last_usable_host: format(last - exclude, version),
    usable_host_count: total - 2n * exclude,
    wildcard_mask: ipv4 ? format(total - 1n, version) : null,
    is_private: isPrivate,
    is_loopback: within("loopback"),
    is_link_local: within("link_local"),
    is_multicast: within("multicast"),
    is_global: !isPrivate && !within("shared"),
  };
}

function parseAddress(value) {
  try {
    return address(value);
  } catch {
    throw new ArgTypeError(`無効な IP アドレス: ${pyRepr(value)}`);
  }
}

function run(argv, io) {
  const root = new ArgParser({ prog: "devkit ip", usage, description: help });
  root.addPositional("command", { metavar: "{info,contains,overlap}" });
  const [name, ...rest] = argv;
  if (!["info", "contains", "overlap"].includes(name)) {
    root.parse(argv.slice(0, 1));
    root.fail(`argument command: invalid choice: ${pyRepr(name)} (choose from 'info', 'contains', 'overlap')`);
  }
  const suffix = { info: "CIDR [--json]", contains: "CIDR IP", overlap: "CIDR1 CIDR2" };
  const prog = `devkit ip ${name}`;
  const parser = new ArgParser({ prog, usage: `${prog} ${suffix[name]}` });
  parser.addPositional("cidr", { metavar: name === "overlap" ? "CIDR1" : "CIDR", type: network });
  if (name === "info") parser.addFlag("--json", { key: "json", help: "JSON 形式で出力" });
  if (name === "contains") parser.addPositional("ip", { metavar: "IP", type: parseAddress });
  if (name === "overlap") parser.addPositional("other", { metavar: "CIDR2", type: network });
  const { cidr, ip, other, json } = parser.parse(rest);
  if (name === "info") {
    const result = info(cidr);
    if (json) io.out(pyJsonStringify(result, 2) + "\n");
    else {
      for (const [key, label] of Object.entries(labels)) {
        const value = result[key];
        if (value === null && key !== "broadcast_address") continue;
        const display = value === null ? "なし" : typeof value === "boolean" ? (value ? "True" : "False") : value;
        io.out(`${label}: ${display}\n`);
      }
    }
    return 0;
  }
  if (cidr.version !== (ip ?? other).version) {
    root.fail("IPv4 と IPv6 は比較できません（IP バージョンが異なります）");
  }
  if (ip) {
    const included = cidr.first <= ip.number && ip.number <= cidr.last;
    io.out(included ? "含まれる\n" : "含まれない\n");
    return included ? 0 : 1;
  }
  if (cidr.last < other.first || other.last < cidr.first) {
    io.out("重ならない\n");
    return 1;
  }
  const net = cidr.prefix >= other.prefix ? cidr : other;
  io.out(`重なる: ${networkName(net)}/${net.prefix} (${networkName(net)} ～ ${format(net.last, net.version)})\n`);
  return 0;
}

module.exports = { name: "ip", help, usage, run };
