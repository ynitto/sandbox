import { App, normalizePath, PluginSettingTab, Setting } from "obsidian";
import GitlabIssuesPlugin from "../main";
import { settings } from "./settings";
import { DropdownInputs } from "./settings-types";
import { splitLabelList } from "../IssueActions/actions";

export class GitlabIssuesSettingTab extends PluginSettingTab {
	plugin: GitlabIssuesPlugin;

	constructor(app: App, plugin: GitlabIssuesPlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display(): void {
		const { containerEl } = this;

		const { settingInputs, dropdowns, checkBoxInputs, gitlabDocumentation, getGitlabIssuesLevel, title } = settings;

		const issueSettingKeys = new Set(["gitlabUrl", "gitlabToken", "templateFile", "outputDir", "filter"]);
		const mrSettingKeys = new Set(["mrTemplateFile", "mrOutputDir", "mrFilter"]);
		const issueCheckboxKeys = new Set(["showIcon", "purgeIssues", "refreshOnStartup", "fetchDiscussions"]);
		const mrCheckboxKeys = new Set(["fetchMergeRequests", "fetchMrDiscussions", "fetchMrActivities", "fetchMrChanges"]);
		const connectionDropdownKeys = new Set(["intervalOfRefresh", "gitlabIssuesLevel"]);
		const issueDropdownKeys = new Set(["relatedMrMode"]);

		containerEl.empty();
		containerEl.createEl('h2', { text: title });

		// ── Connection ──
		containerEl.createEl('h3', { text: 'Connection' });
		settingInputs
			.filter(s => s.value === "gitlabUrl" || s.value === "gitlabToken")
			.forEach(setting => this.renderTextInput(containerEl, setting));

		dropdowns
			.filter(d => connectionDropdownKeys.has(d.value))
			.forEach(d => this.renderDropdown(containerEl, d));

		if (this.plugin.settings.gitlabIssuesLevel !== "personal") {
			const gitlabIssuesLevelIdObject = getGitlabIssuesLevel(this.plugin.settings.gitlabIssuesLevel);
			const descriptionDocumentFragment = document.createDocumentFragment();
			const descriptionLinkElement = descriptionDocumentFragment.createEl('a', {
				href: gitlabIssuesLevelIdObject.url,
				text: `Find your ${gitlabIssuesLevelIdObject.title} Id.`,
				title: `Goto ${gitlabIssuesLevelIdObject.url}`
			});
			descriptionDocumentFragment.appendChild(descriptionLinkElement);

			new Setting(containerEl)
				.setName(`Set Gitlab ${gitlabIssuesLevelIdObject.title} Id`)
				.setDesc(descriptionDocumentFragment)
				.addText(value => value
					.setValue(this.plugin.settings.gitlabAppId)
					.onChange(async (value: string) => {
						this.plugin.settings.gitlabAppId = value;
						await this.plugin.saveSettings();
					}));
		}

		// ── Issues ──
		containerEl.createEl('h3', { text: 'Issues' });
		settingInputs
			.filter(s => issueSettingKeys.has(s.value) && s.value !== "gitlabUrl" && s.value !== "gitlabToken")
			.forEach(setting => this.renderTextInput(containerEl, setting));

		checkBoxInputs
			.filter(c => issueCheckboxKeys.has(c.value))
			.forEach(checkboxSetting => this.renderCheckbox(containerEl, checkboxSetting));

		dropdowns
			.filter(d => issueDropdownKeys.has(d.value))
			.forEach(d => this.renderDropdown(containerEl, d));

		new Setting(containerEl)
			.setName('Max Items')
			.setDesc('Maximum total number of issues and merge requests to fetch (pages of 100 are fetched until this limit is reached)')
			.addText(text => text
				.setPlaceholder('20')
				.setValue(String(this.plugin.settings.maxItems))
				.onChange(async (value) => {
					const parsed = parseInt(value, 10);
					if (!isNaN(parsed) && parsed >= 1) {
						this.plugin.settings.maxItems = parsed;
						await this.plugin.saveSettings();
					}
				}));

		new Setting(containerEl)
			.setName('Skip items not updated in (days)')
			.setDesc('Skip issues and merge requests whose last update is older than this many days. Set to 0 to disable. Sent to the GitLab API as updated_after for efficiency.')
			.addText(text => text
				.setPlaceholder('0')
				.setValue(String(this.plugin.settings.staleDays))
				.onChange(async (value) => {
					const parsed = parseInt(value, 10);
					if (!isNaN(parsed) && parsed >= 0) {
						this.plugin.settings.staleDays = parsed;
						await this.plugin.saveSettings();
					}
				}));

		// ── Merge Requests ──
		containerEl.createEl('h3', { text: 'Merge Requests' });
		settingInputs
			.filter(s => mrSettingKeys.has(s.value))
			.forEach(setting => this.renderTextInput(containerEl, setting));

		checkBoxInputs
			.filter(c => mrCheckboxKeys.has(c.value))
			.forEach(checkboxSetting => this.renderCheckbox(containerEl, checkboxSetting));

		new Setting(containerEl)
			.setName('Max MR Items')
			.setDesc('Maximum total number of merge requests to fetch (pages of 100 are fetched until this limit is reached)')
			.addText(text => text
				.setPlaceholder('20')
				.setValue(String(this.plugin.settings.maxMrItems))
				.onChange(async (value) => {
					const parsed = parseInt(value, 10);
					if (!isNaN(parsed) && parsed >= 1) {
						this.plugin.settings.maxMrItems = parsed;
						await this.plugin.saveSettings();
					}
				}));

		this.renderLabelMappingsSection(containerEl);

		this.renderIssueActionTemplatesSection(containerEl);

		containerEl.createEl('h3', { text: 'More Information' });
		containerEl.createEl('a', {
			text: gitlabDocumentation.title,
			href: gitlabDocumentation.url
		});
	}

	private renderDropdown(containerEl: HTMLElement, dropdown: DropdownInputs): void {
		const key = dropdown.value;
		new Setting(containerEl)
			.setName(dropdown.title)
			.setDesc(dropdown.description)
			.addDropdown(value => value
				.addOptions(dropdown.options)
				.setValue(this.plugin.settings[key] as string)
				.onChange(async (newValue) => {
					(this.plugin.settings as any)[key] = newValue;
					if (key === 'intervalOfRefresh') {
						this.plugin.scheduleAutomaticRefresh();
					}
					await this.plugin.saveSettings();
					this.display();
				}));
	}

	private renderTextInput(containerEl: HTMLElement, setting: import("./settings-types").SettingInput): void {
		const handleSetValue = () => {
			if (setting.modifier === 'normalizePath') {
				return normalizePath(this.plugin.settings[setting.value] as string);
			}
			return this.plugin.settings[setting.value] as string;
		};

		new Setting(containerEl)
			.setName(setting.title)
			.setDesc(setting.description)
			.addText(text => text
				.setPlaceholder(setting.placeholder ?? "")
				.setValue(handleSetValue())
				.onChange(async (value) => {
					if (setting.modifier === "normalizePath") {
						(this.plugin.settings as any)[setting.value] = normalizePath(value);
					} else {
						(this.plugin.settings as any)[setting.value] = value;
					}
					await this.plugin.saveSettings();
				}));
	}

	private renderCheckbox(containerEl: HTMLElement, checkboxSetting: import("./settings-types").SettingCheckboxInput): void {
		const s = new Setting(containerEl)
			.setName(checkboxSetting.title)
			.addToggle(value => value
				.setValue(this.plugin.settings[checkboxSetting.value])
				.onChange(async (value) => {
					this.plugin.settings[checkboxSetting.value] = value;
					await this.plugin.saveSettings();
				}));
		if (checkboxSetting.description) {
			s.setDesc(checkboxSetting.description);
		}
	}

	private renderLabelMappingsSection(containerEl: HTMLElement): void {
		containerEl.createEl('h3', { text: 'Label Property Mappings' });
		containerEl.createEl('p', {
			cls: 'setting-item-description',
			text: 'Map issue labels to note properties. Rules are evaluated top-to-bottom; the first matching label wins. Computed properties are available as template variables (e.g. {{priority}}).',
		});

		const listEl = containerEl.createDiv();

		const refresh = () => {
			listEl.empty();
			this.plugin.settings.labelPropertyMappings.forEach((_, mi) => {
				this.renderMappingBlock(listEl, mi, refresh);
			});
		};

		refresh();

		new Setting(containerEl)
			.addButton(btn => btn
				.setButtonText('+ Add Mapping')
				.setCta()
				.onClick(async () => {
					this.plugin.settings.labelPropertyMappings.push({
						property: '',
						rules: [{ label: '', value: '' }],
					});
					await this.plugin.saveSettings();
					refresh();
				}));
	}

	private renderMappingBlock(container: HTMLElement, mi: number, refresh: () => void): void {
		const mapping = this.plugin.settings.labelPropertyMappings[mi];

		const card = container.createDiv();
		card.style.border = '1px solid var(--background-modifier-border)';
		card.style.borderRadius = '6px';
		card.style.padding = '8px 12px 4px';
		card.style.marginBottom = '12px';

		// Property name + default value + remove button
		const propSetting = new Setting(card)
			.setName(`Mapping ${mi + 1}`)
			.addText(text => {
				text.inputEl.style.width = '160px';
				return text
					.setPlaceholder('property name (e.g. priority)')
					.setValue(mapping.property)
					.onChange(async (v) => {
						this.plugin.settings.labelPropertyMappings[mi].property = v;
						await this.plugin.saveSettings();
					});
			})
			.addText(text => {
				text.inputEl.style.width = '130px';
				return text
					.setPlaceholder('default value (optional)')
					.setValue(mapping.default ?? '')
					.onChange(async (v) => {
						const m = this.plugin.settings.labelPropertyMappings[mi];
						if (v) {
							m.default = v;
						} else {
							delete m.default;
						}
						await this.plugin.saveSettings();
					});
			})
			.addButton(btn => btn
				.setIcon('trash')
				.setTooltip('Remove mapping')
				.onClick(async () => {
					this.plugin.settings.labelPropertyMappings.splice(mi, 1);
					await this.plugin.saveSettings();
					refresh();
				}));

		// Add label to each text input for clarity
		const controls = propSetting.controlEl.querySelectorAll('input[type="text"]');
		if (controls[0]) (controls[0] as HTMLElement).setAttribute('aria-label', 'Property name');
		if (controls[1]) (controls[1] as HTMLElement).setAttribute('aria-label', 'Default value');

		// Rules header
		const rulesHeader = card.createDiv();
		rulesHeader.style.marginTop = '8px';
		rulesHeader.style.marginBottom = '4px';
		rulesHeader.style.paddingLeft = '4px';

		const headerRow = rulesHeader.createDiv();
		headerRow.style.display = 'flex';
		headerRow.style.gap = '8px';
		headerRow.style.color = 'var(--text-muted)';
		headerRow.style.fontSize = '12px';
		headerRow.style.paddingRight = '36px';

		const labelCol = headerRow.createSpan({ text: 'Label' });
		labelCol.style.flex = '1';
		labelCol.style.minWidth = '160px';

		headerRow.createSpan({ text: '→' });

		const valueCol = headerRow.createSpan({ text: 'Value' });
		valueCol.style.flex = '1';
		valueCol.style.minWidth = '100px';

		// Rule rows
		const rulesEl = card.createDiv();
		rulesEl.style.paddingLeft = '4px';

		mapping.rules.forEach((rule, ri) => {
			const ruleSetting = new Setting(rulesEl);
			ruleSetting.settingEl.style.borderTop = 'none';
			ruleSetting.settingEl.style.padding = '4px 0';

			ruleSetting.addText(text => {
				text.inputEl.style.width = '200px';
				return text
					.setPlaceholder('label (e.g. priority::high)')
					.setValue(rule.label)
					.onChange(async (v) => {
						this.plugin.settings.labelPropertyMappings[mi].rules[ri].label = v;
						await this.plugin.saveSettings();
					});
			});

			// Arrow between label and value
			ruleSetting.controlEl.createSpan({ text: '→' }).style.margin = '0 6px';

			ruleSetting.addText(text => {
				text.inputEl.style.width = '120px';
				return text
					.setPlaceholder('value')
					.setValue(rule.value)
					.onChange(async (v) => {
						this.plugin.settings.labelPropertyMappings[mi].rules[ri].value = v;
						await this.plugin.saveSettings();
					});
			});

			ruleSetting.addButton(btn => btn
				.setIcon('x')
				.setTooltip('Remove rule')
				.onClick(async () => {
					this.plugin.settings.labelPropertyMappings[mi].rules.splice(ri, 1);
					await this.plugin.saveSettings();
					refresh();
				}));
		});

		// Add rule button
		new Setting(card)
			.addButton(btn => btn
				.setButtonText('+ Add Rule')
				.onClick(async () => {
					this.plugin.settings.labelPropertyMappings[mi].rules.push({ label: '', value: '' });
					await this.plugin.saveSettings();
					refresh();
				}));
	}

	private renderIssueActionTemplatesSection(containerEl: HTMLElement): void {
		containerEl.createEl('h3', { text: 'Issue Action Templates' });
		containerEl.createEl('p', {
			cls: 'setting-item-description',
			text: 'Define re-usable bundles of comment body + label add/remove/replace. Apply via the "Apply template to active Gitlab issue" command.',
		});

		const listEl = containerEl.createDiv();

		const refresh = () => {
			listEl.empty();
			const templates = this.plugin.settings.issueActionTemplates ?? [];
			templates.forEach((_, i) => this.renderTemplateBlock(listEl, i, refresh));
		};

		refresh();

		new Setting(containerEl)
			.addButton(btn => btn
				.setButtonText('+ Add Template')
				.setCta()
				.onClick(async () => {
					if (!this.plugin.settings.issueActionTemplates) {
						this.plugin.settings.issueActionTemplates = [];
					}
					this.plugin.settings.issueActionTemplates.push({
						id: `tmpl-${Date.now()}`,
						name: 'New Template',
					});
					await this.plugin.saveSettings();
					refresh();
				}));
	}

	private renderTemplateBlock(container: HTMLElement, idx: number, refresh: () => void): void {
		const tmpl = this.plugin.settings.issueActionTemplates![idx];

		const card = container.createDiv();
		card.style.border = '1px solid var(--background-modifier-border)';
		card.style.borderRadius = '6px';
		card.style.padding = '8px 12px';
		card.style.marginBottom = '12px';

		new Setting(card)
			.setName(`Template ${idx + 1}`)
			.addText(text => text
				.setPlaceholder('display name')
				.setValue(tmpl.name)
				.onChange(async (v) => {
					this.plugin.settings.issueActionTemplates![idx].name = v;
					await this.plugin.saveSettings();
				}))
			.addButton(btn => btn
				.setIcon('trash')
				.setTooltip('Delete template')
				.onClick(async () => {
					this.plugin.settings.issueActionTemplates!.splice(idx, 1);
					await this.plugin.saveSettings();
					refresh();
				}));

		new Setting(card)
			.setName('Comment body')
			.setDesc('Leave empty to skip posting a comment. Editable at apply time.')
			.addTextArea(ta => {
				ta.inputEl.rows = 4;
				ta.inputEl.style.width = '100%';
				return ta
					.setValue(tmpl.commentBody ?? '')
					.onChange(async (v) => {
						const t = this.plugin.settings.issueActionTemplates![idx];
						if (v) {
							t.commentBody = v;
						} else {
							delete t.commentBody;
						}
						await this.plugin.saveSettings();
					});
			});

		new Setting(card)
			.setName('Add labels')
			.setDesc('Comma-separated. Ignored when Replace is enabled.')
			.addText(text => text
				.setPlaceholder('bug, priority::high')
				.setValue((tmpl.labelsAdd ?? []).join(', '))
				.onChange(async (v) => {
					const t = this.plugin.settings.issueActionTemplates![idx];
					const list = splitLabelList(v);
					if (list.length > 0) {
						t.labelsAdd = list;
					} else {
						delete t.labelsAdd;
					}
					await this.plugin.saveSettings();
				}));

		new Setting(card)
			.setName('Remove labels')
			.setDesc('Comma-separated. Ignored when Replace is enabled.')
			.addText(text => text
				.setPlaceholder('triage')
				.setValue((tmpl.labelsRemove ?? []).join(', '))
				.onChange(async (v) => {
					const t = this.plugin.settings.issueActionTemplates![idx];
					const list = splitLabelList(v);
					if (list.length > 0) {
						t.labelsRemove = list;
					} else {
						delete t.labelsRemove;
					}
					await this.plugin.saveSettings();
				}));

		const replaceEnabled = tmpl.labelsReplace !== undefined;
		new Setting(card)
			.setName('Replace labels')
			.setDesc('Replaces ALL existing labels with the list below. Empty list clears all labels. Overrides Add/Remove.')
			.addToggle(toggle => toggle
				.setValue(replaceEnabled)
				.onChange(async (v) => {
					const t = this.plugin.settings.issueActionTemplates![idx];
					if (v) {
						t.labelsReplace = t.labelsReplace ?? [];
					} else {
						delete t.labelsReplace;
					}
					await this.plugin.saveSettings();
					refresh();
				}));

		if (replaceEnabled) {
			new Setting(card)
				.setName('Replacement labels')
				.setDesc('Comma-separated.')
				.addText(text => text
					.setPlaceholder('label1, label2')
					.setValue((tmpl.labelsReplace ?? []).join(', '))
					.onChange(async (v) => {
						this.plugin.settings.issueActionTemplates![idx].labelsReplace = splitLabelList(v);
						await this.plugin.saveSettings();
					}));
		}
	}
}
