import { App, TFile, normalizePath } from "obsidian";
import GitlabApi from "../GitlabLoader/gitlab-api";
import { GitlabIssuesSettings, IssueActionTemplate } from "../SettingsTab/settings-types";

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

export async function executeIssueActionTemplate(
	app: App,
	settings: GitlabIssuesSettings,
	ref: IssueRef,
	file: TFile,
	template: IssueActionTemplate,
	commentBodyOverride?: string | null
): Promise<void> {
	const change: { add?: string[]; remove?: string[]; replace?: string[] } = {};
	if (template.labelsReplace !== undefined) {
		change.replace = template.labelsReplace;
	} else {
		if (template.labelsAdd && template.labelsAdd.length > 0) change.add = template.labelsAdd;
		if (template.labelsRemove && template.labelsRemove.length > 0) change.remove = template.labelsRemove;
	}

	if (change.replace !== undefined || change.add || change.remove) {
		const updatedLabels = await updateIssueLabels(settings, ref, change);
		await updateNoteFrontmatter(app, file, { labels: updatedLabels });
	}

	const body =
		commentBodyOverride === undefined ? template.commentBody : commentBodyOverride;
	if (body !== undefined && body !== null && body.trim().length > 0) {
		await postIssueComment(settings, ref, body);
	}
}
