import { ObsidianMergeRequest, MergeRequest, Discussion, Assignee, TimeStats, ShortIssue, References, MrActivityEvent } from "./issue-types";

export class GitlabMergeRequest implements ObsidianMergeRequest {
	id: number;
	iid: number;
	project_id: number;
	title: string;
	description: string | null;
	state: string;
	created_at: string;
	updated_at: string;
	merged_at: string | null;
	closed_at: string | null;
	web_url: string;
	references: References;
	author: Assignee;
	assignees: Assignee[];
	labels: string[];
	source_branch: string;
	target_branch: string;
	merge_status: string;
	detailed_merge_status: string;
	sha: string;
	draft: boolean;
	work_in_progress: boolean;
	squash: boolean;
	reviewers: Assignee[];
	milestone: ShortIssue | null;
	time_stats: TimeStats;
	task_completion_status: { count: number; completed_count: number };
	upvotes: number;
	downvotes: number;
	user_notes_count: number;
	discussions: Discussion[];
	issueLinks: string[];
	activities: MrActivityEvent[];

	constructor(mr: MergeRequest) {
		Object.assign(this, mr);
		this.discussions = [];
		this.issueLinks = [];
		this.activities = [];
	}

	get filename(): string {
		return `MR-${this.iid} ${this.title}`.replace(/[/\\?%*:|"<>]/g, "-");
	}
}
