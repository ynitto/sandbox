import { JiraIssue, ObsidianJiraIssue, JiraSubtask, ProcessedComment, AdfDocument, AdfNode } from "./issue-types";
import { sanitizeFileName, adfToMarkdown } from "../utils/utils";

export class JiraTask implements ObsidianJiraIssue {
	id: string;
	key: string;
	self: string;
	summary: string;
	description: string;
	status: string;
	statusCategory: string;
	priority: string;
	issuetype: string;
	assignee: string;
	reporter: string;
	labels: string[];
	components: string[];
	fixVersions: string[];
	duedate: string | null;
	created: string;
	updated: string;
	subtasks: JiraSubtask[];
	parent: string | null;
	parentKey: string | null;
	storyPoints: number | null;
	sprint: string | null;
	comments: ProcessedComment[];
	webUrl: string;

	constructor(issue: JiraIssue, baseUrl: string) {
		const f = issue.fields;

		this.id = issue.id;
		this.key = issue.key;
		this.self = issue.self;
		this.summary = f.summary ?? '';
		this.description = this.parseDescription(f.description);
		this.status = f.status?.name ?? '';
		this.statusCategory = f.status?.statusCategory?.name ?? '';
		this.priority = f.priority?.name ?? '';
		this.issuetype = f.issuetype?.name ?? '';
		this.assignee = f.assignee?.displayName ?? '';
		this.reporter = f.reporter?.displayName ?? '';
		this.labels = f.labels ?? [];
		this.components = (f.components ?? []).map(c => c.name);
		this.fixVersions = (f.fixVersions ?? []).map(v => v.name);
		this.duedate = f.duedate ?? null;
		this.created = f.created ?? '';
		this.updated = f.updated ?? '';
		this.subtasks = f.subtasks ?? [];
		this.parent = f.parent?.fields?.summary ?? null;
		this.parentKey = f.parent?.key ?? null;
		this.storyPoints = f.customfield_10016 ?? null;
		this.sprint = this.parseSprint(f.customfield_10020);
		this.comments = [];
		this.webUrl = `${baseUrl}/browse/${issue.key}`;
	}

	get filename(): string {
		return sanitizeFileName(`${this.key} ${this.summary}`);
	}

	private parseDescription(desc: AdfDocument | string | null): string {
		if (!desc) return '';
		if (typeof desc === 'string') return desc;
		return adfToMarkdown(desc as AdfNode);
	}

	private parseSprint(sprints: any[] | null | undefined): string | null {
		if (!sprints || !sprints.length) return null;
		const active = sprints.find(s => s.state === 'active') ?? sprints[sprints.length - 1];
		return active?.name ?? null;
	}
}
