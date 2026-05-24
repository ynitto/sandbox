export type GitlabIssuesLevel = 'personal' | 'project' | 'group';
export type GitlabRefreshInterval = "15" | "30" | "45" | "60" | "120" | "off";
export type RelatedMrMode = "off" | "separate" | "same";

export interface LabelPropertyRule {
	label: string;
	value: string;
}

export interface LabelPropertyMapping {
	property: string;
	rules: LabelPropertyRule[];
	default?: string;
}

export interface IssueActionTemplate {
	id: string;
	name: string;
	commentBody?: string;
	labelsAdd?: string[];
	labelsRemove?: string[];
	// `undefined` means "do not perform replace". `[]` means "clear all labels".
	labelsReplace?: string[];
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
	relatedMrMode: RelatedMrMode;
	fetchMergeRequests: boolean;
	mrFilter: string;
	mrOutputDir: string;
	mrTemplateFile: string;
	fetchMrDiscussions: boolean;
	fetchMrActivities: boolean;
	fetchMrChanges: boolean;
	labelPropertyMappings: LabelPropertyMapping[];
	issueActionTemplates: IssueActionTemplate[];
	knownLabels: string[];
	knownProjects: string[];
	maxItems: number;
	maxMrItems: number;
	staleDays: number;
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
	value: keyof Pick<GitlabIssuesSettings, "gitlabIssuesLevel" | "intervalOfRefresh" | "relatedMrMode">
	options: Record<string, string>
}

export interface SettingCheckboxInput {
	title: string;
	description?: string;
	value: keyof Pick<GitlabIssuesSettings, "refreshOnStartup" | "purgeIssues" | "showIcon" | "fetchDiscussions" | "fetchMergeRequests" | "fetchMrDiscussions" | "fetchMrActivities" | "fetchMrChanges">
}

export interface SettingsTab {
	title: string,
	settingInputs: SettingInput[],
	dropdowns: DropdownInputs[]
	checkBoxInputs: SettingCheckboxInput[],
	getGitlabIssuesLevel: (currentLevel: Omit<GitlabIssuesLevel, "personal">) => SettingOutLink;
	gitlabDocumentation: SettingOutLink
}
