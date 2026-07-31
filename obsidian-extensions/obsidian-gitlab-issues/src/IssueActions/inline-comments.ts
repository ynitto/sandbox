import type { Editor, EditorPosition } from "obsidian";

// Inline review comments let a reviewer anchor short notes to spans of an
// issue/MR note, Obsidian-footnote style, and later aggregate them into a
// single GitLab comment. The on-disk representation is plain Markdown so the
// note stays human-readable and hand-editable:
//
//   ... this ==step is not idempotent==[^gli-1] so retries ...
//
//   <!-- gitlab-review-comments:start -->
//   [^gli-1]: 💬 Re-running double-applies the migration. Make it idempotent.
//   <!-- gitlab-review-comments:end -->
//
// Footnote labels are namespaced (`gli-<n>`) so they never collide with a
// user's own footnotes, and the definitions live inside an HTML-comment
// fenced region so they can be collected and cleared deterministically.

export const REGION_START = "<!-- gitlab-review-comments:start -->";
export const REGION_END = "<!-- gitlab-review-comments:end -->";
export const COMMENT_MARKER = "💬 ";
export const LABEL_PREFIX = "gli-";

export interface InlineComment {
	/** Numeric portion of the footnote label, e.g. 1 for `gli-1`. */
	index: number;
	/** Full footnote label without the caret, e.g. `gli-1`. */
	label: string;
	/** Comment text (the 💬 marker stripped). */
	body: string;
	/** Highlighted anchor text the comment is attached to, if any. */
	anchor?: string;
}

const DEF_RE = /^\[\^(gli-(\d+))\]:[ \t]*(.*)$/;

/** Highest existing inline-comment index in the note (0 when there are none). */
export function highestInlineIndex(text: string): number {
	let max = 0;
	for (const line of text.split("\n")) {
		const m = line.match(DEF_RE);
		if (m) {
			const n = parseInt(m[2], 10);
			if (n > max) max = n;
		}
	}
	return max;
}

export function nextInlineLabel(text: string): string {
	return `${LABEL_PREFIX}${highestInlineIndex(text) + 1}`;
}

function findAnchor(text: string, label: string): string | undefined {
	const re = new RegExp("==([^=]+?)==\\[\\^" + escapeRegExp(label) + "\\]");
	const m = text.match(re);
	return m ? m[1].trim() : undefined;
}

/** Parse every managed inline comment, ordered by index. */
export function parseInlineComments(text: string): InlineComment[] {
	const out: InlineComment[] = [];
	for (const line of text.split("\n")) {
		const m = line.match(DEF_RE);
		if (!m) continue;
		const label = m[1];
		let body = m[3].trim();
		if (body.startsWith(COMMENT_MARKER)) body = body.slice(COMMENT_MARKER.length).trim();
		out.push({
			index: parseInt(m[2], 10),
			label,
			body,
			anchor: findAnchor(text, label),
		});
	}
	out.sort((a, b) => a.index - b.index);
	return out;
}

/** Single-line, footnote-safe rendering of a free-text comment body. */
function normalizeBody(body: string): string {
	return body.replace(/\r?\n/g, " ").replace(/\s+/g, " ").trim();
}

/**
 * Insert a new inline comment into raw note text. The anchor (selected text)
 * is wrapped in a highlight + footnote ref; when no anchor is given the bare
 * ref is appended to the end of the body so it still resolves.
 *
 * Returns the rewritten text plus the assigned label. `anchorReplacement`
 * tells an editor-backed caller what to swap the live selection for.
 */
export function addInlineComment(
	text: string,
	anchor: string | null,
	body: string
): { text: string; label: string; anchorReplacement: string } {
	const label = nextInlineLabel(text);
	const ref = `[^${label}]`;
	const anchorReplacement = anchor && anchor.length > 0 ? `==${anchor}==${ref}` : ref;
	const defLine = `[^${label}]: ${COMMENT_MARKER}${normalizeBody(body)}`;

	let next = text;
	if (anchor && anchor.length > 0) {
		// Replace the first verbatim occurrence of the selected text.
		const idx = next.indexOf(anchor);
		if (idx !== -1) {
			next = next.slice(0, idx) + anchorReplacement + next.slice(idx + anchor.length);
		} else {
			next = appendBareRef(next, ref);
		}
	} else {
		next = appendBareRef(next, ref);
	}

	next = upsertDefinition(next, defLine);
	return { text: next, label, anchorReplacement };
}

function appendBareRef(text: string, ref: string): string {
	const region = regionBounds(text);
	if (region) {
		const head = text.slice(0, region.start).replace(/\s+$/, "");
		return head + ` ${ref}\n\n` + text.slice(region.start);
	}
	const trimmed = text.replace(/\s+$/, "");
	return `${trimmed} ${ref}\n`;
}

interface RegionBounds {
	start: number; // index of REGION_START
	innerStart: number; // first char after the start marker line
	innerEnd: number; // index of REGION_END
	end: number; // index just past REGION_END line
}

function regionBounds(text: string): RegionBounds | null {
	const start = text.indexOf(REGION_START);
	if (start === -1) return null;
	const innerStart = start + REGION_START.length + 1; // skip trailing newline
	const innerEnd = text.indexOf(REGION_END, innerStart);
	if (innerEnd === -1) return null;
	let end = innerEnd + REGION_END.length;
	if (text[end] === "\n") end += 1;
	return { start, innerStart, innerEnd, end };
}

/** Append a footnote definition line to the managed region, creating it if needed. */
export function upsertDefinition(text: string, defLine: string): string {
	const region = regionBounds(text);
	if (!region) {
		const sep = text.endsWith("\n") ? "\n" : "\n\n";
		return `${text}${sep}${REGION_START}\n${defLine}\n${REGION_END}\n`;
	}
	const before = text.slice(0, region.innerEnd);
	const after = text.slice(region.innerEnd);
	const normalizedBefore = before.endsWith("\n") ? before : before + "\n";
	return `${normalizedBefore}${defLine}\n${after}`;
}

/**
 * Strip every managed inline comment: unwrap highlighted anchors, drop bare
 * refs, and remove the definitions region. Restores the note to clean prose.
 */
export function clearInlineComments(text: string): string {
	let next = text;
	// Unwrap highlighted anchors: ==foo==[^gli-N] -> foo
	next = next.replace(/==([^=]*?)==\[\^gli-\d+\]/g, "$1");
	// Drop any remaining bare refs.
	next = next.replace(/[ \t]*\[\^gli-\d+\]/g, "");
	// Remove the definitions region.
	const region = regionBounds(next);
	if (region) {
		let head = next.slice(0, region.start);
		const tail = next.slice(region.end);
		head = head.replace(/\n{2,}$/, "\n");
		next = head + tail;
	}
	// Tidy any trailing whitespace runs we may have introduced.
	return next.replace(/[ \t]+$/gm, "");
}

export interface ComposeOptions {
	heading?: string;
	/** When false, omit the quoted anchor snippets. */
	includeAnchors?: boolean;
}

/** Build a single Markdown comment body from a set of inline comments. */
export function composeAggregateComment(
	comments: InlineComment[],
	opts: ComposeOptions = {}
): string {
	if (comments.length === 0) return "";
	const heading = opts.heading ?? "## Review comments";
	const includeAnchors = opts.includeAnchors !== false;

	const blocks = comments.map((c, i) => {
		const n = i + 1;
		const lines: string[] = [];
		if (includeAnchors && c.anchor) {
			lines.push(`${n}. > ${c.anchor}`);
			lines.push("");
			lines.push(`   ${c.body}`);
		} else {
			lines.push(`${n}. ${c.body}`);
		}
		return lines.join("\n");
	});

	return `${heading}\n\n${blocks.join("\n\n")}\n`;
}

function escapeRegExp(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// --- Editor-backed helpers -------------------------------------------------

export interface EditorSelection {
	text: string;
	from: EditorPosition;
	to: EditorPosition;
}

/**
 * Insert an inline comment using the live editor so cursor position and undo
 * history stay intact. When a selection range is supplied it is wrapped in a
 * highlight + footnote ref; otherwise a bare ref is dropped at the cursor.
 * The caller must capture the selection range *before* opening the comment
 * modal, since focusing the modal clears the editor's live selection.
 * Returns the assigned label.
 */
export function addInlineCommentToEditor(
	editor: Editor,
	body: string,
	selection?: EditorSelection
): string {
	const fullText = editor.getValue();
	const label = nextInlineLabel(fullText);
	const ref = `[^${label}]`;

	if (selection && selection.text.trim().length > 0) {
		editor.replaceRange(`==${selection.text}==${ref}`, selection.from, selection.to);
	} else {
		const cursor = editor.getCursor();
		editor.replaceRange(` ${ref}`, cursor);
	}

	const defLine = `[^${label}]: ${COMMENT_MARKER}${normalizeBody(body)}`;
	appendDefinitionToEditor(editor, defLine);
	return label;
}

function appendDefinitionToEditor(editor: Editor, defLine: string): void {
	const text = editor.getValue();
	const region = regionBounds(text);
	if (!region) {
		const sep = text.endsWith("\n") ? "\n" : "\n\n";
		const block = `${sep}${REGION_START}\n${defLine}\n${REGION_END}\n`;
		editor.replaceRange(block, editor.offsetToPos(text.length));
		return;
	}
	// Insert just before the END marker.
	const before = text.slice(0, region.innerEnd);
	const insertAt = before.endsWith("\n")
		? editor.offsetToPos(region.innerEnd)
		: editor.offsetToPos(region.innerEnd);
	const prefix = before.endsWith("\n") ? "" : "\n";
	editor.replaceRange(`${prefix}${defLine}\n`, insertAt);
}

/** Clear all inline comments in an open editor (preserving undo history). */
export function clearInlineCommentsInEditor(editor: Editor): void {
	const cleaned = clearInlineComments(editor.getValue());
	if (cleaned === editor.getValue()) return;
	const lastLine = editor.lastLine();
	const lastCh = editor.getLine(lastLine).length;
	editor.replaceRange(cleaned, { line: 0, ch: 0 }, { line: lastLine, ch: lastCh });
}
