export interface JiraUser {
	readonly accountId: string;
	readonly displayName: string;
	readonly emailAddress?: string;
	readonly avatarUrls?: Record<string, string>;
	readonly active: boolean;
	readonly timeZone?: string;
}

export interface JiraStatus {
	readonly id: string;
	readonly name: string;
	readonly statusCategory: {
		readonly id: number;
		readonly key: string;
		readonly colorName: string;
		readonly name: string;
	};
}

export interface JiraPriority {
	readonly id: string;
	readonly name: string;
	readonly iconUrl?: string;
}

export interface JiraIssueType {
	readonly id: string;
	readonly name: string;
	readonly description?: string;
	readonly iconUrl?: string;
	readonly subtask: boolean;
}

export interface JiraComponent {
	readonly id: string;
	readonly name: string;
}

export interface JiraVersion {
	readonly id: string;
	readonly name: string;
	readonly released?: boolean;
}

// Atlassian Document Format
export interface AdfNode {
	readonly type: string;
	readonly content?: AdfNode[];
	readonly text?: string;
	readonly attrs?: Record<string, any>;
	readonly marks?: ReadonlyArray<{ type: string; attrs?: Record<string, any> }>;
}

export interface AdfDocument {
	readonly version: number;
	readonly type: 'doc';
	readonly content: AdfNode[];
}

export interface JiraComment {
	readonly id: string;
	readonly author: JiraUser;
	readonly body: AdfDocument | string | null;
	readonly created: string;
	readonly updated: string;
}

export interface JiraSubtask {
	readonly id: string;
	readonly key: string;
	readonly fields: {
		readonly summary: string;
		readonly status: JiraStatus;
		readonly issuetype: JiraIssueType;
		readonly priority?: JiraPriority;
	};
}

export interface JiraSprint {
	readonly id: number;
	readonly name: string;
	readonly state: string;
	readonly boardId?: number;
	readonly startDate?: string;
	readonly endDate?: string;
}

export interface JiraIssueFields {
	readonly summary: string;
	readonly description: AdfDocument | string | null;
	readonly status: JiraStatus;
	readonly priority: JiraPriority | null;
	readonly issuetype: JiraIssueType;
	readonly assignee: JiraUser | null;
	readonly reporter: JiraUser | null;
	readonly labels: string[];
	readonly components: JiraComponent[];
	readonly fixVersions: JiraVersion[];
	readonly duedate: string | null;
	readonly created: string;
	readonly updated: string;
	readonly subtasks: JiraSubtask[];
	readonly parent?: {
		readonly id: string;
		readonly key: string;
		readonly fields: {
			readonly summary: string;
			readonly status: JiraStatus;
			readonly issuetype: JiraIssueType;
		};
	};
	readonly comment?: {
		readonly comments: JiraComment[];
		readonly total: number;
	};
	// Story points (Jira Cloud common custom field)
	readonly customfield_10016?: number | null;
	// Sprint (Jira Cloud common custom field)
	readonly customfield_10020?: JiraSprint[] | null;
	readonly [key: string]: any;
}

export interface JiraIssue {
	readonly id: string;
	readonly key: string;
	readonly self: string;
	readonly fields: JiraIssueFields;
}

export interface JiraSearchResponse {
	readonly issues: JiraIssue[];
	readonly total: number;
	readonly maxResults: number;
	readonly startAt: number;
}

export interface JiraCommentResponse {
	readonly comments: JiraComment[];
	readonly total: number;
	readonly maxResults: number;
	readonly startAt: number;
}

export interface ObsidianJiraIssue {
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
	filename: string;
	[key: string]: any;
}

export interface ProcessedComment {
	readonly id: string;
	readonly author: string;
	readonly bodyText: string;
	readonly created: string;
	readonly updated: string;
}
