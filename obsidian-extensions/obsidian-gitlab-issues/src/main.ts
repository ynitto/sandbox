import { Plugin, Notice } from "obsidian";
import { DEFAULT_SETTINGS } from "./SettingsTab/settings";
import { GitlabIssuesSettingTab } from "./SettingsTab/settings-tab";
import { GitlabIssuesSettings } from "./SettingsTab/settings-types";
import GitlabLoader from "./GitlabLoader/gitlab-loader";
import MergeRequestLoader from "./GitlabLoader/merge-request-loader";
import Filesystem from "./filesystem";
import { logger } from "./utils/utils";
import {
	getActiveIssueRef,
	setIssueState,
	updateNoteFrontmatter,
	executeIssueActionTemplate,
	moveIssueFileForState,
} from "./IssueActions/actions";
import {
	ConfirmModal,
	IssueActionsModal,
	TemplateSuggestModal,
	TemplatePreviewModal,
} from "./IssueActions/modals";

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

			this.addCommand({
				id: "gitlab-issues-manage",
				name: "Manage active Gitlab issue (comment & labels)",
				callback: () => this.manageActiveIssue(),
			});

			this.addCommand({
				id: "gitlab-issues-close",
				name: "Close active Gitlab issue",
				callback: () => this.changeActiveIssueState("close"),
			});

			this.addCommand({
				id: "gitlab-issues-reopen",
				name: "Reopen active Gitlab issue",
				callback: () => this.changeActiveIssueState("reopen"),
			});

			this.addCommand({
				id: "gitlab-issues-apply-template",
				name: "Apply template to active Gitlab issue",
				callback: () => this.applyTemplateToActiveIssue(),
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
		const loaded = (await this.loadData()) as Record<string, any> | null;
		this.settings = Object.assign({}, DEFAULT_SETTINGS, loaded ?? {}) as GitlabIssuesSettings;

		// Migration: collapse the old three booleans into the single relatedMrMode dropdown.
		if (loaded && loaded.relatedMrMode === undefined) {
			if (loaded.embedRelatedMrDetails) {
				this.settings.relatedMrMode = "same";
			} else if (loaded.createRelatedMrFiles) {
				this.settings.relatedMrMode = "separate";
			} else {
				this.settings.relatedMrMode = "off";
			}
		}
		delete (this.settings as any).fetchRelatedMergeRequests;
		delete (this.settings as any).createRelatedMrFiles;
		delete (this.settings as any).embedRelatedMrDetails;
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

	private resolveActiveIssue() {
		const ctx = getActiveIssueRef(this.app);
		if (!ctx) {
			new Notice(
				"Could not identify a Gitlab issue from the active note. Frontmatter must contain projectId+iid or webUrl."
			);
			return null;
		}
		return ctx;
	}

	private manageActiveIssue() {
		const ctx = this.resolveActiveIssue();
		if (!ctx) return;
		new IssueActionsModal(this.app, this.settings, ctx.ref, ctx.file, ctx.frontmatter).open();
	}

	private applyTemplateToActiveIssue() {
		const ctx = this.resolveActiveIssue();
		if (!ctx) return;

		const templates = this.settings.issueActionTemplates ?? [];
		if (templates.length === 0) {
			new Notice("No issue action templates configured. Add some in plugin settings.");
			return;
		}

		new TemplateSuggestModal(this.app, templates, (template) => {
			new TemplatePreviewModal(this.app, template, ctx.ref.iid, async (commentBody) => {
				try {
					await executeIssueActionTemplate(
						this.app,
						this.settings,
						ctx.ref,
						ctx.file,
						template,
						commentBody
					);
					new Notice(`Applied "${template.name}" to issue #${ctx.ref.iid}`);
				} catch (e: any) {
					logger(`Failed to apply template: ${e.message}`);
					new Notice(`Failed to apply template: ${e.message}`);
				}
			}).open();
		}).open();
	}

	private changeActiveIssueState(stateEvent: "close" | "reopen") {
		const ctx = this.resolveActiveIssue();
		if (!ctx) return;
		const verb = stateEvent === "close" ? "Close" : "Reopen";

		new ConfirmModal(this.app, {
			title: `${verb} issue #${ctx.ref.iid}`,
			message: `${verb} issue #${ctx.ref.iid} on Gitlab?`,
			submitText: verb,
			onConfirm: async () => {
				try {
					const state = await setIssueState(this.settings, ctx.ref, stateEvent);
					await updateNoteFrontmatter(this.app, ctx.file, { state });
					await moveIssueFileForState(this.app, ctx.file, state);
					new Notice(`Issue #${ctx.ref.iid} ${state}`);
				} catch (e: any) {
					logger(`Failed to ${verb.toLowerCase()} issue: ${e.message}`);
					new Notice(`Failed to ${verb.toLowerCase()} issue: ${e.message}`);
				}
			},
		}).open();
	}
}
