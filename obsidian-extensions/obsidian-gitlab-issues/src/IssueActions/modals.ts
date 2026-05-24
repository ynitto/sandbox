import { App, Modal, Notice, Setting, TFile } from "obsidian";
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
import { createIssue } from "./actions";
import { logger } from "../utils/utils";

function renderLabelDropdown(
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

function appendLabelToInput(input: HTMLInputElement, label: string): void {
	const existing = splitLabelList(input.value);
	if (existing.includes(label)) return;
	existing.push(label);
	input.value = existing.join(", ");
	input.focus();
}

export interface IssueActionsModalHooks {
	getKnownLabels?: () => string[];
	onLabelsLearned?: (labels: string[]) => void | Promise<void>;
	getTemplates?: () => IssueActionTemplate[];
}

interface IssueActionsFormRefs {
	commentTextarea: HTMLTextAreaElement;
	addInput: HTMLInputElement;
	removeInput: HTMLInputElement;
}

export class IssueActionsModal extends Modal {
	private file: TFile;
	private labels: string[];
	private commentBody = "";
	private formRefs: IssueActionsFormRefs | null = null;

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

	private inlineRow(parent: HTMLElement): HTMLElement {
		const row = parent.createDiv();
		row.style.display = "flex";
		row.style.alignItems = "center";
		row.style.gap = "6px";
		row.style.margin = "4px 0";
		return row;
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
		this.formRefs = null;
		const heading = contentEl.createEl("h3", { text: `Manage issue #${this.ref.iid}` });
		heading.style.margin = "0 0 6px";
		this.renderTemplateSection(contentEl);
		this.renderCommentSection(contentEl);
		this.renderLabelsSection(contentEl);
		this.renderStateSection(contentEl);
	}

	private sectionLabel(parent: HTMLElement, text: string): HTMLElement {
		const el = parent.createEl("div", { text });
		el.style.fontSize = "12px";
		el.style.color = "var(--text-muted)";
		el.style.margin = "8px 0 2px";
		return el;
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
		ta.rows = 3;
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
		buttonRow.style.justifyContent = "flex-end";
		const postBtn = buttonRow.createEl("button", { text: "Post comment" });
		postBtn.type = "button";
		postBtn.classList.add("mod-cta");
		postBtn.addEventListener("click", async (e) => {
			e.preventDefault();
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

	private renderLabelsSection(parent: HTMLElement): void {
		this.sectionLabel(parent, "Labels");

		const currentEl = parent.createDiv();
		currentEl.style.fontSize = "12px";
		currentEl.style.margin = "0 0 4px";
		const renderCurrent = () => {
			currentEl.empty();
			currentEl.createEl("span", {
				text: "Current: ",
			}).style.color = "var(--text-muted)";
			currentEl.createEl("span", {
				text: this.labels.length > 0 ? this.labels.join(", ") : "(none)",
			});
		};
		renderCurrent();

		// Add row: input + dropdown picker
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

		// Remove row: input (wildcards) + dropdown picker (current labels)
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

	private async changeState(
		stateEvent: "close" | "reopen",
		btn: HTMLButtonElement,
		statusEl: HTMLElement
	): Promise<void> {
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

	onClose(): void {
		this.contentEl.empty();
	}
}

export interface NewIssueModalHooks {
	getKnownLabels?: () => string[];
	onLabelsLearned?: (labels: string[]) => void | Promise<void>;
	onCreated?: (created: { iid: number; web_url: string; project_id: number }) => void | Promise<void>;
}

export class NewIssueModal extends Modal {
	private projectId: string;
	private title = "";
	private description = "";
	private labelsValue = "";
	private confidential = false;
	private dueDate = "";

	constructor(
		app: App,
		private settings: GitlabIssuesSettings,
		defaultProjectId: string,
		private hooks: NewIssueModalHooks = {}
	) {
		super(app);
		this.projectId = defaultProjectId;
	}

	private inlineRow(parent: HTMLElement): HTMLElement {
		const row = parent.createDiv();
		row.style.display = "flex";
		row.style.alignItems = "center";
		row.style.gap = "6px";
		row.style.margin = "4px 0";
		return row;
	}

	private knownLabels(): string[] {
		return this.hooks.getKnownLabels ? this.hooks.getKnownLabels() ?? [] : [];
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.empty();
		const heading = contentEl.createEl("h3", { text: "Create Gitlab issue" });
		heading.style.margin = "0 0 6px";

		const projectRow = this.inlineRow(contentEl);
		projectRow.createEl("span", { text: "Project" }).style.minWidth = "5em";
		const projectInput = projectRow.createEl("input", { type: "text" });
		projectInput.placeholder = "group/project or numeric ID";
		projectInput.style.flex = "1";
		projectInput.value = this.projectId;
		projectInput.addEventListener("input", () => {
			this.projectId = projectInput.value.trim();
		});

		const titleRow = this.inlineRow(contentEl);
		titleRow.createEl("span", { text: "Title" }).style.minWidth = "5em";
		const titleInput = titleRow.createEl("input", { type: "text" });
		titleInput.placeholder = "Issue title (required)";
		titleInput.style.flex = "1";
		titleInput.addEventListener("input", () => {
			this.title = titleInput.value;
		});

		const descLabel = contentEl.createEl("div", { text: "Description" });
		descLabel.style.fontSize = "12px";
		descLabel.style.color = "var(--text-muted)";
		descLabel.style.margin = "6px 0 2px";
		const descTa = contentEl.createEl("textarea");
		descTa.rows = 5;
		descTa.style.width = "100%";
		descTa.placeholder = "Markdown body (optional)";
		descTa.addEventListener("input", () => {
			this.description = descTa.value;
		});

		const labelsRow = this.inlineRow(contentEl);
		labelsRow.createEl("span", { text: "Labels" }).style.minWidth = "5em";
		const labelsInput = labelsRow.createEl("input", { type: "text" });
		labelsInput.placeholder = "label1, label2 (custom OK)";
		labelsInput.style.flex = "1";
		labelsInput.addEventListener("input", () => {
			this.labelsValue = labelsInput.value;
		});
		renderLabelDropdown(labelsRow, "+ known", this.knownLabels(), (label) => {
			appendLabelToInput(labelsInput, label);
			this.labelsValue = labelsInput.value;
		});

		const optsRow = this.inlineRow(contentEl);
		const confidentialWrap = optsRow.createEl("label");
		confidentialWrap.style.display = "flex";
		confidentialWrap.style.alignItems = "center";
		confidentialWrap.style.gap = "4px";
		confidentialWrap.style.fontSize = "12px";
		const confidentialCb = confidentialWrap.createEl("input", { type: "checkbox" });
		confidentialCb.addEventListener("change", () => {
			this.confidential = confidentialCb.checked;
		});
		confidentialWrap.createEl("span", { text: "Confidential" });

		optsRow.createEl("span", { text: "Due" }).style.fontSize = "12px";
		const dueInput = optsRow.createEl("input", { type: "date" });
		dueInput.addEventListener("change", () => {
			this.dueDate = dueInput.value;
		});

		const buttons = this.inlineRow(contentEl);
		buttons.style.justifyContent = "flex-end";
		buttons.style.marginTop = "10px";

		const cancelBtn = buttons.createEl("button", { text: "Cancel" });
		cancelBtn.type = "button";
		cancelBtn.addEventListener("click", (e) => {
			e.preventDefault();
			this.close();
		});

		const submitBtn = buttons.createEl("button", { text: "Create issue" });
		submitBtn.type = "button";
		submitBtn.classList.add("mod-cta");
		submitBtn.addEventListener("click", async (e) => {
			e.preventDefault();
			if (!this.projectId.trim()) {
				new Notice("Project is required.");
				return;
			}
			if (!this.title.trim()) {
				new Notice("Title is required.");
				return;
			}
			const labels = splitLabelList(this.labelsValue);
			submitBtn.setAttr("disabled", "true");
			try {
				const created = await createIssue(this.settings, this.projectId.trim(), {
					title: this.title.trim(),
					description: this.description || undefined,
					labels: labels.length > 0 ? labels : undefined,
					confidential: this.confidential || undefined,
					due_date: this.dueDate || undefined,
				});
				if (this.hooks.onLabelsLearned && labels.length > 0) {
					try {
						await this.hooks.onLabelsLearned(labels);
					} catch (err: any) {
						logger(`Failed to record known labels: ${err.message}`);
					}
				}
				new Notice(`Created issue #${created.iid}`);
				this.close();
				if (this.hooks.onCreated) {
					await this.hooks.onCreated(created);
				}
			} catch (err: any) {
				logger(`Failed to create issue: ${err.message}`);
				new Notice(`Failed to create issue: ${err.message}`);
			} finally {
				submitBtn.removeAttribute("disabled");
			}
		});

		titleInput.focus();
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
