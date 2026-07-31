export type JiraRefreshInterval = "15" | "30" | "45" | "60" | "120" | "off";
export type JiraIssueScope = "all" | "tasks_only" | "subtasks_only";

export interface LabelPropertyRule {
	label: string;
	value: string;
}

export interface LabelPropertyMapping {
	property: string;
	rules: LabelPropertyRule[];
	default?: string;
}

export interface JiraTasksSettings {
	jiraUrl: string;
	jiraEmail: string;
	jiraApiToken: string;
	templateFile: string;
	outputDir: string;
	jqlFilter: string;
	maxResults: number;
	showIcon: boolean;
	purgeIssues: boolean;
	refreshOnStartup: boolean;
	intervalOfRefresh: JiraRefreshInterval;
	issueScope: JiraIssueScope;
	fetchComments: boolean;
	labelPropertyMappings: LabelPropertyMapping[];
	jiraApiUrl(): string;
}

export interface SettingOutLink {
	url: string;
	title: string;
}

export interface SettingInput {
	title: string;
	description: string;
	placeholder?: string;
	value: keyof Pick<JiraTasksSettings, "jiraUrl" | "jiraEmail" | "jiraApiToken" | "templateFile" | "outputDir" | "jqlFilter">;
	modifier?: string;
	type?: string;
}

export interface DropdownInput {
	title: string;
	description: string;
	value: keyof Pick<JiraTasksSettings, "intervalOfRefresh" | "issueScope">;
	options: Record<string, string>;
}

export interface CheckboxInput {
	title: string;
	description?: string;
	value: keyof Pick<JiraTasksSettings, "showIcon" | "purgeIssues" | "refreshOnStartup" | "fetchComments">;
}
