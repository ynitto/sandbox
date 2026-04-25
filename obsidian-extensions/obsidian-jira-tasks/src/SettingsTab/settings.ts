import { SettingInput, DropdownInput, CheckboxInput } from "./settings-types";

export const DEFAULT_SETTINGS = {
	jiraUrl: "",
	jiraEmail: "",
	jiraApiToken: "",
	templateFile: "",
	outputDir: "/Jira Tasks/",
	jqlFilter: "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC",
	maxResults: 50,
	showIcon: true,
	purgeIssues: false,
	refreshOnStartup: false,
	intervalOfRefresh: "off" as const,
	issueScope: "all" as const,
	fetchComments: false,
	labelPropertyMappings: [],
	jiraApiUrl: function () {
		return `${this.jiraUrl}/rest/api/3`;
	},
};

export const settingInputs: SettingInput[] = [
	{
		title: "Jira URL",
		description: "The base URL of your Jira instance (e.g., https://yourcompany.atlassian.net)",
		placeholder: "https://yourcompany.atlassian.net",
		value: "jiraUrl",
	},
	{
		title: "Email Address",
		description: "Your Atlassian account email address used to authenticate with Jira",
		placeholder: "you@example.com",
		value: "jiraEmail",
	},
	{
		title: "API Token",
		description: "Generate an API token at id.atlassian.com/manage-profile/security/api-tokens",
		placeholder: "ATATT...",
		value: "jiraApiToken",
		type: "password",
	},
	{
		title: "Template File",
		description: "Path to a Handlebars template file (.hbs) for rendering task notes. Leave blank to use the default template.",
		placeholder: "templates/jira-task.hbs",
		value: "templateFile",
	},
	{
		title: "Output Folder",
		description: "Folder where imported task notes will be saved",
		placeholder: "/Jira Tasks/",
		value: "outputDir",
		modifier: "normalizePath",
	},
	{
		title: "JQL Filter",
		description: "Jira Query Language expression to filter which issues to import (see Jira documentation for syntax)",
		placeholder: "assignee = currentUser() AND statusCategory != Done",
		value: "jqlFilter",
	},
];

export const dropdownInputs: DropdownInput[] = [
	{
		title: "Refresh Rate",
		description: "How often to automatically refresh tasks from Jira (in minutes)",
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
		title: "Issue Scope",
		description: "Which issue types to import from Jira",
		value: "issueScope",
		options: {
			"all": "Tasks & Subtasks",
			"tasks_only": "Tasks only",
			"subtasks_only": "Subtasks only",
		},
	},
];

export const checkboxInputs: CheckboxInput[] = [
	{
		title: "Show refresh icon",
		value: "showIcon",
	},
	{
		title: "Purge tasks before import",
		description: "Delete all existing task notes in the output folder before importing new ones.",
		value: "purgeIssues",
	},
	{
		title: "Refresh tasks on startup",
		description: "Automatically fetch tasks 30 seconds after Obsidian starts.",
		value: "refreshOnStartup",
	},
	{
		title: "Fetch Comments",
		description: "Fetch comments for each task. This may slow down imports when many tasks are loaded.",
		value: "fetchComments",
	},
];
