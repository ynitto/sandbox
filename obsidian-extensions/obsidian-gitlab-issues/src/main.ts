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
	postIssueComment,
	updateIssueLabels,
	setIssueState,
	updateNoteFrontmatter,
	splitLabelList,
} from "./IssueActions/actions";
import { TextInputModal, LabelMultiSelectModal, ConfirmModal } from "./IssueActions/modals";

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
				id: "gitlab-issues-add-comment",
				name: "Post comment to active Gitlab issue",
				callback: () => this.commentOnActiveIssue(),
			});

			this.addCommand({
				id: "gitlab-issues-add-label",
				name: "Add label to active Gitlab issue",
				callback: () => this.addLabelToActiveIssue(),
			});

			this.addCommand({
				id: "gitlab-issues-remove-label",
				name: "Remove label from active Gitlab issue",
				callback: () => this.removeLabelFromActiveIssue(),
			});

			this.addCommand({
				id: "gitlab-issues-set-labels",
				name: "Set (replace) labels on active Gitlab issue",
				callback: () => this.replaceLabelsOnActiveIssue(),
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

	private currentLabels(fm: Record<string, any>): string[] {
		const raw = fm.labels;
		if (Array.isArray(raw)) return raw.map((s) => String(s));
		if (typeof raw === "string" && raw.length > 0) return splitLabelList(raw);
		return [];
	}

	private commentOnActiveIssue() {
		const ctx = this.resolveActiveIssue();
		if (!ctx) return;

		new TextInputModal(this.app, {
			title: `Comment on issue #${ctx.ref.iid}`,
			placeholder: "Write a comment in Markdown...",
			multiline: true,
			submitText: "Post",
			onSubmit: async (body) => {
				try {
					await postIssueComment(this.settings, ctx.ref, body);
					new Notice(`Comment posted to issue #${ctx.ref.iid}`);
				} catch (e: any) {
					logger(`Failed to post comment: ${e.message}`);
					new Notice(`Failed to post comment: ${e.message}`);
				}
			},
		}).open();
	}

	private addLabelToActiveIssue() {
		const ctx = this.resolveActiveIssue();
		if (!ctx) return;

		new TextInputModal(this.app, {
			title: `Add label to issue #${ctx.ref.iid}`,
			description: "Comma-separated labels are supported.",
			placeholder: "label-name or label1,label2",
			submitText: "Add",
			onSubmit: async (value) => {
				const toAdd = splitLabelList(value);
				if (toAdd.length === 0) return;
				try {
					const updated = await updateIssueLabels(this.settings, ctx.ref, { add: toAdd });
					await updateNoteFrontmatter(this.app, ctx.file, { labels: updated });
					new Notice(`Added label(s) to issue #${ctx.ref.iid}`);
				} catch (e: any) {
					logger(`Failed to add labels: ${e.message}`);
					new Notice(`Failed to add labels: ${e.message}`);
				}
			},
		}).open();
	}

	private removeLabelFromActiveIssue() {
		const ctx = this.resolveActiveIssue();
		if (!ctx) return;
		const current = this.currentLabels(ctx.frontmatter);

		new LabelMultiSelectModal(this.app, {
			title: `Remove labels from issue #${ctx.ref.iid}`,
			description: "Tick labels to remove.",
			labels: current,
			submitText: "Remove",
			onSubmit: async (toRemove) => {
				try {
					const updated = await updateIssueLabels(this.settings, ctx.ref, { remove: toRemove });
					await updateNoteFrontmatter(this.app, ctx.file, { labels: updated });
					new Notice(`Removed label(s) from issue #${ctx.ref.iid}`);
				} catch (e: any) {
					logger(`Failed to remove labels: ${e.message}`);
					new Notice(`Failed to remove labels: ${e.message}`);
				}
			},
		}).open();
	}

	private replaceLabelsOnActiveIssue() {
		const ctx = this.resolveActiveIssue();
		if (!ctx) return;
		const current = this.currentLabels(ctx.frontmatter);

		new TextInputModal(this.app, {
			title: `Set labels on issue #${ctx.ref.iid}`,
			description: "Comma-separated. The list fully replaces existing labels (empty value clears all labels).",
			placeholder: "label1, label2",
			defaultValue: current.join(", "),
			submitText: "Apply",
			onSubmit: async (value) => {
				const replace = splitLabelList(value);
				try {
					const updated = await updateIssueLabels(this.settings, ctx.ref, { replace });
					await updateNoteFrontmatter(this.app, ctx.file, { labels: updated });
					new Notice(`Labels updated on issue #${ctx.ref.iid}`);
				} catch (e: any) {
					logger(`Failed to set labels: ${e.message}`);
					new Notice(`Failed to set labels: ${e.message}`);
				}
			},
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
					new Notice(`Issue #${ctx.ref.iid} ${state}`);
				} catch (e: any) {
					logger(`Failed to ${verb.toLowerCase()} issue: ${e.message}`);
					new Notice(`Failed to ${verb.toLowerCase()} issue: ${e.message}`);
				}
			},
		}).open();
	}
}
