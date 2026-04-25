import GitlabApi from "./gitlab-api";
import { GitlabIssue } from "./issue";
import { GitlabMergeRequest } from "./merge-request";
import { App } from "obsidian";
import Filesystem from "../filesystem";
import { Issue, Discussion, MergeRequest } from "./issue-types";
import { GitlabIssuesSettings, LabelPropertyMapping } from "../SettingsTab/settings-types";
import { logger } from "../utils/utils";

export default class GitlabLoader {
	private fs: Filesystem;
	private settings: GitlabIssuesSettings;

	constructor(app: App, settings: GitlabIssuesSettings) {
		this.fs = new Filesystem(app.vault, settings);
		this.settings = settings;
	}

	getUrl() {
		switch (this.settings.gitlabIssuesLevel) {
			case "project":
				return `${this.settings.gitlabApiUrl()}/projects/${this.settings.gitlabAppId}/issues?${this.settings.filter}`;
			case "group":
				return `${this.settings.gitlabApiUrl()}/groups/${this.settings.gitlabAppId}/issues?${this.settings.filter}`;
			case "personal":
			default:
				return `${this.settings.gitlabApiUrl()}/issues?${this.settings.filter}`;
		}
	}

	async loadIssues() {
		try {
			const issues = await GitlabApi.loadAll<Issue>(this.getUrl(), this.settings.gitlabToken, this.settings.maxItems);

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

					if (this.settings.fetchRelatedMergeRequests) {
						try {
							const url = `${this.settings.gitlabApiUrl()}/projects/${rawIssue.project_id}/issues/${rawIssue.iid}/related_merge_requests`;
							issue.relatedMergeRequests = await GitlabApi.load<MergeRequest[]>(encodeURI(url), this.settings.gitlabToken);
						} catch (e: any) {
							logger(`Failed to fetch merge requests for issue #${rawIssue.iid}: ${e.message}`);
						}

						if (this.settings.createRelatedMrFiles) {
							for (const rawMr of issue.relatedMergeRequests) {
								const key = `${rawMr.project_id}/${rawMr.iid}`;
								if (!relatedMrMap.has(key)) {
									relatedMrMap.set(key, new GitlabMergeRequest(rawMr));
								}
								relatedMrMap.get(key)!.issueLinks.push(`[[${issue.filename}]]`);
							}
						}
					}

					this.applyLabelMappings(issue);

					return issue;
				})
			);

			if (this.settings.createRelatedMrFiles && relatedMrMap.size > 0) {
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

				this.fs.createMrOutputDirectory();
				this.fs.processMergeRequests(relatedMrs);
			}

			if (this.settings.purgeIssues) {
				this.fs.purgeExistingIssues();
			}
			this.fs.processIssues(gitlabIssues);
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
