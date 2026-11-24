"use strict";

// 元ツール: tools/semver-tool (semver_tool.py)
// SemVer 2.0.0 の検証・比較・bump・レンジ判定。

const { ArgParser, UsageError, ArgTypeError, HelpDisplayed } = require("../core/args");
const { pyRepr } = require("../core/py");

const NUMBER = "(?:0|[1-9][0-9]*)";
const IDENTIFIER = "[0-9A-Za-z-]+";
const IDENTIFIERS = `${IDENTIFIER}(?:\\.${IDENTIFIER})*`;
const VERSION_RE = new RegExp(
  `^v?(${NUMBER})\\.(${NUMBER})\\.(${NUMBER})(?:-(${IDENTIFIERS}))?(?:\\+${IDENTIFIERS})?$`
);
const PARTIAL_VERSION_RE = new RegExp(`^v?(${NUMBER})(?:\\.(${NUMBER}))?$`);
const WILDCARD_RE = new RegExp(`^v?(${NUMBER})(?:\\.(${NUMBER}))?\\.x$`);
const COMPARATOR_RE = /^(>=|<=|>|<|=)(.+)$/;
const IDENTIFIER_RE = new RegExp(`^${IDENTIFIER}$`);

class SemVerError extends Error {}

function isAsciiDigits(s) {
  return /^[0-9]+$/.test(s);
}

// (桁数, 文字列) の組で数値識別子を比較する（任意精度）。
function numericKey(number) {
  return [number.length, number];
}

function compareNumericKey(a, b) {
  if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
  if (a[1] === b[1]) return 0;
  return a[1] < b[1] ? -1 : 1;
}

function increment(number) {
  const prefix = number.replace(/9+$/, "");
  const zeros = "0".repeat(number.length - prefix.length);
  if (prefix === "") return "1" + zeros;
  const last = String(Number(prefix[prefix.length - 1]) + 1);
  return prefix.slice(0, -1) + last + zeros;
}

class Version {
  constructor(core, prerelease = []) {
    this.core = core; // [major, minor, patch] 文字列
    this.prerelease = prerelease; // 文字列の配列
  }

  static parse(value) {
    const match = VERSION_RE.exec(value);
    if (!match || match[0] !== value) {
      throw new SemVerError(`invalid SemVer: ${pyRepr(value)}`);
    }
    const [, major, minor, patch, pre] = match;
    const prerelease = pre ? pre.split(".") : [];
    if (
      prerelease.some(
        (part) => isAsciiDigits(part) && part.length > 1 && part.startsWith("0")
      )
    ) {
      throw new SemVerError(`leading zero in numeric prerelease: ${pyRepr(value)}`);
    }
    return new Version([major, minor, patch], prerelease);
  }

  // 優先順位キー: [coreKeys, hasNoPrerelease, prereleaseKeys]
  precedenceKey() {
    return {
      core: this.core.map(numericKey),
      release: this.prerelease.length === 0,
      prerelease: this.prerelease.map((part) =>
        isAsciiDigits(part) ? [0, numericKey(part)] : [1, part]
      ),
    };
  }

  nextCore(index) {
    const core = this.core
      .slice(0, index)
      .concat([increment(this.core[index])], Array(2 - index).fill("0"));
    return new Version(core);
  }

  bump(part, preId) {
    if (part !== "pre") {
      return this.nextCore(["major", "minor", "patch"].indexOf(part));
    }
    let pre = this.prerelease;
    if (pre.length === 0) {
      pre = [preId, "1"];
    } else if (isAsciiDigits(pre[pre.length - 1])) {
      pre = pre.slice(0, -1).concat([increment(pre[pre.length - 1])]);
    } else {
      pre = pre.concat(["1"]);
    }
    return new Version(this.core, pre);
  }

  toString() {
    const core = this.core.join(".");
    return this.prerelease.length ? `${core}-${this.prerelease.join(".")}` : core;
  }
}

function comparePrecedence(a, b) {
  const ka = a.precedenceKey();
  const kb = b.precedenceKey();
  for (let i = 0; i < 3; i += 1) {
    const result = compareNumericKey(ka.core[i], kb.core[i]);
    if (result !== 0) return result;
  }
  if (ka.release !== kb.release) return ka.release ? 1 : -1;
  const length = Math.max(ka.prerelease.length, kb.prerelease.length);
  for (let i = 0; i < length; i += 1) {
    if (i >= ka.prerelease.length) return -1;
    if (i >= kb.prerelease.length) return 1;
    const [tagA, valueA] = ka.prerelease[i];
    const [tagB, valueB] = kb.prerelease[i];
    if (tagA !== tagB) return tagA < tagB ? -1 : 1;
    if (tagA === 0) {
      const result = compareNumericKey(valueA, valueB);
      if (result !== 0) return result;
    } else if (valueA !== valueB) {
      return valueA < valueB ? -1 : 1;
    }
  }
  return 0;
}

const OPERATORS = {
  ">=": (c) => c >= 0,
  "<=": (c) => c <= 0,
  ">": (c) => c > 0,
  "<": (c) => c < 0,
  "=": (c) => c === 0,
};

function interval(lower, index) {
  return [
    [">=", lower],
    ["<", lower.nextCore(index)],
  ];
}

function rangeConditions(expression) {
  const terms = expression.split(/\s+/).filter((term) => term !== "");
  if (terms.length === 0) throw new SemVerError("empty version range");

  if (terms.length === 1) {
    const term = terms[0];
    if (term === "*" || term === "x") return [];
    if (term.startsWith("^")) {
      const partial = PARTIAL_VERSION_RE.exec(term.slice(1));
      if (partial && partial[0] === term.slice(1)) {
        const [, major, minor] = partial;
        const index = major === "0" && minor !== undefined ? 1 : 0;
        return interval(new Version([major, minor || "0", "0"]), index);
      }
      const lower = Version.parse(term.slice(1));
      const index = (() => {
        const found = lower.core.findIndex((value) => value !== "0");
        return found === -1 ? 2 : found;
      })();
      return interval(lower, index);
    }
    if (term.startsWith("~")) {
      const partial = PARTIAL_VERSION_RE.exec(term.slice(1));
      if (partial && partial[0] === term.slice(1)) {
        const [, major, minor] = partial;
        return interval(
          new Version([major, minor || "0", "0"]),
          minor !== undefined ? 1 : 0
        );
      }
      return interval(Version.parse(term.slice(1)), 1);
    }
    const wildcard = WILDCARD_RE.exec(term);
    if (wildcard && wildcard[0] === term) {
      const [, major, minor] = wildcard;
      return interval(
        new Version([major, minor || "0", "0"]),
        minor !== undefined ? 1 : 0
      );
    }
    if (!COMPARATOR_RE.test(term)) {
      return [["=", Version.parse(term)]];
    }
  }

  const conditions = [];
  for (const term of terms) {
    const match = COMPARATOR_RE.exec(term);
    if (!match) throw new SemVerError(`invalid comparator: ${pyRepr(term)}`);
    conditions.push([match[1], Version.parse(match[2])]);
  }
  return conditions;
}

function prereleaseId(value) {
  if (
    !IDENTIFIER_RE.test(value) ||
    (isAsciiDigits(value) && value.length > 1 && value.startsWith("0"))
  ) {
    throw new ArgTypeError(`invalid prerelease identifier: ${pyRepr(value)}`);
  }
  return value;
}

const SUBCOMMANDS = ["check", "compare", "bump", "satisfies"];

function topHelp() {
  return [
    "usage: devkit semver <command> [options]",
    "",
    "Validate, compare, bump, and match SemVer 2.0.0 versions.",
    "",
    "commands:",
    "  check      バージョンが有効か検証する",
    "  compare    2つのバージョンの優先順位を比較する",
    "  bump       バージョンの一部を更新する",
    "  satisfies  レンジを満たすか判定する",
    "",
  ].join("\n");
}

function makeParser(command) {
  const parser = new ArgParser({
    prog: `devkit semver ${command}`,
    usage: `devkit semver ${command} ...`,
    description: "Validate, compare, bump, and match SemVer 2.0.0 versions.",
    allowAbbrev: false,
  });
  if (command === "check") {
    parser.usage = "devkit semver check version";
    parser.addPositional("version");
  } else if (command === "compare") {
    parser.usage = "devkit semver compare a b";
    parser.addPositional("a");
    parser.addPositional("b");
  } else if (command === "bump") {
    parser.usage = "devkit semver bump version --part PART [--pre-id PRE_ID]";
    parser.addPositional("version");
    parser.addOption("--part", {
      key: "part",
      required: true,
      choices: ["major", "minor", "patch", "pre"],
    });
    parser.addOption("--pre-id", { key: "pre_id", def: "rc", type: prereleaseId });
  } else if (command === "satisfies") {
    parser.usage = "devkit semver satisfies version range";
    parser.addPositional("version");
    parser.addPositional("range");
  }
  return parser;
}

function run(argv, io) {
  if (argv.length === 0) {
    throw new UsageError("the following arguments are required: command");
  }
  const [command, ...rest] = argv;
  if (command === "-h" || command === "--help") {
    throw new HelpDisplayed(topHelp());
  }
  if (!SUBCOMMANDS.includes(command)) {
    throw new UsageError(
      `invalid choice: '${command}' (choose from ${SUBCOMMANDS.map((c) => `'${c}'`).join(", ")})`
    );
  }
  const args = makeParser(command).parse(rest);
  try {
    if (command === "compare") {
      const a = Version.parse(args.a);
      const b = Version.parse(args.b);
      io.out(String(comparePrecedence(a, b)) + "\n");
      return 0;
    }
    const version = Version.parse(args.version);
    if (command === "check") {
      io.out("valid\n");
    } else if (command === "bump") {
      io.out(version.bump(args.part, args.pre_id).toString() + "\n");
    } else {
      const conditions = rangeConditions(args.range);
      const matches = conditions.every(([op, bound]) =>
        OPERATORS[op](comparePrecedence(version, bound))
      );
      io.out((matches ? "true" : "false") + "\n");
      return matches ? 0 : 1;
    }
  } catch (error) {
    if (error instanceof SemVerError) {
      io.err(error.message + "\n");
      return command === "check" ? 1 : 2;
    }
    throw error;
  }
  return 0;
}

module.exports = {
  name: "semver",
  help: "SemVer の検証・比較・bump・レンジ判定をする",
  usage: "devkit semver <command> [options]",
  run,
};
