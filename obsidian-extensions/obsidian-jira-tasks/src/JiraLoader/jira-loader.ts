import JiraApi from "./jira-api";
import { JiraTask } from "./issue";
import { App } from "obsidian";
import Filesystem from "../filesystem";
import { JiraSearchResponse, JiraCommentResponse, ProcessedComment, AdfNode } from "./issue-types";
import { JiraTasksSettings, LabelPropertyMapping } from "../SettingsTab/settings-types";
import { logger, adfToMarkdown } from "../utils/utils";

export default class JiraLoader {
	private fs: Filesystem;
	private settings: JiraTasksSettings;

	constructor(app: App, settings: JiraTasksSettings) {
		this.fs = new Filesystem(app.vault, settings);
		this.settings = settings;
	}

	getSearchUrl(): string {
		const base = this.settings.jiraApiUrl();
		const fields = [
			'summary', 'description', 'status', 'priority', 'issuetype',
			'assignee', 'reporter', 'labels', 'components', 'fixVersions',
			'duedate', 'created', 'updated', 'subtasks', 'parent',
			'customfield_10016', 'customfield_10020',
		].join(',');

		const jql = encodeURIComponent(this.settings.jqlFilter || 'assignee = currentUser() ORDER BY updated DESC');
		const maxResults = this.settings.maxResults || 50;

		return `${base}/search?jql=${jql}&fields=${fields}&maxResults=${maxResults}`;
	}

	async loadIssues() {
		try {
			const response = await JiraApi.load<JiraSearchResponse>(
				this.getSearchUrl(),
				this.settings.jiraEmail,
				this.settings.jiraApiToken,
			);

			const tasks = await Promise.all(
				response.issues.map(async (rawIssue) => {
					const task = new JiraTask(rawIssue, this.settings.jiraUrl);

					if (this.settings.fetchComments) {
						try {
							const url = `${this.settings.jiraApiUrl()}/issue/${rawIssue.key}/comment?maxResults=100`;
							const commentResponse = await JiraApi.load<JiraCommentResponse>(
								url,
								this.settings.jiraEmail,
								this.settings.jiraApiToken,
							);
							task.comments = commentResponse.comments.map(c => {
								const bodyText = typeof c.body === 'string'
									? c.body
									: c.body ? adfToMarkdown(c.body as AdfNode) : '';
								const processed: ProcessedComment = {
									id: c.id,
									author: c.author.displayName,
									bodyText,
									created: c.created,
									updated: c.updated,
								};
								return processed;
							});
						} catch (e: any) {
							logger(`Failed to fetch comments for ${rawIssue.key}: ${e.message}`);
						}
					}

					this.applyLabelMappings(task);

					return task;
				})
			);

			if (this.settings.purgeIssues) {
				this.fs.purgeExistingIssues();
			}
			this.fs.processIssues(tasks);
		} catch (error: any) {
			logger(error.message);
		}
	}

	private applyLabelMappings(task: JiraTask): void {
		const mappings: LabelPropertyMapping[] = this.settings.labelPropertyMappings ?? [];
		if (!mappings.length) return;

		for (const mapping of mappings) {
			let matched = false;
			for (const rule of mapping.rules) {
				if (task.labels.includes(rule.label)) {
					(task as any)[mapping.property] = rule.value;
					matched = true;
					break;
				}
			}
			if (!matched && mapping.default !== undefined) {
				(task as any)[mapping.property] = mapping.default;
			}
		}
	}
}
