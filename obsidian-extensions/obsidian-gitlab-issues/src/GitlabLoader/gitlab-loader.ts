import GitlabApi from "./gitlab-api";
import { GitlabIssue } from "./issue";
import { GitlabMergeRequest } from "./merge-request";
import { App } from "obsidian";
import Filesystem from "../filesystem";
import { Issue, Discussion, MergeRequest, MergeRequestChangesResponse, EmbeddedRelatedMergeRequest, MrActivityEvent } from "./issue-types";
import { GitlabIssuesSettings, LabelPropertyMapping } from "../SettingsTab/settings-types";
import { buildListFilter, isStale, logger } from "../utils/utils";
import { extractRepoPath, sanitizeFolderSegment, sanitizeRepoPath } from "./repo";

export default class GitlabLoader {
	private fs: Filesystem;
	private settings: GitlabIssuesSettings;
	private onLabelsCollected?: (labels: string[]) => void | Promise<void>;

	constructor(
		app: App,
		settings: GitlabIssuesSettings,
		onLabelsCollected?: (labels: string[]) => void | Promise<void>
	) {
		this.fs = new Filesystem(app.vault, settings);
		this.settings = settings;
		this.onLabelsCollected = onLabelsCollected;
	}

	getUrl() {
		const filter = buildListFilter(this.settings.filter, this.settings.staleDays);
		switch (this.settings.gitlabIssuesLevel) {
			case "project":
				return `${this.settings.gitlabApiUrl()}/projects/${this.settings.gitlabAppId}/issues?${filter}`;
			case "group":
				return `${this.settings.gitlabApiUrl()}/groups/${this.settings.gitlabAppId}/issues?${filter}`;
			case "personal":
			default:
				return `${this.settings.gitlabApiUrl()}/issues?${filter}`;
		}
	}

	async loadIssues() {
		try {
			const allIssues = await GitlabApi.loadAll<Issue>(this.getUrl(), this.settings.gitlabToken, this.settings.maxItems);
			const issues = allIssues.filter((i) => !isStale(i.updated_at, this.settings.staleDays));

			const relatedMrMap = new Map<string, GitlabMergeRequest>();

			const gitlabIssues = await Promise.all(
				issues.map(async (rawIssue: Issue) => {
					const issue = new GitlabIssue(rawIssue);

					if (this.settings.fetchDiscussions) {
						try {
							const url = `${this.settings.gitlabApiUrl()}/projects/${rawIssue.project_id}/issues/${rawIssue.iid}/discussions`;
							issue.discussions = await GitlabApi.load<Discussion[]>(encodeURI(url), this.settings.gitlabToken);
						} catch (e: any) {
							logger(`Failed to fetch discussions for issue #${rawIssue.iid}: ${e.message}`);
						}
					}

					try {
						const url = `${this.settings.gitlabApiUrl()}/projects/${rawIssue.project_id}/issues/${rawIssue.iid}/related_merge_requests`;
						const fetched = await GitlabApi.load<MergeRequest[]>(encodeURI(url), this.settings.gitlabToken);
						issue.relatedMergeRequests = fetched
							.filter((mr) => !isStale(mr.updated_at, this.settings.staleDays))
							.map<EmbeddedRelatedMergeRequest>((mr) => {
								const repoPath = sanitizeRepoPath(extractRepoPath(mr, "merge_requests"));
								const safeTitle = sanitizeFolderSegment(mr.title).replace(/[/\\?%]/g, "-");
								const filename = `!${mr.iid} - ${safeTitle}`;
								return { ...mr, repoPath, wikilink: filename };
							});
					} catch (e: any) {
						logger(`Failed to fetch merge requests for issue #${rawIssue.iid}: ${e.message}`);
					}

					if (this.settings.relatedMrMode === "same" && this.settings.fetchMrChanges) {
						await Promise.all(issue.relatedMergeRequests.map(async (mr) => {
							try {
								const url = `${this.settings.gitlabApiUrl()}/projects/${mr.project_id}/merge_requests/${mr.iid}/changes`;
								const resp = await GitlabApi.load<MergeRequestChangesResponse>(encodeURI(url), this.settings.gitlabToken);
								(mr as any).changes = resp.changes ?? [];
							} catch (e: any) {
								logger(`Failed to fetch changes for related MR !${mr.iid}: ${e.message}`);
							}
						}));
					}

					if (this.settings.relatedMrMode === "separate") {
						for (const rawMr of issue.relatedMergeRequests) {
							const key = `${rawMr.project_id}/${rawMr.iid}`;
							if (!relatedMrMap.has(key)) {
								relatedMrMap.set(key, new GitlabMergeRequest(rawMr));
							}
							relatedMrMap.get(key)!.issueLinks.push(`[[${issue.wikilink}]]`);
						}
					}

					this.applyLabelMappings(issue);

					return issue;
				})
			);

			if (this.settings.relatedMrMode === "separate" && relatedMrMap.size > 0) {
				const relatedMrs = Array.from(relatedMrMap.values());

				if (this.settings.fetchMrDiscussions) {
					await Promise.all(relatedMrs.map(async (mr) => {
						try {
							const url = `${this.settings.gitlabApiUrl()}/projects/${mr.project_id}/merge_requests/${mr.iid}/discussions`;
							mr.discussions = await GitlabApi.load<Discussion[]>(encodeURI(url), this.settings.gitlabToken);
						} catch (e: any) {
							logger(`Failed to fetch discussions for MR !${mr.iid}: ${e.message}`);
						}
					}));
				}

				if (this.settings.fetchMrActivities) {
					await Promise.all(relatedMrs.map(async (mr) => {
						try {
							const url = `${this.settings.gitlabApiUrl()}/projects/${mr.project_id}/merge_requests/${mr.iid}/resource_state_events`;
							mr.activities = await GitlabApi.load<MrActivityEvent[]>(encodeURI(url), this.settings.gitlabToken);
						} catch (e: any) {
							logger(`Failed to fetch activities for MR !${mr.iid}: ${e.message}`);
						}
					}));
				}

				if (this.settings.fetchMrChanges) {
					await Promise.all(relatedMrs.map(async (mr) => {
						if (mr.changes && mr.changes.length > 0) return;
						try {
							const url = `${this.settings.gitlabApiUrl()}/projects/${mr.project_id}/merge_requests/${mr.iid}/changes`;
							const resp = await GitlabApi.load<MergeRequestChangesResponse>(encodeURI(url), this.settings.gitlabToken);
							mr.changes = resp.changes ?? [];
						} catch (e: any) {
							logger(`Failed to fetch changes for MR !${mr.iid}: ${e.message}`);
						}
					}));
				}

				this.fs.createMrOutputDirectory();
				this.fs.processMergeRequests(relatedMrs);
			}

			if (this.settings.purgeIssues) {
				this.fs.purgeExistingIssues();
			}
			this.fs.processIssues(gitlabIssues);

			if (this.onLabelsCollected) {
				const collected = new Set<string>();
				gitlabIssues.forEach((i) => (i.labels ?? []).forEach((l) => collected.add(l)));
				if (collected.size > 0) {
					await this.onLabelsCollected(Array.from(collected));
				}
			}
		} catch (error: any) {
			logger(error.message);
		}
	}

	private applyLabelMappings(issue: GitlabIssue): void {
		const mappings: LabelPropertyMapping[] = this.settings.labelPropertyMappings ?? [];
		if (!mappings.length) return;

		for (const mapping of mappings) {
			let matched = false;
			for (const rule of mapping.rules) {
				if (issue.labels.includes(rule.label)) {
					(issue as any)[mapping.property] = rule.value;
					matched = true;
					break;
				}
			}
			if (!matched && mapping.default !== undefined) {
				(issue as any)[mapping.property] = mapping.default;
			}
		}
	}
}
