export function sanitizeFileName(value: string) {
	return value
		.replace(/[:]/g, '')
		.replace(/[*"/\\<>|?]/g, '-');
}

export function logger(message: string) {
	const pluginNamePrefix = 'Gitlab Issues: ';
	console.log(pluginNamePrefix + message);
}

export const DEFAULT_TEMPLATE = `---
id: "{{id}}"
title: "{{{title}}}"
dueDate: "{{due_date}}"
webUrl: "{{web_url}}"
project: "{{references.full}}"
state: "{{state}}"
{{#if labels.length}}
labels: [{{#each labels}}"{{this}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
---

### {{{title}}}
##### Due on {{due_date}}

{{{description}}}

[View On Gitlab]({{web_url}})

{{#if relatedMergeRequests.length}}
## Related Merge Requests

{{#each relatedMergeRequests}}
- [!{{iid}} {{{title}}}]({{web_url}}) — {{state}}
{{/each}}
{{/if}}

{{#if discussions.length}}
## Discussions

{{#each discussions}}
{{#each notes}}
{{#unless system}}
**{{author.name}}** _{{created_at}}_

> {{{body}}}

{{/unless}}
{{/each}}
{{/each}}
{{/if}}
`;

export const DEFAULT_MR_TEMPLATE = `---
id: "{{id}}"
iid: "{{iid}}"
title: "{{{title}}}"
webUrl: "{{web_url}}"
project: "{{references.full}}"
state: "{{state}}"
sourceBranch: "{{source_branch}}"
targetBranch: "{{target_branch}}"
draft: "{{draft}}"
mergeStatus: "{{detailed_merge_status}}"
{{#if issueLinks.length}}
projects: [{{#each issueLinks}}"{{this}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if labels.length}}
labels: [{{#each labels}}"{{this}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if reviewers.length}}
reviewers: [{{#each reviewers}}"{{name}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if assignees.length}}
assignees: [{{#each assignees}}"{{name}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
---

### !{{iid}} {{{title}}}

**Branch:** \`{{source_branch}}\` → \`{{target_branch}}\`
**Author:** {{author.name}}
**Status:** {{state}}{{#if draft}} (Draft){{/if}}

{{{description}}}

[View On GitLab]({{web_url}})

{{#if discussions.length}}
## Discussions

{{#each discussions}}
{{#each notes}}
{{#unless system}}
**{{author.name}}** _{{created_at}}_

> {{{body}}}

{{/unless}}
{{/each}}
{{/each}}
{{/if}}

{{#if activities.length}}
## Activity

{{#each activities}}
- **{{user.name}}** _{{created_at}}_ → \`{{state}}\`
{{/each}}
{{/if}}
`;
