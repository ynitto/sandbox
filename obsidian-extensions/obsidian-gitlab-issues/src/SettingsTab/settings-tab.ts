import { App, normalizePath, PluginSettingTab, Setting } from "obsidian";
import GitlabIssuesPlugin from "../main";
import { settings } from "./settings";
import { GitlabIssuesLevel, GitlabRefreshInterval } from "./settings-types";

export class GitlabIssuesSettingTab extends PluginSettingTab {
	plugin: GitlabIssuesPlugin;

	constructor(app: App, plugin: GitlabIssuesPlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display(): void {
		const { containerEl } = this;

		const { settingInputs, dropdowns, checkBoxInputs, gitlabDocumentation, getGitlabIssuesLevel, title } = settings;

		containerEl.empty();
		containerEl.createEl('h2', { text: title });

		settingInputs.forEach((setting) => {
			const handleSetValue = () => {
				if (setting.modifier === 'normalizePath') {
					return normalizePath(this.plugin.settings[setting.value]);
				}
				return this.plugin.settings[setting.value];
			};

			new Setting(containerEl)
				.setName(setting.title)
				.setDesc(setting.description)
				.addText(text => text
					.setPlaceholder(setting.placeholder ?? "")
					.setValue(handleSetValue())
					.onChange(async (value) => {
						if (setting.modifier === "normalizePath") {
							this.plugin.settings[setting.value] = normalizePath(value);
						} else {
							this.plugin.settings[setting.value] = value;
						}
						await this.plugin.saveSettings();
					}));
		});

		dropdowns.forEach((dropdown) => {
			const currentValue = dropdown.value;

			new Setting(containerEl)
				.setName(dropdown.title)
				.setDesc(dropdown.description)
				.addDropdown(value => value
					.addOptions(dropdown.options)
					.setValue(this.plugin.settings[currentValue])
					.onChange(async (value) => {
						if (currentValue === 'gitlabIssuesLevel') {
							this.plugin.settings[currentValue] = value as GitlabIssuesLevel;
						} else {
							this.plugin.settings[currentValue] = value as GitlabRefreshInterval;
							this.plugin.scheduleAutomaticRefresh();
						}
						await this.plugin.saveSettings();
						this.display();
					}));
		});

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

		checkBoxInputs.forEach(checkboxSetting => {
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
		});

		this.renderLabelMappingsSection(containerEl);

		containerEl.createEl('h3', { text: 'More Information' });
		containerEl.createEl('a', {
			text: gitlabDocumentation.title,
			href: gitlabDocumentation.url
		});
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
}
