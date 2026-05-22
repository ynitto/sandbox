export function sanitizeFileName(value: string) {
	return value
		.replace(/[:]/g, '')
		.replace(/[*"/\\<>|?]/g, '-');
}

export function logger(message: string) {
	const pluginNamePrefix = 'Gitlab Issues: ';
	console.log(pluginNamePrefix + message);
}

export function staleCutoffIso(staleDays: number): string | null {
	if (!staleDays || staleDays <= 0) return null;
	const cutoff = new Date(Date.now() - staleDays * 24 * 60 * 60 * 1000);
	return cutoff.toISOString();
}

export function appendStaleParam(filter: string, staleDays: number): string {
	const cutoff = staleCutoffIso(staleDays);
	if (!cutoff) return filter;
	if (/(^|[?&])updated_after=/.test(filter)) return filter;
	const sep = filter && !filter.endsWith('&') && !filter.endsWith('?') ? '&' : '';
	return `${filter}${sep}updated_after=${cutoff}`;
}

export function isStale(updatedAt: string | undefined | null, staleDays: number): boolean {
	const cutoff = staleCutoffIso(staleDays);
	if (!cutoff || !updatedAt) return false;
	return updatedAt < cutoff;
}

export const DEFAULT_TEMPLATE = `---
id: "{{id}}"
iid: "{{iid}}"
projectId: "{{project_id}}"
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

{{#if (eq relatedMrMode "same")}}
{{#each relatedMergeRequests}}
### [!{{iid}} {{{title}}}]({{web_url}})

**Status:** {{state}}{{#if draft}} (Draft){{/if}}{{#if detailed_merge_status}} · {{detailed_merge_status}}{{/if}}
{{#if source_branch}}**Branch:** \`{{source_branch}}\` → \`{{target_branch}}\`{{/if}}
**Author:** {{author.name}}{{#if assignees.length}} · **Assignees:** {{#each assignees}}{{name}}{{#unless @last}}, {{/unless}}{{/each}}{{/if}}{{#if reviewers.length}} · **Reviewers:** {{#each reviewers}}{{name}}{{#unless @last}}, {{/unless}}{{/each}}{{/if}}
**Updated:** {{updated_at}}{{#if merged_at}} · **Merged:** {{merged_at}}{{/if}}

{{#if description}}
{{{description}}}

{{/if}}
{{#if changes.length}}
<details><summary>Code Diff ({{changes.length}} file(s))</summary>

{{#each changes}}
**\`{{new_path}}\`**{{#if new_file}} (new){{/if}}{{#if deleted_file}} (deleted){{/if}}{{#if renamed_file}} (renamed from \`{{old_path}}\`){{/if}}

\`\`\`diff
{{{diff}}}
\`\`\`

{{/each}}
</details>

{{/if}}
{{/each}}
{{else}}
{{#if (eq relatedMrMode "separate")}}
{{#each relatedMergeRequests}}
- [[{{wikilink}}|!{{iid}} {{{title}}}]] — {{state}}
{{/each}}
{{else}}
{{#each relatedMergeRequests}}
- [!{{iid}} {{{title}}}]({{web_url}}) — {{state}}
{{/each}}
{{/if}}
{{/if}}
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
projectId: "{{project_id}}"
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

{{#if changes.length}}
## Code Diff ({{changes.length}} file(s))

{{#each changes}}
### \`{{new_path}}\`{{#if new_file}} (new){{/if}}{{#if deleted_file}} (deleted){{/if}}{{#if renamed_file}} (renamed from \`{{old_path}}\`){{/if}}

\`\`\`diff
{{{diff}}}
\`\`\`

{{/each}}
{{/if}}
`;
