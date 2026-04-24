import { ObsidianIssue, Issue, Discussion, MergeRequest, References, TimeStats, Epic, ShortIssue } from "./issue-types";

export class GitlabIssue implements ObsidianIssue {
	id: number;
	iid: number;
	title: string;
	description: string | null;
	due_date: string | null;
	web_url: string;
	state: string;
	created_at: string;
	updated_at: string;
	project_id: number;
	references: string | References;
	assignees: Array<any>;
	author: any;
	closed_by: any;
	epic: Epic;
	upvotes: number;
	downvotes: number;
	user_notes_count: number;
	has_tasks: boolean;
	task_completion_status: any;
	task_status: any;
	labels: Array<string>;
	severity: string | undefined;
	confidential: boolean;
	discussion_locked: boolean;
	issue_type: string;
	time_stats: TimeStats;
	merge_requests_count: number;
	milestone: ShortIssue;
	imported: boolean;
	imported_from: string;
	_links: any;
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
