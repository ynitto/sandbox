import { Vault, TFile, TAbstractFile, TFolder, normalizePath } from "obsidian";
import { compile } from 'handlebars';
import { ObsidianIssue, ObsidianMergeRequest } from "./GitlabLoader/issue-types";
import { GitlabIssuesSettings } from "./SettingsTab/settings-types";
import { DEFAULT_TEMPLATE, DEFAULT_MR_TEMPLATE, logger } from "./utils/utils";
import { registerHandlebarsHelpers } from "./utils/handlebars-helpers";

registerHandlebarsHelpers();

export default class Filesystem {
	private vault: Vault;
	private settings: GitlabIssuesSettings;

	constructor(vault: Vault, settings: GitlabIssuesSettings) {
		this.vault = vault;
		this.settings = settings;
	}

	public createOutputDirectory() {
		this.vault.createFolder(this.settings.outputDir)
			.catch((error) => {
				if (error.message !== 'Folder already exists.') {
					logger('Could not create output directory');
				}
			});
	}

	public createMrOutputDirectory() {
		this.vault.createFolder(this.settings.mrOutputDir)
			.catch((error) => {
				if (error.message !== 'Folder already exists.') {
					logger('Could not create MR output directory');
				}
			});
	}

	public purgeExistingIssues() {
		const outputDir: TAbstractFile | null = this.vault.getAbstractFileByPath(this.settings.outputDir);
		if (outputDir instanceof TFolder) {
			Vault.recurseChildren(outputDir, (existingFile: TAbstractFile) => {
				if (existingFile instanceof TFile) {
					this.vault.delete(existingFile).catch(error => logger(error.message));
				}
			});
		}
	}

	public processIssues(issues: Array<ObsidianIssue>) {
		this.vault.adapter.read(this.settings.templateFile)
			.then((rawTemplate: string) => {
				issues.map((issue: ObsidianIssue) => this.writeIssueFile(issue, compile(rawTemplate)));
			})
			.catch(() => {
				issues.map((issue: ObsidianIssue) => this.writeIssueFile(issue, compile(DEFAULT_TEMPLATE.toString())));
			});
	}

	public processMergeRequests(mrs: Array<ObsidianMergeRequest>) {
		this.vault.adapter.read(this.settings.mrTemplateFile)
			.then((rawTemplate: string) => {
				mrs.forEach((mr: ObsidianMergeRequest) => this.writeMrFile(mr, compile(rawTemplate)));
			})
			.catch(() => {
				mrs.forEach((mr: ObsidianMergeRequest) => this.writeMrFile(mr, compile(DEFAULT_MR_TEMPLATE.toString())));
			});
	}

	private async writeIssueFile(issue: ObsidianIssue, template: HandlebarsTemplateDelegate) {
		(issue as any).relatedMrMode = this.settings.relatedMrMode;
		const repoFolder = this.joinPath(this.settings.outputDir, (issue as any).repoPath);
		await this.ensureFolder(repoFolder);
		const path = this.joinPath(repoFolder, `${issue.filename}.md`);
		this.vault.create(path, template(issue)).catch((error) => logger(error.message));
	}

	private async writeMrFile(mr: ObsidianMergeRequest, template: HandlebarsTemplateDelegate) {
		const repoFolder = this.joinPath(this.settings.mrOutputDir, (mr as any).repoPath);
		await this.ensureFolder(repoFolder);
		const path = this.joinPath(repoFolder, `${mr.filename}.md`);
		this.vault.create(path, template(mr)).catch((error) => logger(error.message));
	}

	private joinPath(...parts: string[]): string {
		return normalizePath(parts.filter((p) => p && p.length > 0).join("/"));
	}

	private async ensureFolder(path: string): Promise<void> {
		if (!path) return;
		const segments = path.split("/").filter((s) => s.length > 0);
		let current = "";
		for (const seg of segments) {
			current = current ? `${current}/${seg}` : seg;
			const exists = this.vault.getAbstractFileByPath(current);
			if (exists) continue;
			try {
				await this.vault.createFolder(current);
			} catch (error: any) {
				if (!error?.message?.includes("Folder already exists")) {
					logger(`Could not create folder ${current}: ${error?.message ?? error}`);
				}
			}
		}
	}
}
