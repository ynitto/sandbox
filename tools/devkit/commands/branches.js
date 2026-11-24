"use strict";

const childProcess = require("node:child_process");
const { ArgParser, ArgTypeError } = require("../core/args");
const { pyJsonStringify, pyRepr } = require("../core/py");
const { formatIso } = require("../core/tz");

const help = "古いローカル Git ブランチを表示し、マージ済みの候補を削除する";
const usage = "devkit branches [--base BASE] [--days DAYS] [--protect BRANCH] [--delete-merged] [--json]";
const heads = "refs/heads/";

function git(io, ...args) {
  const result = childProcess.spawnSync("git", args, {
    cwd: io.cwd, env: io.env, encoding: "utf8", maxBuffer: Infinity,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr.trim() || `git ${args[0]} failed`);
  return result.stdout;
}

function refLines(output) {
  return output.split("\n").filter(Boolean);
}

function inventory(io, base, days, protectedNames) {
  git(io, "rev-parse", "--git-dir");
  const baseRef = heads + base;
  try {
    git(io, "show-ref", "--verify", "--quiet", "--", baseRef);
  } catch (error) {
    throw new Error(`Cannot resolve local base branch ${pyRepr(base)}: ${error.message}`);
  }
  const merged = new Set(refLines(git(io, "for-each-ref", `--merged=${baseRef}`, "--format=%(refname)", heads)));
  const rows = refLines(git(io, "for-each-ref", "--sort=refname", "--sort=committerdate",
    "--format=%(refname)%09%(HEAD)%09%(committerdate:unix)", heads));
  const now = Date.now() / 1000;
  return rows.map((row) => {
    const fields = row.split("\t");
    if (fields.length !== 3 || !/^-?[0-9]+$/.test(fields[2])) throw new Error("Invalid Git reference record");
    const [ref, head, timestamp] = fields;
    const name = ref.slice(heads.length);
    const seconds = BigInt(timestamp);
    if (seconds < -62135596800n || seconds >= 253402300800n) throw new Error("Commit date out of range");
    const age = Math.floor((now - Number(seconds)) / 86400);
    const current = head === "*";
    const isMerged = merged.has(ref);
    const isProtected = current || protectedNames.has(name);
    return {
      name,
      merged: isMerged,
      last_commit_date: formatIso(seconds * 1000000n, "UTC"),
      age_days: Math.max(0, age),
      current,
      protected: isProtected,
      candidate: !isProtected && (isMerged || age > days),
    };
  });
}

function printReport(branches, io) {
  const length = (name) => [...name].length;
  const width = branches.reduce((max, branch) => Math.max(max, length(branch.name)), "Branch".length);
  const padBranch = (name) => name + " ".repeat(width - length(name));
  io.out(`${padBranch("Branch")}  Merged  ${"Last commit (UTC)".padEnd(25)}  ${"Days".padStart(6)}  Current  Candidate\n`);
  for (const branch of branches) {
    const merged = (branch.merged ? "yes" : "no").padEnd(6);
    const date = branch.last_commit_date.padEnd(25);
    const days = String(branch.age_days).padStart(6);
    const current = (branch.current ? "yes" : "no").padEnd(7);
    const row = `${padBranch(branch.name)}  ${merged}  ${date}  ${days}  ${current}  ${branch.candidate ? "*" : ""}`;
    io.out(row.trimEnd() + "\n");
  }
  const candidates = branches.filter((branch) => branch.candidate).map((branch) => branch.name);
  io.out("\nCandidates: " + (candidates.join(", ") || "none") + "\n");
}

function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit branches", usage, description: help });
  parser.addOption("--base", { key: "base", def: "main", help: "local base branch (default: main)" });
  parser.addOption("--days", { key: "days", def: 90n, help: "age threshold (default: 90)", type(value) {
    if (!/^[+-]?[0-9](?:_?[0-9])*$/.test(value.trim())) {
      throw new ArgTypeError(`invalid int value: ${pyRepr(value)}`);
    }
    return BigInt(value.replace(/_/g, ""));
  } });
  parser.addOption("--protect", { key: "protect", append: true, metavar: "BRANCH", help: "protect a branch (repeatable)" });
  parser.addFlag("--delete-merged", { key: "deleteMerged", help: "delete merged candidates" });
  parser.addFlag("--json", { key: "json", help: "output a JSON array" });
  const args = parser.parse(argv);
  if (args.days < 0n) parser.fail("--days must be non-negative");
  let branches;
  try {
    branches = inventory(io, args.base, args.days, new Set(["main", "master", "develop", args.base, ...args.protect]));
  } catch (error) {
    io.err(`Error: ${error.message}\n`);
    return 2;
  }
  if (args.json) {
    const json = pyJsonStringify(branches, 2).replace(/[\x7f-\uffff]/g, (char) =>
      "\\u" + char.charCodeAt(0).toString(16).padStart(4, "0"));
    io.out(json + "\n");
  } else {
    printReport(branches, io);
  }

  if (!args.deleteMerged) return 0;
  let failed = false;
  const deleted = [];
  for (const { name, candidate, merged } of branches) {
    if (!candidate || !merged) continue;
    try {
      git(io, "branch", "-d", "--", name);
      deleted.push(name);
    } catch (error) {
      failed = true;
      io.err(
        `Failed to delete ${pyRepr(name)}: ${error.message}\n` +
        `The tool checks whether the branch is merged into --base ${pyRepr(args.base)}. ` +
        "git branch -d checks the upstream branch (or HEAD if no upstream is set), " +
        "so a different --base can lead Git to refuse the deletion.\n"
      );
    }
  }
  const summary = "Deleted: " + (deleted.join(", ") || "none") + "\n";
  if (args.json) io.err(summary);
  else io.out(summary);
  return failed ? 2 : 0;
}

module.exports = { name: "branches", help, usage, run };
