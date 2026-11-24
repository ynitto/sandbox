"use strict";

const { promises } = require("node:dns");
const { ArgParser, UsageError } = require("../core/args");

const name = "dns";
const help = "ホストの DNS レコードを取得する";
const usage = "devkit dns [--type TYPE] [--json] <host>";
const example = "devkit dns example.com --type A";
const methods = {
  A: "resolve4",
  AAAA: "resolve6",
  CNAME: "resolveCname",
  MX: "resolveMx",
  TXT: "resolveTxt",
  NS: "resolveNs",
};

function createRun(resolver) {
  return async function run(argv, io) {
    const parser = new ArgParser({ prog: "devkit dns", usage, description: help });
    const types = Object.keys(methods);
    parser.addPositional("host", { help: "検索するホスト名" });
    parser.addOption("--type", { choices: [...types, "ALL"], def: "ALL", help: "レコード種別（既定値: ALL）" });
    parser.addFlag("--json", { help: "JSON 形式で出力する" });
    const { host, type, json } = parser.parse(argv);
    if (!host.trim()) throw new UsageError("ホスト名を指定してください");

    const results = await Promise.all((type === "ALL" ? types : [type]).map(async (kind) => {
      try {
        const values = await resolver[methods[kind]](host);
        return { kind, values: kind === "TXT" ? values.map((chunks) => chunks.join("")) : values };
      } catch (error) {
        return { kind, error };
      }
    }));
    const records = {};
    for (const { kind, values, error } of results) {
      if (error && (type !== "ALL" || !["ENODATA", "ENOTFOUND"].includes(error.code))) {
        io.err(`${host} の ${kind} レコードを取得できません: ${error.message}\n`);
        return 1;
      }
      if (values?.length) records[kind] = values;
    }
    if (Object.keys(records).length === 0) {
      io.err(`${host} の ${type} レコードがありません\n`);
      return 1;
    }

    if (json) io.out(JSON.stringify({ host, records }) + "\n");
    else {
      for (const [kind, values] of Object.entries(records)) {
        io.out(`${kind}:\n`);
        for (const value of values) {
          io.out((kind === "MX" ? `${value.priority} ${value.exchange}` : value) + "\n");
        }
      }
    }
    return 0;
  };
}

module.exports = { name, help, usage, example, createRun, run: createRun(promises) };
