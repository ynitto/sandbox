import { App, normalizePath, TFile } from "obsidian";

export type TemplateScaffoldKind = "issue" | "mr";

export function defaultScaffoldPath(kind: TemplateScaffoldKind): string {
	return kind === "issue"
		? "templates/gitlab-issue.hbs"
		: "templates/gitlab-merge-request.hbs";
}

export function scaffoldContent(kind: TemplateScaffoldKind): string {
	return kind === "issue" ? ISSUE_TEMPLATE_SCAFFOLD : MR_TEMPLATE_SCAFFOLD;
}

async function ensureParentFolder(app: App, filePath: string): Promise<void> {
	const segments = filePath.split("/").filter((s) => s.length > 0);
	segments.pop();
	let current = "";
	for (const seg of segments) {
		current = current ? `${current}/${seg}` : seg;
		if (app.vault.getAbstractFileByPath(current)) continue;
		try {
			await app.vault.createFolder(current);
		} catch (e: any) {
			if (!String(e?.message ?? "").includes("Folder already exists")) throw e;
		}
	}
}

export async function writeTemplateScaffold(
	app: App,
	kind: TemplateScaffoldKind,
	rawPath: string,
	overwrite: boolean
): Promise<TFile> {
	const path = normalizePath(rawPath);
	await ensureParentFolder(app, path);
	const existing = app.vault.getAbstractFileByPath(path);
	const content = scaffoldContent(kind);
	if (existing instanceof TFile) {
		if (!overwrite) {
			throw new Error(`A file already exists at "${path}". Tick "Overwrite" to replace it.`);
		}
		await app.vault.modify(existing, content);
		return existing;
	}
	const created = await app.vault.create(path, content);
	return created;
}

export const ISSUE_TEMPLATE_SCAFFOLD = `---
{{!-- ===========================================================
     Issue note frontmatter
     All placeholders below come from the GitLab Issues API plus
     a few computed values added by this plugin (repoPath, wikilink,
     relatedMrMode, and any properties from Label Property Mappings).
     Remove sections you do not need; the file is plain Handlebars.

     Custom helpers registered by this plugin:
       {{eq a b}}                       — equality test (used by #if)
       {{replace input "pat" "rep"}}    — global literal string replace
       {{prefixLines input "> "}}       — prefix every line of input
     Example:
       {{{prefixLines description "> "}}}   renders the description as
                                            a Markdown blockquote.

     Note: {{wikilink}} is the file's basename (e.g. "!9 - MR Title").
     Obsidian resolves [[basename]] links via basename match across the
     whole vault, so links keep working even if you move the file
     later — as long as basenames remain unique.
============================================================ --}}
id: "{{id}}"
iid: "{{iid}}"
projectId: "{{project_id}}"
title: "{{{title}}}"
state: "{{state}}"
issueType: "{{issue_type}}"
severity: "{{severity}}"
confidential: "{{confidential}}"
discussionLocked: "{{discussion_locked}}"
imported: "{{imported}}"
importedFrom: "{{imported_from}}"
hasTasks: "{{has_tasks}}"
taskStatus: "{{task_status}}"
tasksCompleted: "{{task_completion_status.completed_count}}"
tasksTotal: "{{task_completion_status.count}}"
upvotes: "{{upvotes}}"
downvotes: "{{downvotes}}"
mergeRequestsCount: "{{merge_requests_count}}"
userNotesCount: "{{user_notes_count}}"
createdAt: "{{created_at}}"
updatedAt: "{{updated_at}}"
dueDate: "{{due_date}}"
webUrl: "{{web_url}}"
project: "{{references.full}}"
projectShort: "{{references.short}}"
projectRelative: "{{references.relative}}"
author: "{{author.name}}"
authorUsername: "{{author.username}}"
{{#if closed_by}}
closedBy: "{{closed_by.name}}"
{{/if}}
{{#if assignees.length}}
assignees: [{{#each assignees}}"{{name}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if labels.length}}
labels: [{{#each labels}}"{{this}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if milestone}}
milestone: "{{milestone.title}}"
milestoneIid: "{{milestone.iid}}"
milestoneState: "{{milestone.state}}"
milestoneDueDate: "{{milestone.due_date}}"
{{/if}}
{{#if epic}}
epic: "{{epic.title}}"
epicIid: "{{epic.iid}}"
epicUrl: "{{epic.url}}"
{{/if}}
{{#if time_stats}}
timeEstimate: "{{time_stats.time_estimate}}"
timeSpent: "{{time_stats.total_time_spent}}"
humanTimeEstimate: "{{time_stats.human_time_spent}}"
humanTotalTimeSpent: "{{time_stats.human_total_time_spent}}"
{{/if}}
repoPath: "{{repoPath}}"
wikilink: "{{wikilink}}"
{{!-- Computed properties from Label Property Mappings appear by
     the property name you configured, e.g.:
     priority: "{{priority}}"                                    --}}
---

# {{iid}} · {{{title}}}

**State:** {{state}}{{#if confidential}} · Confidential{{/if}}{{#if discussion_locked}} · Locked{{/if}}
**Author:** {{author.name}} (@{{author.username}})
**Created:** {{created_at}} · **Updated:** {{updated_at}}{{#if due_date}} · **Due:** {{due_date}}{{/if}}
{{#if closed_by}}**Closed by:** {{closed_by.name}}{{/if}}
{{#if assignees.length}}**Assignees:** {{#each assignees}}{{name}}{{#unless @last}}, {{/unless}}{{/each}}{{/if}}
{{#if labels.length}}**Labels:** {{#each labels}}\`{{this}}\`{{#unless @last}} {{/unless}}{{/each}}{{/if}}
{{#if milestone}}**Milestone:** {{milestone.title}} ({{milestone.state}}){{/if}}
{{#if epic}}**Epic:** [{{epic.title}}]({{epic.url}}){{/if}}

[View on GitLab]({{web_url}})

---

## Description

{{{description}}}

{{#if has_tasks}}
> Tasks: {{task_completion_status.completed_count}}/{{task_completion_status.count}}
{{/if}}

{{#if relatedMergeRequests.length}}
## Related Merge Requests

{{!-- relatedMrMode is one of "off" | "separate" | "same" --}}
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

{{!-- Links exposed by the GitLab API (uncomment to use):
- Self:  {{_links.self}}
- Notes: {{_links.notes}}
- Award emoji: {{_links.award_emoji}}
- Project: {{_links.project}}
- Closed as duplicate of: {{_links.closed_as_duplicate_of}}
--}}
`;

export const MR_TEMPLATE_SCAFFOLD = `---
{{!-- ===========================================================
     Merge Request note frontmatter
     All placeholders below come from the GitLab Merge Requests API
     plus computed fields added by this plugin (repoPath, wikilink,
     issueLinks). Trim sections to taste.

     Custom helpers registered by this plugin:
       {{eq a b}}                       — equality test (used by #if)
       {{replace input "pat" "rep"}}    — global literal string replace
       {{prefixLines input "> "}}       — prefix every line of input
     Example:
       {{{prefixLines description "> "}}}   renders the description as
                                            a Markdown blockquote.

     Note: {{wikilink}} is the file's basename (e.g. "!9 - MR Title").
     Obsidian resolves [[basename]] links via basename match across the
     whole vault, so links keep working even if you move the file
     later — as long as basenames remain unique.
============================================================ --}}
id: "{{id}}"
iid: "{{iid}}"
projectId: "{{project_id}}"
title: "{{{title}}}"
state: "{{state}}"
draft: "{{draft}}"
workInProgress: "{{work_in_progress}}"
squash: "{{squash}}"
mergeStatus: "{{merge_status}}"
detailedMergeStatus: "{{detailed_merge_status}}"
sha: "{{sha}}"
createdAt: "{{created_at}}"
updatedAt: "{{updated_at}}"
mergedAt: "{{merged_at}}"
closedAt: "{{closed_at}}"
sourceBranch: "{{source_branch}}"
targetBranch: "{{target_branch}}"
webUrl: "{{web_url}}"
project: "{{references.full}}"
projectShort: "{{references.short}}"
projectRelative: "{{references.relative}}"
author: "{{author.name}}"
authorUsername: "{{author.username}}"
upvotes: "{{upvotes}}"
downvotes: "{{downvotes}}"
userNotesCount: "{{user_notes_count}}"
tasksCompleted: "{{task_completion_status.completed_count}}"
tasksTotal: "{{task_completion_status.count}}"
{{#if assignees.length}}
assignees: [{{#each assignees}}"{{name}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if reviewers.length}}
reviewers: [{{#each reviewers}}"{{name}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if labels.length}}
labels: [{{#each labels}}"{{this}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
{{#if milestone}}
milestone: "{{milestone.title}}"
milestoneIid: "{{milestone.iid}}"
milestoneState: "{{milestone.state}}"
milestoneDueDate: "{{milestone.due_date}}"
{{/if}}
{{#if time_stats}}
timeEstimate: "{{time_stats.time_estimate}}"
timeSpent: "{{time_stats.total_time_spent}}"
humanTimeEstimate: "{{time_stats.human_time_spent}}"
humanTotalTimeSpent: "{{time_stats.human_total_time_spent}}"
{{/if}}
{{#if issueLinks.length}}
issueLinks: [{{#each issueLinks}}"{{this}}"{{#unless @last}}, {{/unless}}{{/each}}]
{{/if}}
repoPath: "{{repoPath}}"
wikilink: "{{wikilink}}"
---

# !{{iid}} · {{{title}}}

**Status:** {{state}}{{#if draft}} (Draft){{/if}}{{#if detailed_merge_status}} · {{detailed_merge_status}}{{/if}}
**Branch:** \`{{source_branch}}\` → \`{{target_branch}}\`
**Author:** {{author.name}} (@{{author.username}})
**Created:** {{created_at}} · **Updated:** {{updated_at}}{{#if merged_at}} · **Merged:** {{merged_at}}{{/if}}{{#if closed_at}} · **Closed:** {{closed_at}}{{/if}}
{{#if assignees.length}}**Assignees:** {{#each assignees}}{{name}}{{#unless @last}}, {{/unless}}{{/each}}{{/if}}
{{#if reviewers.length}}**Reviewers:** {{#each reviewers}}{{name}}{{#unless @last}}, {{/unless}}{{/each}}{{/if}}
{{#if labels.length}}**Labels:** {{#each labels}}\`{{this}}\`{{#unless @last}} {{/unless}}{{/each}}{{/if}}
{{#if milestone}}**Milestone:** {{milestone.title}} ({{milestone.state}}){{/if}}

[View on GitLab]({{web_url}})

---

## Description

{{{description}}}

{{#if issueLinks.length}}
## Linked Issues

{{#each issueLinks}}
- {{{this}}}
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

{{#if activities.length}}
## Activity

{{#each activities}}
- **{{user.name}}** _{{created_at}}_ → \`{{state}}\` ({{resource_type}})
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
