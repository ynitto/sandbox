import * as Handlebars from "handlebars";

let registered = false;

export function registerHandlebarsHelpers(): void {
	if (registered) return;
	registered = true;

	Handlebars.registerHelper("eq", (a: unknown, b: unknown) => a === b);

	// {{replace input pattern replacement}}
	// Replaces every occurrence of `pattern` (literal string) in `input` with `replacement`.
	// Returns `input` unchanged when it is not a string or when `pattern` is empty.
	Handlebars.registerHelper("replace", (input: unknown, pattern: unknown, replacement: unknown) => {
		if (typeof input !== "string") return input;
		if (pattern === undefined || pattern === null || pattern === "") return input;
		return input.split(String(pattern)).join(String(replacement ?? ""));
	});

	// {{prefixLines input prefix}}
	// Prepends `prefix` to every line of `input` (including empty lines).
	// Useful for building Markdown blockquotes, indented blocks, etc.
	Handlebars.registerHelper("prefixLines", (input: unknown, prefix: unknown) => {
		if (typeof input !== "string") return input;
		const p = String(prefix ?? "");
		return input.split("\n").map((line) => p + line).join("\n");
	});
}
