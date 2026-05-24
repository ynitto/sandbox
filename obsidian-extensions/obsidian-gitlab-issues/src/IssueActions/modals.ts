import { App, FuzzySuggestModal, Modal, Notice, Setting, TFile } from "obsidian";
import { IssueActionTemplate, GitlabIssuesSettings } from "../SettingsTab/settings-types";
import {
	IssueRef,
	postIssueComment,
	updateIssueLabels,
	updateNoteFrontmatter,
	setIssueState,
	splitLabelList,
	moveIssueFileForState,
} from "./actions";
import { logger } from "../utils/utils";

export class TextInputModal extends Modal {
	private value: string;

	constructor(
		app: App,
		private opts: {
			title: string;
			description?: string;
			placeholder?: string;
			defaultValue?: string;
			multiline?: boolean;
			submitText?: string;
			onSubmit: (value: string) => void;
		}
	) {
		super(app);
		this.value = opts.defaultValue ?? "";
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.createEl("h3", { text: this.opts.title });
		if (this.opts.description) {
			contentEl.createEl("p", { text: this.opts.description, cls: "setting-item-description" });
		}

		const inputWrap = contentEl.createDiv();
		inputWrap.style.margin = "8px 0";

		let inputEl: HTMLInputElement | HTMLTextAreaElement;
		if (this.opts.multiline) {
			inputEl = inputWrap.createEl("textarea");
			inputEl.rows = 6;
		} else {
			inputEl = inputWrap.createEl("input", { type: "text" });
		}
		inputEl.style.width = "100%";
		inputEl.value = this.value;
		if (this.opts.placeholder) inputEl.placeholder = this.opts.placeholder;
		inputEl.addEventListener("input", () => {
			this.value = inputEl.value;
		});
		inputEl.focus();

		if (!this.opts.multiline) {
			inputEl.addEventListener("keydown", (e: KeyboardEvent) => {
				if (e.key === "Enter") {
					e.preventDefault();
					this.submit();
				}
			});
		}

		new Setting(contentEl)
			.addButton((btn) =>
				btn
					.setButtonText(this.opts.submitText ?? "Submit")
					.setCta()
					.onClick(() => this.submit())
			)
			.addButton((btn) => btn.setButtonText("Cancel").onClick(() => this.close()));
	}

	private submit(): void {
		const v = this.value.trim();
		if (!v) return;
		this.close();
		this.opts.onSubmit(v);
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

export class LabelMultiSelectModal extends Modal {
	private selected = new Set<string>();

	constructor(
		app: App,
		private opts: {
			title: string;
			description?: string;
			labels: string[];
			submitText?: string;
			onSubmit: (selected: string[]) => void;
		}
	) {
		super(app);
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.createEl("h3", { text: this.opts.title });
		if (this.opts.description) {
			contentEl.createEl("p", { text: this.opts.description, cls: "setting-item-description" });
		}

		if (this.opts.labels.length === 0) {
			contentEl.createEl("p", { text: "No labels on this issue." });
			new Setting(contentEl).addButton((btn) =>
				btn.setButtonText("Close").onClick(() => this.close())
			);
			return;
		}

		const listEl = contentEl.createDiv();
		listEl.style.margin = "8px 0";

		this.opts.labels.forEach((label) => {
			const row = listEl.createDiv();
			row.style.display = "flex";
			row.style.alignItems = "center";
			row.style.gap = "6px";
			row.style.padding = "2px 0";

			const cb = row.createEl("input", { type: "checkbox" });
			cb.id = `lbl-${label}`;
			cb.addEventListener("change", () => {
				if (cb.checked) this.selected.add(label);
				else this.selected.delete(label);
			});
			const lblEl = row.createEl("label", { text: label });
			lblEl.htmlFor = cb.id;
		});

		new Setting(contentEl)
			.addButton((btn) =>
				btn
					.setButtonText(this.opts.submitText ?? "Submit")
					.setCta()
					.onClick(() => {
						if (this.selected.size === 0) return;
						this.close();
						this.opts.onSubmit(Array.from(this.selected));
					})
			)
			.addButton((btn) => btn.setButtonText("Cancel").onClick(() => this.close()));
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

export class TemplateSuggestModal extends FuzzySuggestModal<IssueActionTemplate> {
	constructor(
		app: App,
		private templates: IssueActionTemplate[],
		private onSelect: (template: IssueActionTemplate) => void
	) {
		super(app);
		this.setPlaceholder("Pick an issue action template...");
	}

	getItems(): IssueActionTemplate[] {
		return this.templates;
	}

	getItemText(item: IssueActionTemplate): string {
		return item.name;
	}

	onChooseItem(item: IssueActionTemplate): void {
		this.onSelect(item);
	}
}

export class TemplatePreviewModal extends Modal {
	private commentBody: string | null;

	constructor(
		app: App,
		private template: IssueActionTemplate,
		private iid: number,
		private onSubmit: (commentBody: string | null) => void
	) {
		super(app);
		this.commentBody = template.commentBody !== undefined ? template.commentBody : null;
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.createEl("h3", { text: `Apply template: ${this.template.name}` });
		contentEl.createEl("p", { text: `Target: issue #${this.iid}` });

		const summary = contentEl.createEl("ul");
		summary.style.margin = "8px 0";
		if (this.template.commentBody !== undefined) {
			summary.createEl("li", { text: "Post comment (editable below)" });
		}
		if (this.template.labelsReplace !== undefined) {
			const list = this.template.labelsReplace.length > 0 ? this.template.labelsReplace.join(", ") : "(clear all)";
			summary.createEl("li", { text: `Replace labels with: ${list}` });
		} else {
			if (this.template.labelsAdd && this.template.labelsAdd.length > 0) {
				summary.createEl("li", { text: `Add labels: ${this.template.labelsAdd.join(", ")}` });
			}
			if (this.template.labelsRemove && this.template.labelsRemove.length > 0) {
				summary.createEl("li", { text: `Remove labels: ${this.template.labelsRemove.join(", ")}` });
			}
		}
		if (summary.children.length === 0) {
			summary.createEl("li", { text: "(template has no actions configured)" });
		}

		if (this.commentBody !== null) {
			const labelEl = contentEl.createEl("div");
			labelEl.createEl("label", { text: "Comment body:" });
			const ta = labelEl.createEl("textarea");
			ta.rows = 8;
			ta.style.width = "100%";
			ta.style.marginTop = "4px";
			ta.value = this.commentBody;
			ta.addEventListener("input", () => {
				this.commentBody = ta.value;
			});
		}

		new Setting(contentEl)
			.addButton((btn) =>
				btn
					.setButtonText("Apply")
					.setCta()
					.onClick(() => {
						this.close();
						this.onSubmit(this.commentBody);
					})
			)
			.addButton((btn) => btn.setButtonText("Cancel").onClick(() => this.close()));
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

export interface IssueActionsModalHooks {
	getKnownLabels?: () => string[];
	onLabelsLearned?: (labels: string[]) => void | Promise<void>;
}

export class IssueActionsModal extends Modal {
	private file: TFile;
	private labels: string[];
	private commentBody = "";

	constructor(
		app: App,
		private settings: GitlabIssuesSettings,
		private ref: IssueRef,
		file: TFile,
		frontmatter: Record<string, any>,
		private hooks: IssueActionsModalHooks = {}
	) {
		super(app);
		this.file = file;
		this.labels = this.readLabels(frontmatter);
	}

	private knownLabels(): string[] {
		const arr = this.hooks.getKnownLabels ? this.hooks.getKnownLabels() : [];
		return arr ?? [];
	}

	private async announceLearned(labels: string[]): Promise<void> {
		if (!this.hooks.onLabelsLearned || labels.length === 0) return;
		try {
			await this.hooks.onLabelsLearned(labels);
		} catch (e: any) {
			logger(`Failed to record known labels: ${e.message}`);
		}
	}

	private appendToInput(input: HTMLInputElement, label: string): void {
		const existing = splitLabelList(input.value);
		if (existing.includes(label)) return;
		existing.push(label);
		input.value = existing.join(", ");
		input.focus();
	}

	private renderChips(
		parent: HTMLElement,
		suggestions: string[],
		emptyText: string,
		onPick: (label: string) => void
	): HTMLElement {
		const wrap = parent.createDiv();
		wrap.style.display = "flex";
		wrap.style.flexWrap = "wrap";
		wrap.style.gap = "4px";
		wrap.style.margin = "4px 0";
		if (suggestions.length === 0) {
			wrap.createEl("span", {
				text: emptyText,
				cls: "setting-item-description",
			});
			return wrap;
		}
		suggestions.forEach((label) => {
			const chip = wrap.createEl("button", { text: label });
			chip.type = "button";
			chip.style.padding = "1px 8px";
			chip.style.fontSize = "12px";
			chip.style.borderRadius = "10px";
			chip.style.cursor = "pointer";
			chip.addEventListener("click", (e) => {
				e.preventDefault();
				onPick(label);
			});
		});
		return wrap;
	}

	private readLabels(fm: Record<string, any>): string[] {
		const raw = fm?.labels;
		if (Array.isArray(raw)) return raw.map((s) => String(s));
		if (typeof raw === "string" && raw.length > 0) return splitLabelList(raw);
		return [];
	}

	private get state(): string {
		const fm = this.app.metadataCache.getFileCache(this.file)?.frontmatter as
			| Record<string, any>
			| undefined;
		return fm?.state ? String(fm.state) : "opened";
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.empty();
		contentEl.createEl("h3", { text: `Manage issue #${this.ref.iid}` });
		this.renderCommentSection(contentEl);
		contentEl.createEl("hr");
		this.renderLabelsSection(contentEl);
		contentEl.createEl("hr");
		this.renderStateSection(contentEl);
		contentEl.createEl("hr");
		new Setting(contentEl).addButton((btn) =>
			btn.setButtonText("Close").onClick(() => this.close())
		);
	}

	private renderCommentSection(parent: HTMLElement): void {
		parent.createEl("h4", { text: "Post comment" });
		const wrap = parent.createDiv();
		wrap.style.margin = "8px 0";
		const ta = wrap.createEl("textarea");
		ta.rows = 5;
		ta.style.width = "100%";
		ta.placeholder = "Write a comment in Markdown...";
		ta.addEventListener("input", () => {
			this.commentBody = ta.value;
		});

		new Setting(parent).addButton((btn) =>
			btn
				.setButtonText("Post comment")
				.setCta()
				.onClick(async () => {
					const body = this.commentBody.trim();
					if (!body) {
						new Notice("Comment is empty.");
						return;
					}
					btn.setDisabled(true);
					try {
						await postIssueComment(this.settings, this.ref, body);
						new Notice(`Comment posted to issue #${this.ref.iid}`);
						ta.value = "";
						this.commentBody = "";
					} catch (e: any) {
						logger(`Failed to post comment: ${e.message}`);
						new Notice(`Failed to post comment: ${e.message}`);
					} finally {
						btn.setDisabled(false);
					}
				})
		);
	}

	private renderLabelsSection(parent: HTMLElement): void {
		parent.createEl("h4", { text: "Labels" });

		const listEl = parent.createDiv();
		listEl.style.margin = "6px 0";
		const selected = new Set<string>();

		const renderList = () => {
			listEl.empty();
			selected.clear();
			if (this.labels.length === 0) {
				listEl.createEl("p", {
					text: "(no labels)",
					cls: "setting-item-description",
				});
				return;
			}
			this.labels.forEach((label) => {
				const row = listEl.createDiv();
				row.style.display = "flex";
				row.style.alignItems = "center";
				row.style.gap = "6px";
				row.style.padding = "2px 0";
				const cb = row.createEl("input", { type: "checkbox" });
				cb.id = `gitlab-issue-lbl-${label}`;
				cb.addEventListener("change", () => {
					if (cb.checked) selected.add(label);
					else selected.delete(label);
				});
				const lblEl = row.createEl("label", { text: label });
				lblEl.htmlFor = cb.id;
			});
		};
		renderList();

		const addInputWrap = parent.createDiv();
		addInputWrap.style.margin = "6px 0";
		const addInput = addInputWrap.createEl("input", { type: "text" });
		addInput.placeholder = "Add labels (comma-separated, custom OK)";
		addInput.style.width = "100%";

		const addSuggestionsEl = parent.createDiv();
		const renderAddSuggestions = () => {
			addSuggestionsEl.empty();
			const known = this.knownLabels();
			const current = new Set(this.labels);
			const suggestions = known.filter((l) => !current.has(l));
			addSuggestionsEl.createEl("div", {
				text: "Known labels (click to add):",
				cls: "setting-item-description",
			});
			this.renderChips(
				addSuggestionsEl,
				suggestions,
				"(no recorded labels yet — fetch issues first, or just type custom labels above)",
				(label) => this.appendToInput(addInput, label)
			);
		};
		renderAddSuggestions();

		new Setting(parent)
			.addButton((btn) =>
				btn.setButtonText("Add").onClick(async () => {
					const toAdd = splitLabelList(addInput.value);
					if (toAdd.length === 0) return;
					btn.setDisabled(true);
					try {
						const updated = await updateIssueLabels(this.settings, this.ref, { add: toAdd });
						await updateNoteFrontmatter(this.app, this.file, { labels: updated });
						this.labels = updated;
						addInput.value = "";
						renderList();
						await this.announceLearned(toAdd);
						renderAddSuggestions();
						renderReplaceSuggestions();
						new Notice(`Added label(s) to issue #${this.ref.iid}`);
					} catch (e: any) {
						logger(`Failed to add labels: ${e.message}`);
						new Notice(`Failed to add labels: ${e.message}`);
					} finally {
						btn.setDisabled(false);
					}
				})
			)
			.addButton((btn) =>
				btn.setButtonText("Remove selected").onClick(async () => {
					const toRemove = Array.from(selected);
					if (toRemove.length === 0) {
						new Notice("Tick labels to remove first.");
						return;
					}
					btn.setDisabled(true);
					try {
						const updated = await updateIssueLabels(this.settings, this.ref, { remove: toRemove });
						await updateNoteFrontmatter(this.app, this.file, { labels: updated });
						this.labels = updated;
						renderList();
						renderAddSuggestions();
						new Notice(`Removed label(s) from issue #${this.ref.iid}`);
					} catch (e: any) {
						logger(`Failed to remove labels: ${e.message}`);
						new Notice(`Failed to remove labels: ${e.message}`);
					} finally {
						btn.setDisabled(false);
					}
				})
			);

		const replaceWrap = parent.createDiv();
		replaceWrap.style.margin = "6px 0";
		replaceWrap.createEl("div", {
			text: "Replace all labels (empty clears):",
			cls: "setting-item-description",
		});
		const replaceInput = replaceWrap.createEl("input", { type: "text" });
		replaceInput.style.width = "100%";
		replaceInput.value = this.labels.join(", ");
		replaceInput.placeholder = "label1, label2";

		const replaceSuggestionsEl = parent.createDiv();
		const renderReplaceSuggestions = () => {
			replaceSuggestionsEl.empty();
			const known = this.knownLabels();
			replaceSuggestionsEl.createEl("div", {
				text: "Known labels (click to add to replacement set):",
				cls: "setting-item-description",
			});
			this.renderChips(
				replaceSuggestionsEl,
				known,
				"(no recorded labels yet)",
				(label) => this.appendToInput(replaceInput, label)
			);
		};
		renderReplaceSuggestions();

		new Setting(parent).addButton((btn) =>
			btn.setButtonText("Apply replace").onClick(async () => {
				const replace = splitLabelList(replaceInput.value);
				btn.setDisabled(true);
				try {
					const updated = await updateIssueLabels(this.settings, this.ref, { replace });
					await updateNoteFrontmatter(this.app, this.file, { labels: updated });
					this.labels = updated;
					replaceInput.value = updated.join(", ");
					renderList();
					await this.announceLearned(replace);
					renderAddSuggestions();
					renderReplaceSuggestions();
					new Notice(`Labels updated on issue #${this.ref.iid}`);
				} catch (e: any) {
					logger(`Failed to set labels: ${e.message}`);
					new Notice(`Failed to set labels: ${e.message}`);
				} finally {
					btn.setDisabled(false);
				}
			})
		);
	}

	private renderStateSection(parent: HTMLElement): void {
		parent.createEl("h4", { text: "State" });
		const statusEl = parent.createEl("p", {
			text: `Current state: ${this.state}`,
			cls: "setting-item-description",
		});

		new Setting(parent)
			.addButton((btn) =>
				btn.setButtonText("Close issue").onClick(() => this.changeState("close", btn, statusEl))
			)
			.addButton((btn) =>
				btn.setButtonText("Reopen issue").onClick(() => this.changeState("reopen", btn, statusEl))
			);
	}

	private async changeState(
		stateEvent: "close" | "reopen",
		btn: { setDisabled: (b: boolean) => any },
		statusEl: HTMLElement
	): Promise<void> {
		btn.setDisabled(true);
		try {
			const state = await setIssueState(this.settings, this.ref, stateEvent);
			await updateNoteFrontmatter(this.app, this.file, { state });
			this.file = await moveIssueFileForState(this.app, this.file, state);
			statusEl.setText(`Current state: ${state}`);
			new Notice(`Issue #${this.ref.iid} ${state}`);
		} catch (e: any) {
			logger(`Failed to change state: ${e.message}`);
			new Notice(`Failed to change state: ${e.message}`);
		} finally {
			btn.setDisabled(false);
		}
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

export class TemplateScaffoldModal extends Modal {
	private path: string;
	private overwrite = false;
	private linkToSettings = true;

	constructor(
		app: App,
		private opts: {
			kind: "issue" | "mr";
			defaultPath: string;
			currentSettingPath?: string;
			onSubmit: (path: string, overwrite: boolean, linkToSettings: boolean) => Promise<void>;
		}
	) {
		super(app);
		this.path = opts.currentSettingPath && opts.currentSettingPath.length > 0
			? opts.currentSettingPath
			: opts.defaultPath;
	}

	onOpen(): void {
		const { contentEl } = this;
		const label = this.opts.kind === "issue" ? "issue" : "merge request";
		contentEl.createEl("h3", { text: `Generate ${label} template scaffold` });
		contentEl.createEl("p", {
			cls: "setting-item-description",
			text: "Writes a Handlebars template file pre-populated with every supported placeholder. Edit the result to keep only the sections you need.",
		});

		new Setting(contentEl)
			.setName("Output path")
			.setDesc("Path inside the vault, e.g. templates/gitlab-issue.hbs")
			.addText((text) => {
				text.inputEl.style.width = "100%";
				return text.setValue(this.path).onChange((v) => {
					this.path = v;
				});
			});

		new Setting(contentEl)
			.setName("Overwrite if file exists")
			.addToggle((t) => t.setValue(this.overwrite).onChange((v) => (this.overwrite = v)));

		new Setting(contentEl)
			.setName(`Set as ${label} template after creating`)
			.setDesc(`Updates the "${this.opts.kind === "issue" ? "Template File" : "Merge Request Template File"}" setting to point at the new file.`)
			.addToggle((t) => t.setValue(this.linkToSettings).onChange((v) => (this.linkToSettings = v)));

		new Setting(contentEl)
			.addButton((btn) =>
				btn
					.setButtonText("Generate")
					.setCta()
					.onClick(async () => {
						if (!this.path.trim()) {
							new Notice("Please specify an output path.");
							return;
						}
						btn.setDisabled(true);
						try {
							await this.opts.onSubmit(this.path.trim(), this.overwrite, this.linkToSettings);
							this.close();
						} catch (e: any) {
							new Notice(e.message ?? String(e));
						} finally {
							btn.setDisabled(false);
						}
					})
			)
			.addButton((btn) => btn.setButtonText("Cancel").onClick(() => this.close()));
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

export class ConfirmModal extends Modal {
	constructor(
		app: App,
		private opts: {
			title: string;
			message: string;
			submitText?: string;
			onConfirm: () => void;
		}
	) {
		super(app);
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.createEl("h3", { text: this.opts.title });
		contentEl.createEl("p", { text: this.opts.message });

		new Setting(contentEl)
			.addButton((btn) =>
				btn
					.setButtonText(this.opts.submitText ?? "OK")
					.setCta()
					.onClick(() => {
						this.close();
						this.opts.onConfirm();
					})
			)
			.addButton((btn) => btn.setButtonText("Cancel").onClick(() => this.close()));
	}

	onClose(): void {
		this.contentEl.empty();
	}
}
