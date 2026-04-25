import { Plugin, Notice } from "obsidian";
import { DEFAULT_SETTINGS } from "./SettingsTab/settings";
import { GitlabIssuesSettingTab } from "./SettingsTab/settings-tab";
import { GitlabIssuesSettings } from "./SettingsTab/settings-types";
import GitlabLoader from "./GitlabLoader/gitlab-loader";
import MergeRequestLoader from "./GitlabLoader/merge-request-loader";
import Filesystem from "./filesystem";
import { logger } from "./utils/utils";

export default class GitlabIssuesPlugin extends Plugin {
	settings: GitlabIssuesSettings;
	private fs: Filesystem;
	private ribbonIconEl: HTMLElement;
	private refreshIntervalId: number | undefined;

	async onload() {
		await this.loadSettings();

		this.fs = new Filesystem(this.app.vault, this.settings);

		if (!this.settings.gitlabToken) {
			logger("Add your Gitlab Personal Token to the plugin settings");
		} else {
			if (this.settings.showIcon) {
				this.ribbonIconEl = this.addRibbonIcon("cloud-download", "Import Gitlab Issues & Merge Requests", () => {
					this.fetchIssuesFromGitlab();
					this.fetchMergeRequestsFromGitlab();
				});
			}

			this.addCommand({
				id: "gitlab-issues-open",
				name: "Import Gitlab Issues",
				callback: () => {
					this.fetchIssuesFromGitlab();
				},
			});

			this.addCommand({
				id: "gitlab-merge-requests-open",
				name: "Import Gitlab Merge Requests",
				callback: () => {
					this.fetchMergeRequestsFromGitlab();
				},
			});

			this.fs.createOutputDirectory();
			this.scheduleAutomaticRefresh();
			this.refreshIssuesAtStartup();
		}

		this.addSettingTab(new GitlabIssuesSettingTab(this.app, this));
	}

	onunload() {
		this.clearAutomaticRefresh();
	}

	async loadSettings() {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
	}

	async saveSettings() {
		await this.saveData(this.settings);
	}

	scheduleAutomaticRefresh() {
		this.clearAutomaticRefresh();

		const intervalMinutes: any = this.settings.intervalOfRefresh;

		if (intervalMinutes === "off") {
			return;
		}

		const intervalMs = parseInt(intervalMinutes) * 60 * 1000;

		this.refreshIntervalId = window.setInterval(() => {
			this.fetchIssuesFromGitlab();
		}, intervalMs);
	}

	private clearAutomaticRefresh() {
		if (this.refreshIntervalId !== undefined) {
			clearInterval(this.refreshIntervalId);
			this.refreshIntervalId = undefined;
		}
	}

	private fetchIssuesFromGitlab() {
		new Notice("Fetching Gitlab issues...");
		const loader = new GitlabLoader(this.app, this.settings);
		loader.loadIssues();
	}

	private fetchMergeRequestsFromGitlab() {
		if (!this.settings.fetchMergeRequests) {
			new Notice("Enable 'Import Merge Requests' in settings to use this command.");
			return;
		}
		new Notice("Fetching Gitlab merge requests...");
		const loader = new MergeRequestLoader(this.app, this.settings);
		loader.loadMergeRequests();
	}

	private refreshIssuesAtStartup() {
		if (this.settings.refreshOnStartup) {
			setTimeout(() => {
				this.fetchIssuesFromGitlab();
			}, 30000);
		}
	}
}
