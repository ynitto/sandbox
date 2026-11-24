"use strict";

const { isIP } = require("node:net");
const { ArgParser, ArgTypeError } = require("../core/args");
const { pyRepr, pyJsonStringify } = require("../core/py");

const help = "URL の構成要素を表示し、クエリーパラメーターを編集する";
const usage = "devkit url [--decode TEXT | --encode TEXT] [--safe CHARS] [--fix-scheme] [--json] [--set KEY=VALUE] [--add KEY=VALUE] [--drop KEY] [url]";
const defaultPorts = new Map([["http", 80], ["https", 443], ["ws", 80], ["wss", 443], ["ftp", 21]]);
const schemePattern = /^[a-z][a-z0-9+.-]*:/i;

function partition(text, separator) {
  const index = text.indexOf(separator);
  return index === -1 ? [text, "", ""] : [text.slice(0, index), separator, text.slice(index + 1)];
}

function decode(value, io, form = false) {
  try {
    return decodeURIComponent(form ? value.replace(/\+/g, " ") : value);
  } catch {
    io.err(`warning: invalid percent encoding in ${pyRepr(value)}\n`);
    return value;
  }
}

function quote(value, safe = "", form = false) {
  let output = "";
  for (const byte of Buffer.from(value, "utf8")) {
    const char = String.fromCharCode(byte);
    if (form && char === " ") output += "+";
    else if (byte < 128 && (/[a-z0-9_.~-]/i.test(char) || safe.includes(char))) output += char;
    else output += "%" + byte.toString(16).toUpperCase().padStart(2, "0");
  }
  return output;
}

function splitUrl(url, io) {
  const schemeMatch = schemePattern.exec(url);
  if (!schemeMatch) throw new Error("URL must include a scheme (or use --fix-scheme)");
  const scheme = schemeMatch[0].slice(0, -1).toLowerCase();
  let rest = url.slice(schemeMatch[0].length);
  let netloc = "";
  if (rest.startsWith("//")) {
    rest = rest.slice(2);
    const end = rest.search(/[/?#]/);
    netloc = end === -1 ? rest : rest.slice(0, end);
    rest = end === -1 ? "" : rest.slice(end);
  }
  if (netloc.includes("[") !== netloc.includes("]")) throw new Error("Invalid IPv6 URL");
  if (netloc.includes("[") && netloc.includes("]")) {
    const bracket = /\[([^\]]*)\]/.exec(netloc);
    const literal = bracket?.[1] ?? "";
    if (literal.startsWith("v")) {
      if (!/^v[0-9a-f]+\.[^]+$/i.test(literal)) throw new Error("IPvFuture address is invalid");
    } else {
      const [ip, marker, scope] = partition(literal, "%");
      if (isIP(literal) === 4) throw new Error("An IPv4 address cannot be in brackets");
      if (isIP(ip) !== 6 || (marker && (!scope || scope.includes("%")))) {
        throw new Error(`${pyRepr(literal)} does not appear to be an IPv4 or IPv6 address`);
      }
    }
  }
  const check = netloc.replace(/[@:#?]/g, "");
  if (/[/?#@:]/.test(check.normalize("NFKC"))) {
    throw new Error(`netloc ${pyRepr(netloc)} contains invalid characters under NFKC normalization`);
  }
  const at = netloc.lastIndexOf("@");
  const authority = netloc.slice(at + 1);
  const [user, passMarker, pass] = partition(at === -1 ? "" : netloc.slice(0, at), ":");
  let hostname;
  let rawPort;
  if (authority.includes("[")) {
    const match = /\[([^\]]*)\]([^]*)/.exec(authority);
    hostname = match?.[1] ?? "";
    rawPort = partition(match?.[2] ?? "", ":")[2];
  } else {
    [hostname, , rawPort] = partition(authority, ":");
  }
  if ((defaultPorts.has(scheme) || scheme === "ssh" || netloc) && !hostname) {
    throw new Error("URL must include a host");
  }
  if ((authority.match(/:/g) ?? []).length > 1 && !authority.startsWith("[")) {
    throw new Error("IPv6 hosts must be enclosed in brackets");
  }
  if (authority.startsWith("[") && !/^\[[^\]]+\](?::[0-9]*)?$/.test(authority)) {
    throw new Error("invalid bracketed host or port");
  }
  if (rawPort && !/^[0-9]+$/.test(rawPort)) {
    throw new Error(`Port could not be cast to integer value as ${pyRepr(rawPort)}`);
  }
  if (Number(rawPort) > 65535) throw new Error("Port out of range 0-65535");
  const [host, zoneMarker, zone] = partition(hostname, "%");
  const [beforeFragment, , fragment] = partition(rest, "#");
  const [path, , query] = partition(beforeFragment, "?");
  const components = {
    scheme,
    username: at === -1 ? null : user,
    password: passMarker ? pass : null,
    host: hostname ? host.toLowerCase() + zoneMarker + zone : null,
    port: rawPort ? Number(rawPort) : defaultPorts.get(scheme) ?? null,
    port_is_default: !rawPort && defaultPorts.has(scheme),
    path,
    fragment,
  };
  const params = query.split("&").filter(Boolean).map((field) => {
    const [key, marker, value] = partition(field, "=");
    return [decode(key, io, true), marker ? decode(value, io, true) : null];
  });
  return { components, params };
}

function rebuild(url, params, args, io) {
  const dropKeys = new Set(args.drop.map((key) => decode(key, io, true)));
  let edited = params.filter(([key]) => !dropKeys.has(key));
  for (const [key, value] of args.set) {
    const index = edited.findIndex(([name]) => name === key);
    edited = edited.filter(([name]) => name !== key);
    edited.splice(index === -1 ? edited.length : index, 0, [key, value]);
  }
  edited.push(...args.add);
  const query = edited.map(([key, value]) => quote(key, "", true) + (value === null ? "" : "=" + quote(value, "", true))).join("&");
  const [prefix, marker, fragment] = partition(url, "#");
  return partition(prefix, "?")[0] + (edited.length ? "?" + query : "") + marker + fragment;
}

function run(argv, io) {
  const parser = new ArgParser({
    prog: "devkit url", usage, description: help, negativePattern: /^-(?:[0-9]+|[0-9]*\.[0-9]+)$/,
  });
  const modes = new Set();
  for (const mode of ["decode", "encode"]) {
    parser.addOption("--" + mode, { key: mode, type(value) {
      modes.add(mode);
      if (modes.size > 1) parser.fail(`argument --${mode}: not allowed with argument --${[...modes][0]}`);
      return value;
    } });
  }
  const assignment = (argument) => {
    const [key, marker, value] = partition(argument, "=");
    if (!marker) throw new ArgTypeError("expected key=value");
    return [decode(key, io, true), value];
  };
  parser.addPositional("url", { nargs: "?" });
  parser.addOption("--safe", { key: "safe" });
  parser.addFlag("--fix-scheme", { key: "fixScheme" });
  parser.addFlag("--json", { key: "json" });
  parser.addOption("--set", { key: "set", type: assignment, append: true });
  parser.addOption("--add", { key: "add", type: assignment, append: true });
  parser.addOption("--drop", { key: "drop", append: true });
  // Argparse treats a following option-looking token as a missing value.
  // A value that starts with a dash can also be supplied using --option=value.
  for (let index = 0; index < argv.length; index++) {
    const token = argv[index];
    if (token === "--" || token === "-h" || token === "--help") break;
    if (!token.startsWith("--") || token.includes("=")) continue;
    const { option } = parser.findOption(token);
    if (!option?.takesValue) continue;
    const next = argv[++index];
    if (next?.length > 1 && next.startsWith("-") && !parser.negativePattern.test(next)) {
      parser.fail("argument " + option.names[0] + ": expected one argument");
    }
  }
  const args = parser.parse(argv);
  const editing = args.set.length || args.add.length || args.drop.length;
  if (args.safe !== undefined && args.encode === undefined) parser.fail("--safe requires --encode");
  if (modes.size) {
    if (args.url !== undefined || args.fixScheme || args.json || editing) {
      parser.fail("--encode and --decode cannot be combined with URL arguments or options");
    }
    io.out((args.encode === undefined ? decode(args.decode, io) : quote(args.encode, args.safe)) + "\n");
    return 0;
  }
  let url = args.url;
  if (url === undefined) parser.fail("a URL, --decode, or --encode is required");
  if (!url || /[\x00-\x20\x7f]/.test(url)) {
    parser.fail("URL is empty or contains whitespace or control characters");
  }
  if (args.fixScheme && (!schemePattern.test(url) || /^[^:/?#]+:[0-9]+(?:[/?#]|$)/.test(url))) {
    url = (url.startsWith("//") ? "https:" : "https://") + url;
  }
  let parsed;
  try {
    parsed = splitUrl(url, io);
  } catch (error) {
    parser.fail(error.message);
  }
  const { components, params } = parsed;
  const rebuilt = editing ? rebuild(url, params, args, io) : undefined;
  if (args.json) {
    const result = { components, params: params.map(([key, value]) => ({ key, value })) };
    if (editing) result.rebuilt = rebuilt;
    io.out(pyJsonStringify(result) + "\n");
    return 0;
  }
  for (const [name, value] of Object.entries(components)) {
    if (name === "port_is_default" || (value === null && ["username", "password", "port"].includes(name))) continue;
    const suffix = name === "port" && components.port_is_default ? " (既定)" : "";
    io.out(`${name}: ${value ?? ""}${suffix}\n`);
  }
  io.out("params:\n");
  for (const [key, value] of params) {
    io.out(`  ${pyRepr(key)}${value === null ? " (値なし)" : " = " + pyRepr(value)}\n`);
  }
  if (editing) io.out(`rebuilt: ${rebuilt}\n`);
  return 0;
}

module.exports = { name: "url", help, usage, example: "devkit url https://example.com/", run };
