import { App, MarkdownView, Notice, TFile } from "obsidian";
import { IssueActionTemplate, GitlabIssuesSettings } from "../SettingsTab/settings-types";
import {
	IssueRef,
	postIssueComment,
	applyLabelChanges,
	updateNoteFrontmatter,
	setIssueState,
	splitLabelList,
	moveIssueFileForState,
} from "./actions";
import { logger } from "../utils/utils";
import {
	parseInlineComments,
	composeAggregateComment,
	addInlineCommentToEditor,
	clearInlineComments,
	clearInlineCommentsInEditor,
} from "./inline-comments";

export const INLINE_REVIEW_HEADING = "## Review comments";

export interface IssueActionsFormHooks {
	getKnownLabels?: () => string[];
	onLabelsLearned?: (labels: string[]) => void | Promise<void>;
	getTemplates?: () => IssueActionTemplate[];
	getSourceEditor?: () => MarkdownView | null;
	getLastSelection?: () => string;
	clearLastSelection?: () => void;
	openInlineCommentModal?: (
		anchor: string,
		onSubmit: (body: string) => void | Promise<void>
	) => void;
}

export interface IssueActionsFormContext {
	file: TFile;
	ref: IssueRef;
	frontmatter: Record<string, any>;
}

interface IssueActionsFormRefs {
	commentTextarea: HTMLTextAreaElement;
	addInput: HTMLInputElement;
	removeInput: HTMLInputElement;
}

export function renderLabelDropdown(
	parent: HTMLElement,
	placeholder: string,
	options: string[],
	onPick: (label: string) => void
): HTMLSelectElement {
	const select = parent.createEl("select");
	select.style.maxWidth = "12em";
	const placeholderOpt = select.createEl("option", {
		text: options.length > 0 ? placeholder : `${placeholder} (none)`,
	});
	placeholderOpt.value = "";
	options.forEach((label) => {
		const opt = select.createEl("option", { text: label });
		opt.value = label;
	});
	select.disabled = options.length === 0;
	select.addEventListener("change", () => {
		const v = select.value;
		if (!v) return;
		onPick(v);
		select.value = "";
	});
	return select;
}

export function appendLabelToInput(input: HTMLInputElement, label: string): void {
	const existing = splitLabelList(input.value);
	if (existing.includes(label)) return;
	existing.push(label);
	input.value = existing.join(", ");
	input.focus();
}

function readLabels(fm: Record<string, any>): string[] {
	const raw = fm?.labels;
	if (Array.isArray(raw)) return raw.map((s) => String(s));
	if (typeof raw === "string" && raw.length > 0) return splitLabelList(raw);
	return [];
}

export class IssueActionsForm {
	private file: TFile | null = null;
	private ref: IssueRef | null = null;
	private labels: string[] = [];
	private commentBody = "";
	private formRefs: IssueActionsFormRefs | null = null;
	private clearAfterPost = true;
	private inlineStatusRefresh: (() => Promise<void>) | null = null;

	constructor(
		private app: App,
		private settings: GitlabIssuesSettings,
		private hooks: IssueActionsFormHooks = {}
	) {}

	render(container: HTMLElement, ctx: IssueActionsFormContext | null): void {
		container.empty();
		this.formRefs = null;

		if (!ctx) {
			const empty = container.createEl("p", { cls: "setting-item-description" });
			empty.style.padding = "8px";
			empty.setText(
				"Open a Gitlab issue note (frontmatter with projectId+iid or webUrl) to manage it from this panel."
			);
			return;
		}

		this.file = ctx.file;
		this.ref = ctx.ref;
		this.labels = readLabels(ctx.frontmatter);
		this.commentBody = "";

		const heading = container.createEl("h3", { text: `Manage issue #${this.ref.iid}` });
		heading.style.margin = "0 0 6px";

		this.renderTemplateSection(container);
		this.renderCommentSection(container);
		this.renderInlineReviewSection(container);
		this.renderRelatedMrsSection(container);
		this.renderLabelsSection(container);
		this.renderStateSection(container);
	}

	private knownLabels(): string[] {
		return this.hooks.getKnownLabels ? this.hooks.getKnownLabels() ?? [] : [];
	}

	private async announceLearned(labels: string[]): Promise<void> {
		if (!this.hooks.onLabelsLearned || labels.length === 0) return;
		try {
			await this.hooks.onLabelsLearned(labels);
		} catch (e: any) {
			logger(`Failed to record known labels: ${e.message}`);
		}
	}

	private inlineRow(parent: HTMLElement): HTMLElement {
		const row = parent.createDiv();
		row.style.display = "flex";
		row.style.alignItems = "center";
		row.style.gap = "6px";
		row.style.margin = "4px 0";
		return row;
	}

	private sectionLabel(parent: HTMLElement, text: string): HTMLElement {
		const el = parent.createEl("div", { text });
		el.style.fontSize = "12px";
		el.style.color = "var(--text-muted)";
		el.style.margin = "8px 0 2px";
		return el;
	}

	private get state(): string {
		if (!this.file) return "opened";
		const fm = this.app.metadataCache.getFileCache(this.file)?.frontmatter as
			| Record<string, any>
			| undefined;
		return fm?.state ? String(fm.state) : "opened";
	}

	private getTemplates(): IssueActionTemplate[] {
		return this.hooks.getTemplates ? this.hooks.getTemplates() ?? [] : [];
	}

	private renderTemplateSection(parent: HTMLElement): void {
		const templates = this.getTemplates();
		if (templates.length === 0) return;

		const row = this.inlineRow(parent);
		row.createEl("span", { text: "Template:" }).style.fontSize = "12px";
		const select = row.createEl("select");
		select.style.flex = "1";
		templates.forEach((t) => {
			const opt = select.createEl("option", { text: t.name });
			opt.value = t.id;
		});
		const loadBtn = row.createEl("button", { text: "Load" });
		loadBtn.type = "button";
		loadBtn.addEventListener("click", (e) => {
			e.preventDefault();
			const tmpl = templates.find((t) => t.id === select.value);
			if (!tmpl) return;
			this.applyTemplateToForm(tmpl);
			new Notice(`Loaded "${tmpl.name}"`);
		});
	}

	private applyTemplateToForm(tmpl: IssueActionTemplate): void {
		if (!this.formRefs) return;
		if (tmpl.commentBody !== undefined) {
			this.formRefs.commentTextarea.value = tmpl.commentBody;
			this.commentBody = tmpl.commentBody;
		}

		// Legacy replace = remove "*" then add labelsReplace
		if (tmpl.labelsReplace !== undefined) {
			this.formRefs.removeInput.value = "*";
			this.formRefs.addInput.value = (tmpl.labelsReplace ?? []).join(", ");
			return;
		}

		if (tmpl.labelsAdd !== undefined) {
			this.formRefs.addInput.value = (tmpl.labelsAdd ?? []).join(", ");
		}
		if (tmpl.labelsRemove !== undefined) {
			this.formRefs.removeInput.value = (tmpl.labelsRemove ?? []).join(", ");
		}
	}

	private renderCommentSection(parent: HTMLElement): void {
		this.sectionLabel(parent, "Comment");
		const ta = parent.createEl("textarea");
		ta.rows = 8;
		ta.style.width = "100%";
		ta.placeholder = "Write a comment in Markdown...";
		ta.addEventListener("input", () => {
			this.commentBody = ta.value;
		});
		this.formRefs = {
			...(this.formRefs ?? ({} as IssueActionsFormRefs)),
			commentTextarea: ta,
		};

		const buttonRow = this.inlineRow(parent);
		buttonRow.style.justifyContent = "space-between";

		const quoteBtn = buttonRow.createEl("button", { text: "Quote selection" });
		quoteBtn.type = "button";
		quoteBtn.addEventListener("click", (e) => {
			e.preventDefault();
			this.insertQuoteFromSelection();
		});

		const postBtn = buttonRow.createEl("button", { text: "Post comment" });
		postBtn.type = "button";
		postBtn.classList.add("mod-cta");
		postBtn.addEventListener("click", async (e) => {
			e.preventDefault();
			if (!this.ref) return;
			const body = this.commentBody.trim();
			if (!body) {
				new Notice("Comment is empty.");
				return;
			}
			postBtn.setAttr("disabled", "true");
			try {
				await postIssueComment(this.settings, this.ref, body);
				new Notice(`Comment posted to #${this.ref.iid}`);
				ta.value = "";
				this.commentBody = "";
			} catch (err: any) {
				logger(`Failed to post comment: ${err.message}`);
				new Notice(`Failed to post comment: ${err.message}`);
			} finally {
				postBtn.removeAttribute("disabled");
			}
		});
	}

	private getIssueView(): MarkdownView | null {
		const view =
			this.hooks.getSourceEditor?.() ?? this.app.workspace.getActiveViewOfType(MarkdownView);
		if (view && this.file && view.file && view.file.path === this.file.path) return view;
		return null;
	}

	private async readNoteText(): Promise<string> {
		const view = this.getIssueView();
		if (view) return view.editor.getValue();
		if (this.file) {
			try {
				return await this.app.vault.read(this.file);
			} catch (e: any) {
				logger(`Failed to read note: ${e.message}`);
			}
		}
		return "";
	}

	private renderInlineReviewSection(parent: HTMLElement): void {
		this.sectionLabel(parent, "Inline review");

		const status = parent.createEl("div");
		status.style.fontSize = "12px";
		status.style.color = "var(--text-muted)";
		status.style.margin = "0 0 4px";

		const refreshStatus = async () => {
			const text = await this.readNoteText();
			const n = text ? parseInlineComments(text).length : 0;
			status.setText(`${n} inline comment${n === 1 ? "" : "s"} in this note`);
		};
		this.inlineStatusRefresh = refreshStatus;
		void refreshStatus();

		const row = this.inlineRow(parent);

		const addBtn = row.createEl("button", { text: "＋ From selection" });
		addBtn.type = "button";
		addBtn.title = "Anchor a comment to the selected text in the issue note";
		addBtn.addEventListener("click", (e) => {
			e.preventDefault();
			this.addInlineCommentFromSelection();
		});

		const composeBtn = row.createEl("button", { text: "Compose ↑" });
		composeBtn.type = "button";
		composeBtn.title = "Aggregate the inline comments into the comment box above";
		composeBtn.addEventListener("click", async (e) => {
			e.preventDefault();
			await this.composeInlineIntoComment();
		});

		const clearBtn = row.createEl("button", { text: "Clear" });
		clearBtn.type = "button";
		clearBtn.title = "Remove all inline review annotations from the note";
		clearBtn.addEventListener("click", async (e) => {
			e.preventDefault();
			await this.clearInlineAnnotations();
			await refreshStatus();
		});

		const optRow = this.inlineRow(parent);
		const clearWrap = optRow.createEl("label");
		clearWrap.style.display = "flex";
		clearWrap.style.alignItems = "center";
		clearWrap.style.gap = "4px";
		clearWrap.style.fontSize = "12px";
		const clearCb = clearWrap.createEl("input", { type: "checkbox" });
		clearCb.checked = this.clearAfterPost;
		clearCb.addEventListener("change", () => {
			this.clearAfterPost = clearCb.checked;
		});
		clearWrap.createEl("span", { text: "Clear annotations after posting" });

		const postRow = this.inlineRow(parent);
		postRow.style.justifyContent = "flex-end";
		const reworkBtn = postRow.createEl("button", { text: "Post review → request re-work" });
		reworkBtn.type = "button";
		reworkBtn.classList.add("mod-cta");
		reworkBtn.title =
			"Post the aggregated comment, apply the label changes below, then clear annotations";
		reworkBtn.addEventListener("click", async (e) => {
			e.preventDefault();
			await this.postReview(reworkBtn);
		});
	}

	private addInlineCommentFromSelection(): void {
		const view = this.getIssueView();
		if (!view) {
			new Notice("Open this issue note in the editor, then select text to comment on.");
			return;
		}
		const open = this.hooks.openInlineCommentModal;
		if (!open) {
			new Notice("Inline comment modal is unavailable.");
			return;
		}
		const editor = view.editor;
		// Capture the selection up front — focusing the modal clears it.
		const from = editor.getCursor("from");
		const to = editor.getCursor("to");
		const anchor = editor.getSelection() ?? "";

		open(anchor, async (body) => {
			addInlineCommentToEditor(editor, body, { text: anchor, from, to });
			new Notice("Inline comment added.");
			if (this.inlineStatusRefresh) await this.inlineStatusRefresh();
		});
	}

	private async composeInlineIntoComment(): Promise<void> {
		const text = await this.readNoteText();
		const comments = parseInlineComments(text);
		if (comments.length === 0) {
			new Notice("No inline comments to compose.");
			return;
		}
		const body = composeAggregateComment(comments, { heading: INLINE_REVIEW_HEADING });
		const ta = this.formRefs?.commentTextarea;
		if (!ta) return;
		ta.value = body;
		this.commentBody = body;
		ta.focus();
		new Notice(`Composed ${comments.length} inline comment${comments.length === 1 ? "" : "s"}.`);
	}

	private async clearInlineAnnotations(): Promise<void> {
		const view = this.getIssueView();
		if (view) {
			clearInlineCommentsInEditor(view.editor);
		} else if (this.file) {
			const text = await this.app.vault.read(this.file);
			const cleaned = clearInlineComments(text);
			if (cleaned !== text) await this.app.vault.modify(this.file, cleaned);
		}
		new Notice("Inline annotations cleared.");
	}

	private async postReview(btn: HTMLButtonElement): Promise<void> {
		if (!this.ref || !this.file) return;

		let body = (this.formRefs?.commentTextarea?.value ?? this.commentBody).trim();
		if (!body) {
			const comments = parseInlineComments(await this.readNoteText());
			if (comments.length === 0) {
				new Notice("Nothing to post — add inline comments or write a comment first.");
				return;
			}
			body = composeAggregateComment(comments, { heading: INLINE_REVIEW_HEADING }).trim();
		}

		btn.setAttr("disabled", "true");
		try {
			await postIssueComment(this.settings, this.ref, body);

			// Optional label swap to drive re-work (only when inputs are filled).
			const addList = splitLabelList(this.formRefs?.addInput?.value ?? "");
			const removePatterns = splitLabelList(this.formRefs?.removeInput?.value ?? "");
			if (addList.length > 0 || removePatterns.length > 0) {
				const updated = await applyLabelChanges(
					this.settings,
					this.ref,
					this.labels,
					removePatterns,
					addList
				);
				await updateNoteFrontmatter(this.app, this.file, { labels: updated });
				this.labels = updated;
				await this.announceLearned(addList);
			}

			if (this.clearAfterPost) {
				await this.clearInlineAnnotations();
			}

			if (this.formRefs?.commentTextarea) this.formRefs.commentTextarea.value = "";
			this.commentBody = "";
			if (this.formRefs?.addInput) this.formRefs.addInput.value = "";
			if (this.formRefs?.removeInput) this.formRefs.removeInput.value = "";
			if (this.inlineStatusRefresh) await this.inlineStatusRefresh();

			new Notice(`Review posted to #${this.ref.iid}`);
		} catch (err: any) {
			logger(`Failed to post review: ${err.message}`);
			new Notice(`Failed to post review: ${err.message}`);
		} finally {
			btn.removeAttribute("disabled");
		}
	}

	private renderLabelsSection(parent: HTMLElement): void {
		this.sectionLabel(parent, "Labels");

		const currentEl = parent.createDiv();
		currentEl.style.fontSize = "12px";
		currentEl.style.margin = "0 0 4px";
		const renderCurrent = () => {
			currentEl.empty();
			currentEl.createEl("span", { text: "Current: " }).style.color = "var(--text-muted)";
			currentEl.createEl("span", {
				text: this.labels.length > 0 ? this.labels.join(", ") : "(none)",
			});
		};
		renderCurrent();

		const addRow = this.inlineRow(parent);
		const addLabel = addRow.createEl("span", { text: "Add" });
		addLabel.style.fontSize = "12px";
		addLabel.style.minWidth = "3em";
		const addInput = addRow.createEl("input", { type: "text" });
		addInput.placeholder = "label1, label2 (custom OK)";
		addInput.style.flex = "1";
		let addPicker: HTMLSelectElement;
		const refreshAddPicker = () => {
			if (addPicker) addPicker.remove();
			const known = this.knownLabels();
			const current = new Set(this.labels);
			const suggestions = known.filter((l) => !current.has(l));
			addPicker = renderLabelDropdown(addRow, "+ known", suggestions, (label) =>
				appendLabelToInput(addInput, label)
			);
		};
		refreshAddPicker();

		const removeRow = this.inlineRow(parent);
		const removeLabel = removeRow.createEl("span", { text: "Remove" });
		removeLabel.style.fontSize = "12px";
		removeLabel.style.minWidth = "3em";
		const removeInput = removeRow.createEl("input", { type: "text" });
		removeInput.placeholder = "label1, status:* (wildcards OK)";
		removeInput.style.flex = "1";
		let removePicker: HTMLSelectElement;
		const refreshRemovePicker = () => {
			if (removePicker) removePicker.remove();
			removePicker = renderLabelDropdown(removeRow, "+ current", this.labels, (label) =>
				appendLabelToInput(removeInput, label)
			);
		};
		refreshRemovePicker();

		this.formRefs = {
			...(this.formRefs ?? ({} as IssueActionsFormRefs)),
			addInput,
			removeInput,
		};

		const applyRow = this.inlineRow(parent);
		applyRow.style.justifyContent = "flex-end";
		const applyBtn = applyRow.createEl("button", { text: "Apply (remove → add)" });
		applyBtn.type = "button";
		applyBtn.classList.add("mod-cta");
		applyBtn.addEventListener("click", async (e) => {
			e.preventDefault();
			if (!this.ref || !this.file) return;
			const removePatterns = splitLabelList(removeInput.value);
			const addList = splitLabelList(addInput.value);
			if (removePatterns.length === 0 && addList.length === 0) {
				new Notice("Nothing to apply.");
				return;
			}
			applyBtn.setAttr("disabled", "true");
			try {
				const updated = await applyLabelChanges(
					this.settings,
					this.ref,
					this.labels,
					removePatterns,
					addList
				);
				await updateNoteFrontmatter(this.app, this.file, { labels: updated });
				this.labels = updated;
				addInput.value = "";
				removeInput.value = "";
				renderCurrent();
				refreshAddPicker();
				refreshRemovePicker();
				await this.announceLearned(addList);
				new Notice(`Labels updated on #${this.ref.iid}`);
			} catch (err: any) {
				logger(`Failed to apply label changes: ${err.message}`);
				new Notice(`Failed to apply label changes: ${err.message}`);
			} finally {
				applyBtn.removeAttribute("disabled");
			}
		});
	}

	private renderStateSection(parent: HTMLElement): void {
		const row = this.inlineRow(parent);
		row.style.marginTop = "10px";
		row.createEl("span", { text: "State:" }).style.fontSize = "12px";
		const statusEl = row.createEl("span", { text: this.state });
		statusEl.style.flex = "1";
		statusEl.style.fontSize = "12px";

		const closeBtn = row.createEl("button", { text: "Close" });
		closeBtn.type = "button";
		closeBtn.addEventListener("click", (e) => {
			e.preventDefault();
			this.changeState("close", closeBtn, statusEl);
		});
		const reopenBtn = row.createEl("button", { text: "Reopen" });
		reopenBtn.type = "button";
		reopenBtn.addEventListener("click", (e) => {
			e.preventDefault();
			this.changeState("reopen", reopenBtn, statusEl);
		});
	}

	private insertQuoteFromSelection(): void {
		const ta = this.formRefs?.commentTextarea;
		if (!ta) return;

		// 1) Prefer the cached selection (kept current by the plugin's
		//    selectionchange listener). Survives focus shifts cleanly.
		let selection = this.hooks.getLastSelection ? this.hooks.getLastSelection() : "";
		// 2) Fall back to live editor selection.
		if (!selection) {
			const view =
				this.hooks.getSourceEditor?.() ??
				this.app.workspace.getActiveViewOfType(MarkdownView);
			if (view) {
				const cmSel = view.editor.getSelection();
				if (cmSel) selection = cmSel;
			}
		}
		// 3) Fall back to window selection (reading mode).
		if (!selection) {
			const winSel = window.getSelection();
			if (winSel) selection = winSel.toString();
		}
		if (!selection.trim()) {
			new Notice("Select some text in a note first.");
			return;
		}

		const quoted = selection.split("\n").map((l) => `> ${l}`).join("\n") + "\n\n";
		const start = ta.selectionStart ?? ta.value.length;
		const end = ta.selectionEnd ?? ta.value.length;
		ta.value = ta.value.substring(0, start) + quoted + ta.value.substring(end);
		this.commentBody = ta.value;
		const cursor = start + quoted.length;
		ta.focus();
		ta.setSelectionRange(cursor, cursor);
	}

	private renderRelatedMrsSection(parent: HTMLElement): void {
		if (!this.file) return;
		const cache = this.app.metadataCache.getFileCache(this.file);
		const allLinks = cache?.links ?? [];

		const seen = new Set<string>();
		const mrLinks = allLinks.filter((l) => {
			const basename = (l.link.split("/").pop() ?? "").trim();
			if (!basename.startsWith("!")) return false;
			if (seen.has(l.link)) return false;
			seen.add(l.link);
			return true;
		});
		if (mrLinks.length === 0) return;

		this.sectionLabel(parent, "Related MRs");
		const sourcePath = this.file.path;
		mrLinks.forEach((l) => {
			const row = this.inlineRow(parent);
			const label = l.displayText && l.displayText !== l.link ? l.displayText : l.link;
			const linkEl = row.createEl("span", { text: label });
			linkEl.style.flex = "1";
			linkEl.style.fontSize = "12px";

			const splitBtn = row.createEl("button", { text: "↗ Split" });
			splitBtn.type = "button";
			splitBtn.title = "Open in side split";
			splitBtn.addEventListener("click", (e) => {
				e.preventDefault();
				this.app.workspace.openLinkText(l.link, sourcePath, "split");
			});

			const tabBtn = row.createEl("button", { text: "Tab" });
			tabBtn.type = "button";
			tabBtn.title = "Open in new tab";
			tabBtn.addEventListener("click", (e) => {
				e.preventDefault();
				this.app.workspace.openLinkText(l.link, sourcePath, "tab");
			});
		});
	}

	private async changeState(
		stateEvent: "close" | "reopen",
		btn: HTMLButtonElement,
		statusEl: HTMLElement
	): Promise<void> {
		if (!this.ref || !this.file) return;
		btn.setAttr("disabled", "true");
		try {
			const state = await setIssueState(this.settings, this.ref, stateEvent);
			await updateNoteFrontmatter(this.app, this.file, { state });
			this.file = await moveIssueFileForState(this.app, this.file, state);
			statusEl.setText(state);
			new Notice(`Issue #${this.ref.iid} ${state}`);
		} catch (e: any) {
			logger(`Failed to change state: ${e.message}`);
			new Notice(`Failed to change state: ${e.message}`);
		} finally {
			btn.removeAttribute("disabled");
		}
	}
}
