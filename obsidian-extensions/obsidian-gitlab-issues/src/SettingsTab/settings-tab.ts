import { App, normalizePath, PluginSettingTab, Setting } from "obsidian";
import GitlabIssuesPlugin from "../main";
import { settings } from "./settings";
import { GitlabIssuesLevel, GitlabRefreshInterval, LabelPropertyMapping } from "./settings-types";

const LABEL_MAPPING_PLACEHOLDER: LabelPropertyMapping[] = [
	{
		property: "priority",
		default: "none",
		rules: [
			{ label: "priority::high", value: "high" },
			{ label: "priority::medium", value: "medium" },
			{ label: "priority::low", value: "low" },
		],
	},
	{
		property: "type",
		rules: [
			{ label: "bug", value: "bug" },
			{ label: "feature", value: "feature" },
		],
	},
];

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

		containerEl.createEl('h3', { text: 'Label Property Mappings' });
		containerEl.createEl('p', {
			text: 'Map issue labels to note properties using switch-like rules. The first matching label wins. Computed properties are available as template variables (e.g. {{priority}}).',
		});

		new Setting(containerEl)
			.setName('Mappings (JSON)')
			.setDesc('Array of mapping objects. Each mapping sets one property based on matching labels.')
			.addTextArea(text => {
				text
					.setPlaceholder(JSON.stringify(LABEL_MAPPING_PLACEHOLDER, null, 2))
					.setValue(
						this.plugin.settings.labelPropertyMappings.length > 0
							? JSON.stringify(this.plugin.settings.labelPropertyMappings, null, 2)
							: ''
					)
					.onChange(async (value) => {
						if (value.trim() === '') {
							this.plugin.settings.labelPropertyMappings = [];
							await this.plugin.saveSettings();
							return;
						}
						try {
							const parsed = JSON.parse(value);
							if (Array.isArray(parsed)) {
								this.plugin.settings.labelPropertyMappings = parsed;
								await this.plugin.saveSettings();
							}
						} catch (_e) {
							// Ignore invalid JSON until the user finishes editing
						}
					});
				text.inputEl.rows = 12;
				text.inputEl.style.width = '100%';
				text.inputEl.style.fontFamily = 'monospace';
				text.inputEl.style.fontSize = '12px';
				return text;
			});

		containerEl.createEl('h3', { text: 'More Information' });
		containerEl.createEl('a', {
			text: gitlabDocumentation.title,
			href: gitlabDocumentation.url
		});
	}
}
