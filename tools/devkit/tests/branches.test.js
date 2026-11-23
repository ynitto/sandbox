"use strict";

// 元テスト: tools/git-stale-branches/tests/test_git_stale_branches.py
// Python 版と同様に、実際の一時 Git リポジトリを作って検証する。

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { runCli } = require("./helpers");

const OLD = "2000-01-01T00:00:00Z";
const RECENT = "2099-01-01T00:00:00Z";
const hasGit = spawnSync("git", ["--version"]).status === 0;

function setup(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "devkit-branches-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repo = path.join(root, "repo");
  fs.mkdirSync(repo);
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (!key.startsWith("GIT_")) env[key] = value;
  }
  Object.assign(env, {
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_CONFIG_GLOBAL: os.devNull,
    GIT_CEILING_DIRECTORIES: path.dirname(root),
  });
  const git = (args, extraEnv) => {
    const result = spawnSync("git", args, {
      cwd: repo,
      env: extraEnv ? { ...env, ...extraEnv } : env,
      encoding: "utf8",
    });
    assert.equal(result.status, 0, `git ${args.join(" ")}: ${result.stderr}`);
    return result.stdout;
  };
  const commit = (message, date) =>
    git(["commit", "--allow-empty", "-m", message], {
      GIT_AUTHOR_DATE: date,
      GIT_COMMITTER_DATE: date,
    });
  git(["init", "-b", "main"]);
  git(["config", "--local", "user.name", "Test User"]);
  git(["config", "--local", "user.email", "test@example.invalid"]);
  commit("Initial", OLD);
  for (const name of ["master", "develop", "keep", "also-keep"]) git(["branch", name]);
  git(["switch", "-c", "merged"]);
  commit("Merged change", OLD);
  git(["switch", "main"]);
  git(["merge", "--ff-only", "merged"]);
  git(["switch", "-c", "old"]);
  commit("Old unmerged change", OLD);
  git(["switch", "main"]);
  git(["switch", "-c", "recent"]);
  commit("Recent unmerged change", RECENT);
  git(["switch", "main"]);
  git(["switch", "-c", "current"]);

  const cli = (args, cwd) =>
    runCli(["branches", ...args], { cwd: cwd || repo, env, replaceEnv: true });
  const branchNames = () =>
    new Set(git(["for-each-ref", "--format=%(refname:short)", "refs/heads/"]).split("\n").filter(Boolean));
  return { root, repo, env, git, commit, cli, branchNames };
}

test("json report and protection", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  const before = ctx.branchNames();
  const result = ctx.cli(["--json", "--protect", "keep", "--protect", "also-keep"]);
  assert.equal(result.status, 0, result.stderr);
  const rows = JSON.parse(result.stdout);
  assert.ok(Array.isArray(rows));
  const dates = rows.map((row) => row.last_commit_date);
  assert.deepEqual(dates, [...dates].sort());
  assert.equal(dates[0], "2000-01-01T00:00:00+00:00");
  const branches = Object.fromEntries(rows.map((row) => [row.name, row]));
  assert.equal(branches.merged.merged, true);
  assert.equal(branches.old.merged, false);
  assert.ok(branches.old.age_days > 90);
  assert.equal(branches.recent.age_days, 0);
  assert.equal(branches.recent.candidate, false);
  assert.equal(branches.current.current, true);
  assert.deepEqual(
    new Set(rows.filter((row) => row.candidate).map((row) => row.name)),
    new Set(["merged", "old"])
  );
  for (const name of ["main", "master", "develop", "current", "keep", "also-keep"]) {
    assert.equal(branches[name].protected, true, name);
  }
  assert.deepEqual(ctx.branchNames(), before);
});

test("text report and delete only merged candidates", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  const before = ctx.branchNames();
  const result = ctx.cli(["--protect", "keep", "--protect", "also-keep", "--delete-merged"]);
  assert.equal(result.status, 0, result.stderr);
  assert.ok(result.stdout.includes("Last commit (UTC)"));
  const recentRow = result.stdout.split("\n").find((line) => line.startsWith("recent "));
  assert.equal(recentRow.trimEnd().split(/\s+/).at(-2), "0");
  assert.ok(result.stdout.includes("Candidates: merged, old"));
  assert.ok(result.stdout.includes("Deleted: merged"));
  assert.deepEqual(ctx.branchNames(), new Set([...before].filter((n) => n !== "merged")));
});

test("json delete success and protected branches", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  ctx.git(["branch", "release"]);
  const before = ctx.branchNames();
  const result = ctx.cli([
    "--base", "release", "--json", "--delete-merged",
    "--protect", "keep", "--protect", "also-keep",
  ]);
  assert.equal(result.status, 0, result.stderr);
  assert.ok(Array.isArray(JSON.parse(result.stdout)));
  assert.equal(result.stderr, "Deleted: merged\n");
  const after = ctx.branchNames();
  for (const name of ["main", "master", "develop", "current", "release"]) {
    assert.ok(after.has(name), name);
  }
  assert.deepEqual(after, new Set([...before].filter((n) => n !== "merged")));
});

test("unmerged branch at days threshold", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  ctx.git(["switch", "-c", "threshold"]);
  const committedAt = new Date(Date.now() - (30 * 24 + 12) * 3600 * 1000);
  ctx.commit("Threshold change", committedAt.toISOString().replace(/\.\d{3}Z$/, "Z"));
  const result = ctx.cli(["--days", "30", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  const branch = JSON.parse(result.stdout).find((row) => row.name === "threshold");
  assert.equal(branch.age_days, 30);
  assert.equal(branch.merged, false);
  assert.equal(branch.candidate, false);
});

test("negative days", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  const result = ctx.cli(["--days", "-1"]);
  assert.equal(result.status, 2);
  assert.ok(result.stderr.includes("--days must be non-negative"));
  assert.equal(result.stdout, "");
});

test("ref name with unicode line separator", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  const name = "topic branch";
  ctx.git(["branch", name]);
  const result = ctx.cli(["--json"]);
  assert.equal(result.status, 0, result.stderr);
  const branches = Object.fromEntries(JSON.parse(result.stdout).map((row) => [row.name, row]));
  assert.equal(branches[name].merged, true);
});

test("custom base and days", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  ctx.git(["branch", "old-peer", "old"]);
  let result = ctx.cli(["--base", "old", "--days", "100000", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  let branches = Object.fromEntries(JSON.parse(result.stdout).map((row) => [row.name, row]));
  assert.equal(branches.merged.merged, true);
  assert.equal(branches.old.candidate, false);
  assert.equal(branches["old-peer"].merged, true);
  assert.equal(branches.recent.candidate, false);

  result = ctx.cli(["--days", "100000", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  branches = Object.fromEntries(JSON.parse(result.stdout).map((row) => [row.name, row]));
  assert.equal(branches.old.candidate, false);
  assert.equal(branches["old-peer"].merged, false);
});

test("delete failure keeps json valid and continues", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  ctx.git(["worktree", "add", path.join(ctx.root, "linked"), "merged"]);
  const result = ctx.cli(["--delete-merged", "--json"]);
  assert.equal(result.status, 2);
  assert.ok(Array.isArray(JSON.parse(result.stdout)));
  assert.ok(result.stderr.includes("Failed to delete 'merged'"));
  assert.ok(result.stderr.includes("Deleted: also-keep, keep"));
  assert.ok(ctx.branchNames().has("merged"));
  assert.ok(!ctx.branchNames().has("keep"));
});

test("delete failure when merged only into custom base", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  ctx.git(["branch", "release", "old"]);
  const result = ctx.cli(["--base", "release", "--delete-merged", "--json"]);
  assert.equal(result.status, 2);
  const branches = Object.fromEntries(JSON.parse(result.stdout).map((row) => [row.name, row]));
  assert.equal(branches.old.candidate, true);
  assert.equal(branches.old.merged, true);
  assert.ok(result.stderr.includes("Failed to delete 'old'"));
  assert.ok(result.stderr.includes("--base 'release'"));
  assert.ok(result.stderr.includes("upstream branch (or HEAD"));
  assert.ok(ctx.branchNames().has("old"));
});

test("missing base", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  const result = ctx.cli(["--base", "missing", "--json"]);
  assert.equal(result.status, 2);
  assert.ok(result.stderr.includes("missing"));
  assert.equal(result.stdout, "");
});

test("outside repository", { skip: !hasGit }, (t) => {
  const ctx = setup(t);
  const result = ctx.cli([], ctx.root);
  assert.equal(result.status, 2);
  assert.ok(result.stderr.includes("not a git repository"));
  assert.equal(result.stdout, "");
});
