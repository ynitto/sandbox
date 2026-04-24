import { Plugin, Notice } from "obsidian";
import { DEFAULT_SETTINGS } from "./SettingsTab/settings";
import { JiraTasksSettingTab } from "./SettingsTab/settings-tab";
import { JiraTasksSettings } from "./SettingsTab/settings-types";
import JiraLoader from "./JiraLoader/jira-loader";
import Filesystem from "./filesystem";
import { logger } from "./utils/utils";

export default class JiraTasksPlugin extends Plugin {
	settings: JiraTasksSettings;
	private fs: Filesystem;
	private ribbonIconEl: HTMLElement;
	private refreshIntervalId: number | undefined;

	async onload() {
		await this.loadSettings();

		this.fs = new Filesystem(this.app.vault, this.settings);

		if (!this.settings.jiraApiToken || !this.settings.jiraEmail) {
			logger("Add your Jira URL, email, and API token to the plugin settings");
		} else {
			if (this.settings.showIcon) {
				this.ribbonIconEl = this.addRibbonIcon("cloud-download", "Import Jira Tasks", () => {
					this.fetchFromJira();
				});
			}

			this.addCommand({
				id: "jira-tasks-import",
				name: "Import Jira Tasks",
				callback: () => {
					this.fetchFromJira();
				},
			});

			this.fs.createOutputDirectory();
			this.scheduleAutomaticRefresh();
			this.refreshTasksAtStartup();
		}

		this.addSettingTab(new JiraTasksSettingTab(this.app, this));
	}

	onunload() {
		this.clearAutomaticRefresh();
	}

	async loadSettings() {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
		// Restore method lost after JSON serialization
		this.settings.jiraApiUrl = function () {
			return `${this.jiraUrl}/rest/api/3`;
		};
	}

	async saveSettings() {
		await this.saveData(this.settings);
	}

	scheduleAutomaticRefresh() {
		this.clearAutomaticRefresh();

		const intervalMinutes: any = this.settings.intervalOfRefresh;
		if (intervalMinutes === "off") return;

		const intervalMs = parseInt(intervalMinutes) * 60 * 1000;
		this.refreshIntervalId = window.setInterval(() => {
			this.fetchFromJira();
		}, intervalMs);
	}

	private clearAutomaticRefresh() {
		if (this.refreshIntervalId !== undefined) {
			clearInterval(this.refreshIntervalId);
			this.refreshIntervalId = undefined;
		}
	}

	private fetchFromJira() {
		new Notice("Fetching Jira tasks...");
		const loader = new JiraLoader(this.app, this.settings);
		loader.loadIssues();
	}

	private refreshTasksAtStartup() {
		if (this.settings.refreshOnStartup) {
			setTimeout(() => {
				this.fetchFromJira();
			}, 30000);
		}
	}
}
