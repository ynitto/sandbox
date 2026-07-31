import { AdfNode } from "../JiraLoader/issue-types";

export function sanitizeFileName(value: string): string {
	return value
		.replace(/[:]/g, '')
		.replace(/[*"/\\<>|?]/g, '-');
}

export function logger(message: string): void {
	console.log('Jira Tasks: ' + message);
}

export function adfToMarkdown(node: AdfNode, depth = 0): string {
	switch (node.type) {
		case 'doc':
			return (node.content ?? [])
				.map(n => adfToMarkdown(n, depth))
				.filter(s => s.length > 0)
				.join('\n\n')
				.trim();

		case 'paragraph': {
			const text = (node.content ?? []).map(n => adfToMarkdown(n, depth)).join('');
			return text;
		}

		case 'text': {
			let text = node.text ?? '';
			for (const mark of node.marks ?? []) {
				switch (mark.type) {
					case 'strong':
						text = `**${text}**`;
						break;
					case 'em':
						text = `_${text}_`;
						break;
					case 'code':
						text = `\`${text}\``;
						break;
					case 'strike':
						text = `~~${text}~~`;
						break;
					case 'underline':
						text = `<u>${text}</u>`;
						break;
					case 'link':
						text = `[${text}](${mark.attrs?.href ?? ''})`;
						break;
					case 'subsup':
						text = mark.attrs?.type === 'sub' ? `<sub>${text}</sub>` : `<sup>${text}</sup>`;
						break;
				}
			}
			return text;
		}

		case 'heading': {
			const level = node.attrs?.level ?? 1;
			const text = (node.content ?? []).map(n => adfToMarkdown(n, depth)).join('');
			return '#'.repeat(level) + ' ' + text;
		}

		case 'bulletList':
			return (node.content ?? [])
				.map(n => adfToMarkdown(n, depth + 1))
				.join('\n');

		case 'orderedList':
			return (node.content ?? [])
				.map((n, i) => {
					const inner = (n.content ?? [])
						.map(c => adfToMarkdown(c, depth))
						.join('\n');
					return '  '.repeat(depth) + `${i + 1}. ${inner}`;
				})
				.join('\n');

		case 'listItem': {
			const children = node.content ?? [];
			return children
				.map((child, i) => {
					if (child.type === 'paragraph') {
						const text = (child.content ?? []).map(n => adfToMarkdown(n, depth)).join('');
						return i === 0
							? '  '.repeat(depth - 1) + '- ' + text
							: '  '.repeat(depth) + text;
					}
					return adfToMarkdown(child, depth);
				})
				.join('\n');
		}

		case 'codeBlock': {
			const lang = node.attrs?.language ?? '';
			const code = (node.content ?? []).map(n => n.text ?? '').join('');
			return '```' + lang + '\n' + code + '\n```';
		}

		case 'blockquote': {
			const text = (node.content ?? []).map(n => adfToMarkdown(n, depth)).join('\n\n');
			return text.split('\n').map(l => '> ' + l).join('\n');
		}

		case 'rule':
		case 'horizontalRule':
			return '---';

		case 'hardBreak':
			return '\n';

		case 'mention':
			return `@${node.attrs?.text ?? node.attrs?.id ?? ''}`;

		case 'emoji':
			return node.attrs?.text ?? '';

		case 'inlineCard':
			return node.attrs?.url ?? '';

		case 'media':
			return node.attrs?.alt ? `![${node.attrs.alt}](${node.attrs.url ?? ''})` : '';

		case 'mediaSingle':
			return (node.content ?? []).map(n => adfToMarkdown(n, depth)).join('');

		case 'table': {
			const rows = node.content ?? [];
			const lines: string[] = [];
			rows.forEach((row, rowIndex) => {
				const cells = (row.content ?? []).map(cell => {
					const cellText = (cell.content ?? [])
						.map(n => adfToMarkdown(n, depth))
						.join(' ')
						.replace(/\n/g, ' ')
						.replace(/\|/g, '\\|');
					return cellText;
				});
				lines.push('| ' + cells.join(' | ') + ' |');
				if (rowIndex === 0) {
					lines.push('| ' + cells.map(() => '---').join(' | ') + ' |');
				}
			});
			return lines.join('\n');
		}

		case 'tableRow':
		case 'tableHeader':
		case 'tableCell':
			return (node.content ?? []).map(n => adfToMarkdown(n, depth)).join('');

		case 'panel': {
			const panelType = node.attrs?.panelType ?? 'info';
			const text = (node.content ?? []).map(n => adfToMarkdown(n, depth)).join('\n\n');
			return `> [!${panelType}]\n` + text.split('\n').map(l => '> ' + l).join('\n');
		}

		case 'expand': {
			const title = node.attrs?.title ?? '';
			const text = (node.content ?? []).map(n => adfToMarkdown(n, depth)).join('\n\n');
			return `<details><summary>${title}</summary>\n\n${text}\n\n</details>`;
		}

		default:
			if (node.text) return node.text;
			if (node.content) return (node.content).map(n => adfToMarkdown(n, depth)).join('');
			return '';
	}
}

export const DEFAULT_TEMPLATE = `---
id: {{id}}
key: {{key}}
title: {{{summary}}}
status: {{status}}
statusCategory: {{statusCategory}}
priority: {{priority}}
issueType: {{issuetype}}
assignee: {{assignee}}
reporter: {{reporter}}
dueDate: {{duedate}}
created: {{created}}
updated: {{updated}}
{{#if labels.length}}
labels: [{{#each labels}}"{{this}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if components.length}}
components: [{{#each components}}"{{this}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if fixVersions.length}}
fixVersions: [{{#each fixVersions}}"{{this}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if storyPoints}}
storyPoints: {{storyPoints}}
{{/if}}
{{#if sprint}}
sprint: "{{sprint}}"
{{/if}}
{{#if parentKey}}
parent: "{{parentKey}}"
{{/if}}
webUrl: {{webUrl}}
---

### {{{summary}}}

| Field | Value |
| --- | --- |
| Status | {{status}} |
| Priority | {{priority}} |
| Type | {{issuetype}} |
| Assignee | {{assignee}} |
| Reporter | {{reporter}} |
{{#if duedate}}| Due Date | {{duedate}} |
{{/if}}{{#if sprint}}| Sprint | {{sprint}} |
{{/if}}{{#if storyPoints}}| Story Points | {{storyPoints}} |
{{/if}}

---

{{{description}}}

[View in Jira]({{webUrl}})

{{#if subtasks.length}}
## Subtasks

{{#each subtasks}}
- **[{{key}}]** {{{fields.summary}}} — _{{fields.status.name}}_
{{/each}}
{{/if}}

{{#if comments.length}}
## Comments

{{#each comments}}
**{{author}}** _{{created}}_

> {{{bodyText}}}

{{/each}}
{{/if}}
`;
