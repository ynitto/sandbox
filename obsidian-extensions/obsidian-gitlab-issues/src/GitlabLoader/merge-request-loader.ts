import GitlabApi from "./gitlab-api";
import { GitlabMergeRequest } from "./merge-request";
import { App } from "obsidian";
import Filesystem from "../filesystem";
import { MergeRequest, Discussion, MrActivityEvent } from "./issue-types";
import { GitlabIssuesSettings } from "../SettingsTab/settings-types";
import { logger } from "../utils/utils";

export default class MergeRequestLoader {
	private fs: Filesystem;
	private settings: GitlabIssuesSettings;

	constructor(app: App, settings: GitlabIssuesSettings) {
		this.fs = new Filesystem(app.vault, settings);
		this.settings = settings;
	}

	getMrUrl() {
		switch (this.settings.gitlabIssuesLevel) {
			case "project":
				return `${this.settings.gitlabApiUrl()}/projects/${this.settings.gitlabAppId}/merge_requests?${this.settings.mrFilter}`;
			case "group":
				return `${this.settings.gitlabApiUrl()}/groups/${this.settings.gitlabAppId}/merge_requests?${this.settings.mrFilter}`;
			case "personal":
			default:
				return `${this.settings.gitlabApiUrl()}/merge_requests?${this.settings.mrFilter}`;
		}
	}

	async loadMergeRequests() {
		try {
			const mrs = await GitlabApi.load<Array<MergeRequest>>(encodeURI(this.getMrUrl()), this.settings.gitlabToken);

			const gitlabMrs = await Promise.all(
				mrs.map(async (rawMr: MergeRequest) => {
					const mr = new GitlabMergeRequest(rawMr);

					if (this.settings.fetchMrDiscussions) {
						try {
							const url = `${this.settings.gitlabApiUrl()}/projects/${rawMr.project_id}/merge_requests/${rawMr.iid}/discussions`;
							const discussions = await GitlabApi.load<Discussion[]>(encodeURI(url), this.settings.gitlabToken);
							mr.discussions = discussions.map(discussion => ({
								...discussion,
								notes: discussion.notes.map(note => ({
									...note,
									permalink: `${rawMr.web_url}#note_${note.id}`
								}))
							}));
						} catch (e: any) {
							logger(`Failed to fetch discussions for MR !${rawMr.iid}: ${e.message}`);
						}
					}

					if (this.settings.fetchMrActivities) {
						try {
							const url = `${this.settings.gitlabApiUrl()}/projects/${rawMr.project_id}/merge_requests/${rawMr.iid}/resource_state_events`;
							mr.activities = await GitlabApi.load<MrActivityEvent[]>(encodeURI(url), this.settings.gitlabToken);
						} catch (e: any) {
							logger(`Failed to fetch activities for MR !${rawMr.iid}: ${e.message}`);
						}
					}

					return mr;
				})
			);

			this.fs.createMrOutputDirectory();
			this.fs.processMergeRequests(gitlabMrs);
		} catch (error: any) {
			logger(error.message);
		}
	}
}
