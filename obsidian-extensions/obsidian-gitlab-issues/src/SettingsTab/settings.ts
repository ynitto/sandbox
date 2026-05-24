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
	relatedMrMode: "off" as const,
	fetchMergeRequests: false,
	mrFilter: "",
	mrOutputDir: "/Gitlab Merge Requests/",
	mrTemplateFile: "",
	fetchMrDiscussions: false,
	fetchMrActivities: false,
	fetchMrChanges: false,
	labelPropertyMappings: [],
	issueActionTemplates: [],
	knownLabels: [],
	knownProjects: [],
	maxItems: 20,
	maxMrItems: 20,
	staleDays: 0,
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
				"Generate a Personal Access Token in your Gitlab Settings (User > Settings > Access Tokens) with 'api' scope. Stored separately in data.secrets.json alongside this plugin's data.json — add data.secrets.json to your .gitignore so the rest of the settings can be safely shared via git.",
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
		{
			title: "Merge Request Template File",
			description:
				"Path to a handlebars template file for generating merge request notes. Leave blank to use default template.",
			placeholder: "templates/merge-request.hbs",
			value: "mrTemplateFile",
		},
		{
			title: "Merge Requests Output Folder",
			description: "Directory where merge request notes will be saved",
			placeholder: "/Gitlab Merge Requests/",
			value: "mrOutputDir",
			modifier: "normalizePath",
		},
		{
			title: "Merge Requests Filter",
			description:
				"Filter merge requests using Gitlab API query parameters (e.g., state=opened&scope=assigned_to_me)",
			placeholder: "state=opened",
			value: "mrFilter",
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
			description: "Scope of issues and merge requests to import",
			value: "gitlabIssuesLevel",
			options: {
				"personal": "Personal",
				"project": "Project",
				"group": "Group",
			},
		},
		{
			title: "Related Merge Requests",
			description: "How to render related MRs on issue notes. 'Off' writes a list of GitLab URLs. 'Separate' creates a file per MR (with code diff) and links to it. 'Same' embeds details inline in the issue note.",
			value: "relatedMrMode",
			options: {
				"off": "Off (GitLab URL list)",
				"separate": "Separate file (with code diff)",
				"same": "Same file (embed details)",
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
			title: "Import Merge Requests",
			description: "Enable standalone merge request import using the Merge Requests filter and output folder settings below.",
			value: "fetchMergeRequests",
		},
		{
			title: "Fetch MR Discussions",
			description: "Fetch discussion comments for each merge request (applies to both standalone import and related MR files).",
			value: "fetchMrDiscussions",
		},
		{
			title: "Fetch MR Activities",
			description: "Fetch state change activity events for each merge request, opened/merged/closed/reopened (applies to both standalone import and related MR files).",
			value: "fetchMrActivities",
		},
		{
			title: "Fetch MR Code Diff",
			description: "Fetch the final code diff (changes) for each merge request and embed it in the MR markdown. Applies equally to standalone MR import and related-MR files (Separate mode), and to inline embedding when Related MR mode is 'Same'.",
			value: "fetchMrChanges",
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
