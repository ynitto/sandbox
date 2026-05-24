import { App, Modal, Notice, Setting, TFile } from "obsidian";
import { GitlabIssuesSettings } from "../SettingsTab/settings-types";
import {
	IssueRef,
	splitLabelList,
} from "./actions";
import { createIssue } from "./actions";
import {
	IssueActionsForm,
	IssueActionsFormHooks,
	renderLabelDropdown,
	appendLabelToInput,
} from "./form";
import { logger } from "../utils/utils";

// Re-export for backwards-compatible imports.
export type IssueActionsModalHooks = IssueActionsFormHooks;

export class IssueActionsModal extends Modal {
	private form: IssueActionsForm;

	constructor(
		app: App,
		settings: GitlabIssuesSettings,
		private ref: IssueRef,
		private file: TFile,
		private frontmatter: Record<string, any>,
		hooks: IssueActionsFormHooks = {}
	) {
		super(app);
		this.form = new IssueActionsForm(app, settings, hooks);
	}

	onOpen(): void {
		this.form.render(this.contentEl, {
			file: this.file,
			ref: this.ref,
			frontmatter: this.frontmatter,
		});
	}

	onClose(): void {
		this.contentEl.empty();
	}
}


export interface NewIssueModalHooks {
	getKnownLabels?: () => string[];
	getKnownProjects?: () => string[];
	onLabelsLearned?: (labels: string[]) => void | Promise<void>;
	onProjectLearned?: (project: string) => void | Promise<void>;
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
		const knownProjects = this.hooks.getKnownProjects ? this.hooks.getKnownProjects() ?? [] : [];
		renderLabelDropdown(projectRow, "+ known", knownProjects, (project) => {
			projectInput.value = project;
			this.projectId = project;
			projectInput.focus();
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
		descTa.rows = 10;
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
				if (this.hooks.onProjectLearned) {
					try {
						await this.hooks.onProjectLearned(this.projectId.trim());
					} catch (err: any) {
						logger(`Failed to record known project: ${err.message}`);
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
