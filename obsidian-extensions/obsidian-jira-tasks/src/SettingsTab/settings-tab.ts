import { App, normalizePath, PluginSettingTab, Setting } from "obsidian";
import JiraTasksPlugin from "../main";
import { settingInputs, dropdownInputs, checkboxInputs } from "./settings";

export class JiraTasksSettingTab extends PluginSettingTab {
	plugin: JiraTasksPlugin;

	constructor(app: App, plugin: JiraTasksPlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		containerEl.createEl('h2', { text: 'Jira Tasks' });

		this.renderConnectionSection(containerEl);
		this.renderBehaviorSection(containerEl);
		this.renderLabelMappingsSection(containerEl);
		this.renderMoreInfoSection(containerEl);
	}

	private renderConnectionSection(containerEl: HTMLElement): void {
		containerEl.createEl('h3', { text: 'Connection' });

		settingInputs.forEach((setting) => {
			const s = new Setting(containerEl)
				.setName(setting.title)
				.setDesc(setting.description);

			s.addText(text => {
				if (setting.type === 'password') {
					text.inputEl.type = 'password';
				}

				const getValue = () => {
					const raw = this.plugin.settings[setting.value] as string;
					return setting.modifier === 'normalizePath' ? normalizePath(raw) : raw;
				};

				return text
					.setPlaceholder(setting.placeholder ?? '')
					.setValue(getValue())
					.onChange(async (value) => {
						(this.plugin.settings as any)[setting.value] =
							setting.modifier === 'normalizePath' ? normalizePath(value) : value;
						await this.plugin.saveSettings();
					});
			});
		});

		new Setting(containerEl)
			.setName('Max Results')
			.setDesc('Maximum number of Jira tasks to import per sync (1–100)')
			.addText(text => text
				.setPlaceholder('50')
				.setValue(String(this.plugin.settings.maxResults))
				.onChange(async (value) => {
					const parsed = parseInt(value, 10);
					if (!isNaN(parsed) && parsed > 0 && parsed <= 100) {
						this.plugin.settings.maxResults = parsed;
						await this.plugin.saveSettings();
					}
				}));
	}

	private renderBehaviorSection(containerEl: HTMLElement): void {
		containerEl.createEl('h3', { text: 'Behavior' });

		dropdownInputs.forEach((dropdown) => {
			new Setting(containerEl)
				.setName(dropdown.title)
				.setDesc(dropdown.description)
				.addDropdown(select => select
					.addOptions(dropdown.options)
					.setValue(this.plugin.settings[dropdown.value])
					.onChange(async (value) => {
						(this.plugin.settings as any)[dropdown.value] = value;
						this.plugin.scheduleAutomaticRefresh();
						await this.plugin.saveSettings();
					}));
		});

		checkboxInputs.forEach((checkboxSetting) => {
			const s = new Setting(containerEl)
				.setName(checkboxSetting.title)
				.addToggle(toggle => toggle
					.setValue(this.plugin.settings[checkboxSetting.value])
					.onChange(async (value) => {
						this.plugin.settings[checkboxSetting.value] = value;
						await this.plugin.saveSettings();
					}));
			if (checkboxSetting.description) {
				s.setDesc(checkboxSetting.description);
			}
		});
	}

	private renderLabelMappingsSection(containerEl: HTMLElement): void {
		containerEl.createEl('h3', { text: 'Label Property Mappings' });
		containerEl.createEl('p', {
			cls: 'setting-item-description',
			text: 'Map Jira labels to note properties. Rules are evaluated top-to-bottom; the first matching label wins. Computed properties are available in templates (e.g. {{priority}}).',
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

		const propSetting = new Setting(card)
			.setName(`Mapping ${mi + 1}`)
			.addText(text => {
				text.inputEl.style.width = '160px';
				return text
					.setPlaceholder('property name (e.g. category)')
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

		const controls = propSetting.controlEl.querySelectorAll('input[type="text"]');
		if (controls[0]) (controls[0] as HTMLElement).setAttribute('aria-label', 'Property name');
		if (controls[1]) (controls[1] as HTMLElement).setAttribute('aria-label', 'Default value');

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

		const rulesEl = card.createDiv();
		rulesEl.style.paddingLeft = '4px';

		mapping.rules.forEach((rule, ri) => {
			const ruleSetting = new Setting(rulesEl);
			ruleSetting.settingEl.style.borderTop = 'none';
			ruleSetting.settingEl.style.padding = '4px 0';

			ruleSetting.addText(text => {
				text.inputEl.style.width = '200px';
				return text
					.setPlaceholder('label name')
					.setValue(rule.label)
					.onChange(async (v) => {
						this.plugin.settings.labelPropertyMappings[mi].rules[ri].label = v;
						await this.plugin.saveSettings();
					});
			});

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

		new Setting(card)
			.addButton(btn => btn
				.setButtonText('+ Add Rule')
				.onClick(async () => {
					this.plugin.settings.labelPropertyMappings[mi].rules.push({ label: '', value: '' });
					await this.plugin.saveSettings();
					refresh();
				}));
	}

	private renderMoreInfoSection(containerEl: HTMLElement): void {
		containerEl.createEl('h3', { text: 'More Information' });

		const links = [
			{ text: 'Jira REST API Documentation', href: 'https://developer.atlassian.com/cloud/jira/platform/rest/v3/' },
			{ text: 'JQL Reference', href: 'https://support.atlassian.com/jira-service-management-cloud/docs/use-advanced-search-with-jira-query-language-jql/' },
			{ text: 'Create an Atlassian API Token', href: 'https://id.atlassian.com/manage-profile/security/api-tokens' },
		];

		links.forEach(link => {
			const p = containerEl.createEl('p');
			p.createEl('a', { text: link.text, href: link.href });
		});
	}
}
