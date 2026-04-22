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
id: {{id}}
title: {{{title}}}
dueDate: {{due_date}}
webUrl: {{web_url}}
project: {{references.full}}
state: {{state}}
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
