import { App, TFile, normalizePath } from "obsidian";
import GitlabApi from "../GitlabLoader/gitlab-api";
import { GitlabIssuesSettings } from "../SettingsTab/settings-types";

export interface IssueRef {
	projectId: string;
	iid: number;
}

export function parseIssueRefFromWebUrl(webUrl: string): IssueRef | null {
	const m = webUrl.match(/^https?:\/\/[^/]+\/(.+?)\/-\/issues\/(\d+)/);
	if (!m) return null;
	return { projectId: m[1], iid: parseInt(m[2], 10) };
}

export function getActiveIssueRef(app: App): { file: TFile; ref: IssueRef; frontmatter: Record<string, any> } | null {
	const file = app.workspace.getActiveFile();
	if (!file) return null;
	const fm = app.metadataCache.getFileCache(file)?.frontmatter as Record<string, any> | undefined;
	if (!fm) return null;

	let projectId = fm.projectId !== undefined ? String(fm.projectId) : null;
	let iid = fm.iid !== undefined ? parseInt(String(fm.iid), 10) : null;

	if (!projectId || !iid) {
		const webUrl = fm.webUrl ?? fm.web_url;
		if (typeof webUrl === "string") {
			const parsed = parseIssueRefFromWebUrl(webUrl);
			if (parsed) {
				projectId = projectId ?? parsed.projectId;
				iid = iid ?? parsed.iid;
			}
		}
	}

	if (!projectId || !iid || isNaN(iid)) return null;
	return { file, ref: { projectId, iid }, frontmatter: fm };
}

function issueApiUrl(settings: GitlabIssuesSettings, ref: IssueRef, suffix = ""): string {
	const id = encodeURIComponent(ref.projectId);
	return `${settings.gitlabApiUrl()}/projects/${id}/issues/${ref.iid}${suffix}`;
}

export interface CreateIssueParams {
	title: string;
	description?: string;
	labels?: string[];
	confidential?: boolean;
	due_date?: string;
}

export interface CreatedIssue {
	id: number;
	iid: number;
	project_id: number;
	title: string;
	state: string;
	web_url: string;
}

export async function createIssue(
	settings: GitlabIssuesSettings,
	projectId: string,
	params: CreateIssueParams
): Promise<CreatedIssue> {
	const id = encodeURIComponent(projectId);
	const body: Record<string, string> = { title: params.title };
	if (params.description) body.description = params.description;
	if (params.labels && params.labels.length > 0) body.labels = params.labels.join(",");
	if (params.confidential) body.confidential = "true";
	if (params.due_date) body.due_date = params.due_date;

	return await GitlabApi.request<CreatedIssue>(
		`${settings.gitlabApiUrl()}/projects/${id}/issues`,
		settings.gitlabToken,
		"POST",
		body
	);
}

export async function postIssueComment(
	settings: GitlabIssuesSettings,
	ref: IssueRef,
	body: string
): Promise<void> {
	await GitlabApi.request<unknown>(
		issueApiUrl(settings, ref, "/notes"),
		settings.gitlabToken,
		"POST",
		{ body }
	);
}

export async function updateIssueLabels(
	settings: GitlabIssuesSettings,
	ref: IssueRef,
	change: { add?: string[]; remove?: string[]; replace?: string[] }
): Promise<string[]> {
	const params: Record<string, string> = {};
	if (change.replace !== undefined) {
		params.labels = change.replace.join(",");
	} else {
		if (change.add && change.add.length > 0) params.add_labels = change.add.join(",");
		if (change.remove && change.remove.length > 0) params.remove_labels = change.remove.join(",");
	}
	if (Object.keys(params).length === 0) return [];

	const resp = await GitlabApi.request<{ labels: string[] }>(
		issueApiUrl(settings, ref),
		settings.gitlabToken,
		"PUT",
		params
	);
	return resp.labels ?? [];
}

export async function setIssueState(
	settings: GitlabIssuesSettings,
	ref: IssueRef,
	stateEvent: "close" | "reopen"
): Promise<string> {
	const resp = await GitlabApi.request<{ state: string }>(
		issueApiUrl(settings, ref),
		settings.gitlabToken,
		"PUT",
		{ state_event: stateEvent }
	);
	return resp.state;
}

export async function updateNoteFrontmatter(
	app: App,
	file: TFile,
	updates: Record<string, any>
): Promise<void> {
	await app.fileManager.processFrontMatter(file, (fm) => {
		for (const [k, v] of Object.entries(updates)) {
			fm[k] = v;
		}
	});
}

export function splitLabelList(value: string): string[] {
	return value
		.split(",")
		.map((s) => s.trim())
		.filter((s) => s.length > 0);
}

async function ensureFolderPath(app: App, folderPath: string): Promise<void> {
	if (!folderPath) return;
	const segments = folderPath.split("/").filter((s) => s.length > 0);
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

export async function moveIssueFileForState(
	app: App,
	file: TFile,
	newState: string
): Promise<TFile> {
	const targetFolder = newState === "closed" ? "Closed" : "Open";
	const oppositeFolder = newState === "closed" ? "Open" : "Closed";

	const parent = file.parent;
	if (!parent) return file;

	let baseFolderPath: string;
	if (parent.name === oppositeFolder) {
		baseFolderPath = parent.parent ? parent.parent.path : "";
	} else if (parent.name === targetFolder) {
		return file;
	} else {
		baseFolderPath = parent.path;
	}

	const newFolderPath = baseFolderPath
		? normalizePath(`${baseFolderPath}/${targetFolder}`)
		: targetFolder;
	const newPath = normalizePath(`${newFolderPath}/${file.name}`);
	if (newPath === file.path) return file;

	await ensureFolderPath(app, newFolderPath);
	await app.fileManager.renameFile(file, newPath);
	const moved = app.vault.getAbstractFileByPath(newPath);
	return moved instanceof TFile ? moved : file;
}

export function expandRemoveLabelPatterns(patterns: string[], current: string[]): string[] {
	const out = new Set<string>();
	for (const raw of patterns) {
		const p = raw.trim();
		if (p.length === 0) continue;
		if (p.includes("*")) {
			const re = new RegExp(
				"^" + p.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$"
			);
			current.forEach((l) => {
				if (re.test(l)) out.add(l);
			});
		} else {
			out.add(p);
		}
	}
	return Array.from(out);
}

export async function applyLabelChanges(
	settings: GitlabIssuesSettings,
	ref: IssueRef,
	currentLabels: string[],
	removePatterns: string[],
	addLabels: string[]
): Promise<string[]> {
	const expandedRemove = expandRemoveLabelPatterns(removePatterns, currentLabels);
	let resultLabels = currentLabels.slice();
	let changed = false;

	if (expandedRemove.length > 0) {
		resultLabels = await updateIssueLabels(settings, ref, { remove: expandedRemove });
		changed = true;
	}
	if (addLabels.length > 0) {
		resultLabels = await updateIssueLabels(settings, ref, { add: addLabels });
		changed = true;
	}
	if (!changed) return currentLabels;
	return resultLabels;
}
