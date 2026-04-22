import { SettingsTab } from "./settings-types";

export const DEFAULT_SETTINGS = {
	gitlabUrl: "",
	gitlabToken: "",
	gitlabAppId: "",
	templateFile: "",
	outputDir: "/Gitlab Issues/",
	filter: "",
	showIcon: true,
	purgeIssues: false,
	refreshOnStartup: false,
	intervalOfRefresh: "off",
	fetchDiscussions: false,
	fetchRelatedMergeRequests: false,
	labelPropertyMappings: [],
	gitlabIssuesLevel: "personal" as const,
	gitlabApiUrl: function () {
		return `${this.gitlabUrl}/api/v4`;
	},
};

export const settings: SettingsTab = {
	title: "Gitlab Issues",
	settingInputs: [
		{
			title: "Gitlab instance URL",
			description: "The URL of the GitLab instance (e.g., https://gitlab.com)",
			placeholder: "https://gitlab.com",
			value: "gitlabUrl",
		},
		{
			title: "Personal Access Token",
			description:
				"Generate a Personal Access Token in your Gitlab Settings (User > Settings > Access Tokens) with 'api' scope",
			placeholder: "glpat-...",
			value: "gitlabToken",
		},
		{
			title: "Template File",
			description:
				"Path to a handlebars template file to use for generating issue notes. Leave blank to use default template.",
			placeholder: "templates/issue.hbs",
			value: "templateFile",
		},
		{
			title: "Output Folder",
			description: "Directory where issues will be saved",
			placeholder: "/Gitlab Issues/",
			value: "outputDir",
			modifier: "normalizePath",
		},
		{
			title: "Issues Filter",
			description:
				"Filter issues using Gitlab API query parameters (see documentation for available filters)",
			placeholder: "due_date=month",
			value: "filter",
		},
	],
	dropdowns: [
		{
			title: "Refresh Rate",
			description: "How often to automatically refresh issues (in minutes)",
			value: "intervalOfRefresh",
			options: {
				"off": "Off",
				"15": "15 minutes",
				"30": "30 minutes",
				"45": "45 minutes",
				"60": "1 hour",
				"120": "2 hours",
			},
		},
		{
			title: "GitLab Scope",
			description: "Scope of issues to import",
			value: "gitlabIssuesLevel",
			options: {
				"personal": "Personal",
				"project": "Project",
				"group": "Group",
			},
		},
	],
	checkBoxInputs: [
		{
			title: "Show refresh icon",
			value: "showIcon",
		},
		{
			title: "Purge issues before import",
			value: "purgeIssues",
		},
		{
			title: "Refresh issues on startup",
			value: "refreshOnStartup",
		},
		{
			title: "Fetch Discussions",
			description: "Fetch discussion comments for each issue. May slow down imports with many issues.",
			value: "fetchDiscussions",
		},
		{
			title: "Fetch Related Merge Requests",
			description: "Fetch related merge requests for each issue. May slow down imports with many issues.",
			value: "fetchRelatedMergeRequests",
		},
	],
	getGitlabIssuesLevel: (currentLevel) => {
		if (currentLevel === "project") {
			return {
				url: "https://docs.gitlab.com/ee/api/issues.html#list-project-issues",
				title: "Project",
			};
		}
		return {
			url: "https://docs.gitlab.com/ee/api/issues.html#list-group-issues",
			title: "Group",
		};
	},
	gitlabDocumentation: {
		url: "https://docs.gitlab.com/ee/api/issues.html",
		title: "GitLab Issues API Documentation",
	},
};
