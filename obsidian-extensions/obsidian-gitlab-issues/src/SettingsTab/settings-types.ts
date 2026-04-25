export type GitlabIssuesLevel = 'personal' | 'project' | 'group';
export type GitlabRefreshInterval = "15" | "30" | "45" | "60" | "120" | "off";

export interface LabelPropertyRule {
	label: string;
	value: string;
}

export interface LabelPropertyMapping {
	property: string;
	rules: LabelPropertyRule[];
	default?: string;
}

export interface GitlabIssuesSettings {
	gitlabUrl: string;
	gitlabToken: string;
	gitlabIssuesLevel: GitlabIssuesLevel;
	gitlabAppId: string;
	templateFile: string;
	outputDir: string;
	filter: string;
	showIcon: boolean;
	purgeIssues: boolean;
	refreshOnStartup: boolean;
	intervalOfRefresh: GitlabRefreshInterval;
	fetchDiscussions: boolean;
	fetchRelatedMergeRequests: boolean;
	createRelatedMrFiles: boolean;
	fetchMergeRequests: boolean;
	mrFilter: string;
	mrOutputDir: string;
	mrTemplateFile: string;
	fetchMrDiscussions: boolean;
	fetchMrActivities: boolean;
	labelPropertyMappings: LabelPropertyMapping[];
	gitlabApiUrl(): string;
}

export interface SettingOutLink {
	url: string;
	title: string;
}

export interface Setting {
	title: string,
	description: string,
	placeholder?: string;
}

export interface SettingInput extends Setting {
	value: keyof Pick<GitlabIssuesSettings, "filter" | "gitlabUrl" | "gitlabToken" | "outputDir" | "templateFile" | "mrFilter" | "mrOutputDir" | "mrTemplateFile">,
	modifier?: string
}

export interface DropdownInputs extends Setting {
	value: keyof Pick<GitlabIssuesSettings, "gitlabIssuesLevel" | "intervalOfRefresh">
	options: Record<string, string>
}

export interface SettingCheckboxInput {
	title: string;
	description?: string;
	value: keyof Pick<GitlabIssuesSettings, "refreshOnStartup" | "purgeIssues" | "showIcon" | "fetchDiscussions" | "fetchRelatedMergeRequests" | "createRelatedMrFiles" | "fetchMergeRequests" | "fetchMrDiscussions" | "fetchMrActivities">
}

export interface SettingsTab {
	title: string,
	settingInputs: SettingInput[],
	dropdowns: DropdownInputs[]
	checkBoxInputs: SettingCheckboxInput[],
	getGitlabIssuesLevel: (currentLevel: Omit<GitlabIssuesLevel, "personal">) => SettingOutLink;
	gitlabDocumentation: SettingOutLink
}
