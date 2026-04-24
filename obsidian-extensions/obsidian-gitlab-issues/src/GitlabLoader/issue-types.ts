export interface Assignee {
	readonly avatar_url: string;
	readonly id: number;
	readonly locked: boolean;
	readonly name: string;
	readonly state: string;
	readonly username: string;
	readonly web_url: string;
}

export interface Epic {
	readonly id: number,
	readonly iid: number,
	readonly title: string,
	readonly url: string,
	readonly group_id: number
}

export interface References {
	readonly short: string,
	readonly relative: string,
	readonly full: string
}

export interface TimeStats {
	readonly time_estimate: number;
	readonly total_time_spent: number;
	readonly human_time_spent: number;
	readonly human_total_time_spent: number;
}

export interface ShortIssue {
	readonly due_date: string | null,
	readonly project_id: number,
	readonly state: string,
	readonly description: string | null,
	readonly iid: number,
	readonly id: number,
	readonly title: string,
	readonly created_at: string,
	readonly updated_at: string
}

export interface Issue extends ShortIssue {
	readonly web_url: string;
	readonly references: string | References;

	readonly assignees: Assignee[];
	readonly author: Assignee;
	readonly closed_by: Assignee;
	readonly epic: Epic;
	readonly labels: string[];
	readonly upvotes: number;
	readonly downvotes: number;
	readonly merge_requests_count: number;
	readonly user_notes_count: number;
	readonly imported: boolean;
	readonly imported_from: string;
	readonly has_tasks: boolean
	readonly task_status: string,
	readonly confidential: boolean,
	readonly discussion_locked: boolean
	readonly issue_type: string,
	readonly time_stats: TimeStats,
	readonly severity: string | undefined,
	readonly _links: {
		self: string,
		notes: string,
		award_emoji: string,
		project: string,
		closed_as_duplicate_of: string
	},
	readonly task_completion_status: {
		count: number,
		completed_count: number
	}
	readonly milestone: ShortIssue
}

export interface DiscussionNote {
	readonly id: number;
	readonly type: string | null;
	readonly body: string;
	readonly author: Assignee;
	readonly created_at: string;
	readonly updated_at: string;
	readonly system: boolean;
	readonly noteable_id: number;
	readonly noteable_type: string;
	readonly resolvable: boolean;
	readonly resolved?: boolean;
	readonly resolved_by?: Assignee;
	readonly resolved_at?: string;
}

export interface Discussion {
	readonly id: string;
	readonly individual_note: boolean;
	readonly notes: DiscussionNote[];
}

export interface MergeRequest {
	readonly id: number;
	readonly iid: number;
	readonly project_id: number;
	readonly title: string;
	readonly description: string | null;
	readonly state: string;
	readonly created_at: string;
	readonly updated_at: string;
	readonly merged_at: string | null;
	readonly web_url: string;
	readonly references: References;
	readonly author: Assignee;
	readonly assignees: Assignee[];
	readonly labels: string[];
}

export interface MergeRequestDetailed extends MergeRequest {
	readonly source_branch: string;
	readonly target_branch: string;
	readonly merge_status: string;
	readonly detailed_merge_status: string;
	readonly sha: string;
	readonly draft: boolean;
	readonly work_in_progress: boolean;
	readonly squash: boolean;
	readonly reviewers: Assignee[];
	readonly milestone: ShortIssue | null;
	readonly time_stats: TimeStats;
	readonly task_completion_status: {
		count: number;
		completed_count: number;
	};
	readonly upvotes: number;
	readonly downvotes: number;
	readonly user_notes_count: number;
	readonly closed_at: string | null;
}

export interface ObsidianMergeRequest extends MergeRequestDetailed {
	filename: string;
	discussions: Discussion[];
	issueLinks: string[];
}

export interface ObsidianIssue extends Issue {
	filename: string;
	discussions: Discussion[];
	relatedMergeRequests: MergeRequest[];
}
