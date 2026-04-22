import { ObsidianIssue, Issue, Discussion, MergeRequest } from "./issue-types";

export class GitlabIssue implements ObsidianIssue {
	id: number;
	title: string;
	description: string | null;
	due_date: string | null;
	web_url: string;
	state: string;
	created_at: string;
	updated_at: string;
	project_id: number;
	assignees: Array<any>;
	author: any;
	closed_by: any;
	upvotes: number;
	downvotes: number;
	user_notes_count: number;
	has_tasks: boolean;
	task_completion_status: any;
	task_status: any;
	labels: Array<string>;
	severity: string | undefined;
	confidential: boolean;
	merge_requests_count: number;
	_links: any;
	iid: number;
	discussions: Discussion[];
	relatedMergeRequests: MergeRequest[];

	constructor(issue: Issue) {
		Object.assign(this, issue);
		this.discussions = [];
		this.relatedMergeRequests = [];
	}

	get filename(): string {
		return this.title.replace(/[/\\?%*:|"<>]/g, "-");
	}
}
