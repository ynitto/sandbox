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
	moveIssueFileForState,
} from "./IssueActions/actions";
import {
	ConfirmModal,
	IssueActionsModal,
	NewIssueModal,
	TemplateScaffoldModal,
} from "./IssueActions/modals";
import {
	defaultScaffoldPath,
	writeTemplateScaffold,
	TemplateScaffoldKind,
} from "./utils/template-scaffolds";

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
				id: "gitlab-issues-create",
				name: "Create new Gitlab issue",
				callback: () => this.createNewIssue(),
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
				id: "gitlab-issues-create-issue-template-scaffold",
				name: "Create issue template scaffold (all placeholders)",
				callback: () => this.openTemplateScaffoldModal("issue"),
			});

			this.addCommand({
				id: "gitlab-issues-create-mr-template-scaffold",
				name: "Create merge request template scaffold (all placeholders)",
				callback: () => this.openTemplateScaffoldModal("mr"),
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
		const loader = new GitlabLoader(this.app, this.settings, (labels) =>
			this.recordKnownLabels(labels)
		);
		loader.loadIssues();
	}

	public async recordKnownLabels(incoming: string[]): Promise<void> {
		if (!incoming || incoming.length === 0) return;
		const set = new Set<string>(this.settings.knownLabels ?? []);
		const before = set.size;
		incoming.forEach((l) => {
			const trimmed = String(l).trim();
			if (trimmed.length > 0) set.add(trimmed);
		});
		if (set.size === before) return;
		this.settings.knownLabels = Array.from(set).sort((a, b) =>
			a.localeCompare(b, undefined, { sensitivity: "base" })
		);
		await this.saveSettings();
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

	private createNewIssue() {
		const defaultProject =
			this.settings.gitlabIssuesLevel === "project" ? this.settings.gitlabAppId ?? "" : "";
		new NewIssueModal(this.app, this.settings, defaultProject, {
			getKnownLabels: () => this.settings.knownLabels ?? [],
			onLabelsLearned: (labels) => this.recordKnownLabels(labels),
			onCreated: () => {
				this.fetchIssuesFromGitlab();
			},
		}).open();
	}

	private manageActiveIssue() {
		const ctx = this.resolveActiveIssue();
		if (!ctx) return;
		new IssueActionsModal(this.app, this.settings, ctx.ref, ctx.file, ctx.frontmatter, {
			getKnownLabels: () => this.settings.knownLabels ?? [],
			onLabelsLearned: (labels) => this.recordKnownLabels(labels),
			getTemplates: () => this.settings.issueActionTemplates ?? [],
		}).open();
	}

	public openTemplateScaffoldModal(kind: TemplateScaffoldKind): void {
		const currentSettingPath =
			kind === "issue" ? this.settings.templateFile : this.settings.mrTemplateFile;
		new TemplateScaffoldModal(this.app, {
			kind,
			defaultPath: defaultScaffoldPath(kind),
			currentSettingPath,
			onSubmit: async (path, overwrite, linkToSettings) => {
				const file = await writeTemplateScaffold(this.app, kind, path, overwrite);
				if (linkToSettings) {
					if (kind === "issue") {
						this.settings.templateFile = file.path;
					} else {
						this.settings.mrTemplateFile = file.path;
					}
					await this.saveSettings();
				}
				new Notice(`Template scaffold written to ${file.path}`);
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
