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
		const loaded = (await this.loadData()) as Record<string, any> | null;
		this.settings = Object.assign({}, DEFAULT_SETTINGS, loaded ?? {}) as JiraTasksSettings;
		// Restore method lost after JSON serialization
		this.settings.jiraApiUrl = function () {
			return `${this.jiraUrl}/rest/api/3`;
		};

		// Load Jira credentials from the secrets sidecar (kept out of data.json
		// so the rest of the settings can be shared via git).
		const secrets = await this.readSecrets();
		const dataJsonEmail =
			typeof loaded?.jiraEmail === "string" ? loaded.jiraEmail : "";
		const dataJsonToken =
			typeof loaded?.jiraApiToken === "string" ? loaded.jiraApiToken : "";

		let needsRewrite = false;
		if (secrets) {
			if (typeof secrets.jiraEmail === "string") this.settings.jiraEmail = secrets.jiraEmail;
			if (typeof secrets.jiraApiToken === "string") this.settings.jiraApiToken = secrets.jiraApiToken;
			if (dataJsonEmail || dataJsonToken) needsRewrite = true; // strip stale fields
		} else if (dataJsonEmail || dataJsonToken) {
			// One-time migration: move existing credentials out of data.json.
			this.settings.jiraEmail = dataJsonEmail;
			this.settings.jiraApiToken = dataJsonToken;
			needsRewrite = true;
		}

		if (needsRewrite) {
			await this.saveSettings();
		}
	}

	async saveSettings() {
		await this.writeSecrets({
			jiraEmail: this.settings.jiraEmail ?? "",
			jiraApiToken: this.settings.jiraApiToken ?? "",
		});
		const { jiraEmail: _e, jiraApiToken: _t, ...shared } =
			this.settings as Record<string, any>;
		await this.saveData(shared);
	}

	private secretsPath(): string {
		return `${this.app.vault.configDir}/plugins/${this.manifest.id}/data.secrets.json`;
	}

	private async readSecrets(): Promise<Record<string, any> | null> {
		try {
			const path = this.secretsPath();
			if (!(await this.app.vault.adapter.exists(path))) return null;
			const raw = await this.app.vault.adapter.read(path);
			return JSON.parse(raw) as Record<string, any>;
		} catch (e: any) {
			logger(`Failed to read secrets sidecar: ${e.message}`);
			return null;
		}
	}

	private async writeSecrets(secrets: Record<string, string>): Promise<void> {
		try {
			const path = this.secretsPath();
			await this.app.vault.adapter.write(path, JSON.stringify(secrets, null, 2));
		} catch (e: any) {
			logger(`Failed to write secrets sidecar: ${e.message}`);
		}
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
