"use strict";

// argparse に近い挙動の小さな引数パーサ。
// エラー時は UsageError（終了コード2）、-h/--help では HelpDisplayed を投げる。

class UsageError extends Error {}

class HelpDisplayed extends Error {
  constructor(text) {
    super("help displayed");
    this.text = text;
  }
}

class ArgTypeError extends Error {}

class ArgParser {
  // spec: { prog, usage, description, allowAbbrev=true, negativePattern=null }
  constructor({ prog, usage, description = "", allowAbbrev = true, negativePattern = null }) {
    this.prog = prog;
    this.usage = usage;
    this.description = description;
    this.allowAbbrev = allowAbbrev;
    this.negativePattern = negativePattern;
    this.options = []; // {names, key, help, metavar, takesValue, required, def, choices, type, append}
    this.positionals = []; // {name, metavar, help, nargs, type}
  }

  addFlag(names, { key, help = "", def = false } = {}) {
    this.options.push({
      names: [].concat(names),
      key: key || names[0].replace(/^--?/, "").replace(/-/g, "_"),
      help,
      takesValue: false,
      def,
    });
    return this;
  }

  addOption(names, { key, help = "", metavar = null, required = false, def = undefined, choices = null, type = null, append = false } = {}) {
    this.options.push({
      names: [].concat(names),
      key: key || names[0].replace(/^--?/, "").replace(/-/g, "_"),
      help,
      metavar: metavar || (key || names[0].replace(/^--?/, "")).toUpperCase(),
      takesValue: true,
      required,
      def,
      choices,
      type,
      append,
    });
    return this;
  }

  addPositional(name, { metavar = null, help = "", nargs = null, type = null } = {}) {
    this.positionals.push({ name, metavar: metavar || name, help, nargs, type });
    return this;
  }

  helpText() {
    const lines = [`usage: ${this.usage}`, ""];
    if (this.description) lines.push(this.description, "");
    if (this.positionals.length > 0) {
      lines.push("positional arguments:");
      for (const pos of this.positionals) {
        lines.push(`  ${pos.metavar}${pos.help ? `  ${pos.help}` : ""}`);
      }
      lines.push("");
    }
    lines.push("options:");
    lines.push("  -h, --help  show this help message and exit");
    for (const opt of this.options) {
      const rendered = opt.takesValue
        ? opt.names.map((n) => `${n} ${opt.metavar}`).join(", ")
        : opt.names.join(", ");
      lines.push(`  ${rendered}${opt.help ? `  ${opt.help}` : ""}`);
    }
    return lines.join("\n") + "\n";
  }

  fail(message) {
    throw new UsageError(message);
  }

  findOption(token) {
    const eq = token.indexOf("=");
    const name = eq === -1 ? token : token.slice(0, eq);
    let match = this.options.find((opt) => opt.names.includes(name));
    if (!match && this.allowAbbrev && name.startsWith("--")) {
      const candidates = this.options.filter((opt) =>
        opt.names.some((n) => n.startsWith(name))
      );
      if (candidates.length === 1) match = candidates[0];
      else if (candidates.length > 1) {
        this.fail(
          `ambiguous option: ${name} could match ${candidates
            .flatMap((opt) => opt.names)
            .join(", ")}`
        );
      }
    }
    return { option: match, inlineValue: eq === -1 ? undefined : token.slice(eq + 1) };
  }

  convert(opt, displayName, raw) {
    let value = raw;
    if (opt.type) {
      try {
        value = opt.type(raw);
      } catch (error) {
        if (error instanceof ArgTypeError) {
          this.fail(`argument ${displayName}: ${error.message}`);
        }
        throw error;
      }
    }
    if (opt.choices && !opt.choices.includes(value)) {
      const choices = opt.choices.map((c) => `'${c}'`).join(", ");
      this.fail(`argument ${displayName}: invalid choice: '${raw}' (choose from ${choices})`);
    }
    return value;
  }

  parse(argv) {
    const values = {};
    for (const opt of this.options) {
      values[opt.key] = opt.append ? [...(opt.def || [])] : opt.def;
    }
    const positionalValues = [];
    let optionsEnded = false;
    let i = 0;
    const unrecognized = [];
    while (i < argv.length) {
      const token = argv[i];
      if (!optionsEnded && token === "--") {
        optionsEnded = true;
        i += 1;
        continue;
      }
      const isOptionLike =
        !optionsEnded &&
        token.length > 1 &&
        token.startsWith("-") &&
        token !== "-" &&
        !(this.negativePattern && this.negativePattern.test(token));
      if (isOptionLike) {
        if (token === "-h" || token === "--help") {
          throw new HelpDisplayed(this.helpText());
        }
        const { option, inlineValue } = this.findOption(token);
        if (!option) {
          unrecognized.push(token);
          i += 1;
          continue;
        }
        const displayName = option.names[option.names.length - 1];
        if (!option.takesValue) {
          if (inlineValue !== undefined) {
            this.fail(`argument ${displayName}: ignored explicit argument '${inlineValue}'`);
          }
          values[option.key] = true;
          i += 1;
          continue;
        }
        let raw = inlineValue;
        if (raw === undefined) {
          if (i + 1 >= argv.length) {
            this.fail(`argument ${displayName}: expected one argument`);
          }
          raw = argv[i + 1];
          i += 2;
        } else {
          i += 1;
        }
        const converted = this.convert(option, displayName, raw);
        if (option.append) values[option.key].push(converted);
        else values[option.key] = converted;
        continue;
      }
      positionalValues.push(token);
      i += 1;
    }
    if (unrecognized.length > 0) {
      this.fail(`unrecognized arguments: ${unrecognized.join(" ")}`);
    }
    for (const opt of this.options) {
      if (opt.required && values[opt.key] === undefined) {
        this.fail(`the following arguments are required: ${opt.names[opt.names.length - 1]}`);
      }
    }
    // 位置引数への割り当て
    let cursor = 0;
    for (let p = 0; p < this.positionals.length; p += 1) {
      const pos = this.positionals[p];
      const isLast = p === this.positionals.length - 1;
      if (pos.nargs === "*" || pos.nargs === "+") {
        const rest = positionalValues.slice(cursor);
        const take = isLast ? rest : rest.slice(0, Math.max(0, rest.length - (this.positionals.length - p - 1)));
        if (pos.nargs === "+" && take.length === 0) {
          this.fail(`the following arguments are required: ${pos.metavar}`);
        }
        values[pos.name] = take.map((raw) => this.convertPositional(pos, raw));
        cursor += take.length;
      } else if (pos.nargs === "?") {
        if (cursor < positionalValues.length) {
          values[pos.name] = this.convertPositional(pos, positionalValues[cursor]);
          cursor += 1;
        } else {
          values[pos.name] = undefined;
        }
      } else {
        if (cursor >= positionalValues.length) {
          const missing = this.positionals
            .slice(p)
            .filter((q) => q.nargs !== "?" && q.nargs !== "*")
            .map((q) => q.metavar);
          this.fail(`the following arguments are required: ${missing.join(", ")}`);
        }
        values[pos.name] = this.convertPositional(pos, positionalValues[cursor]);
        cursor += 1;
      }
    }
    if (cursor < positionalValues.length) {
      this.fail(`unrecognized arguments: ${positionalValues.slice(cursor).join(" ")}`);
    }
    return values;
  }

  convertPositional(pos, raw) {
    if (!pos.type) return raw;
    try {
      return pos.type(raw);
    } catch (error) {
      if (error instanceof ArgTypeError) {
        this.fail(`argument ${pos.metavar}: ${error.message}`);
      }
      throw error;
    }
  }
}

module.exports = { ArgParser, UsageError, HelpDisplayed, ArgTypeError };
