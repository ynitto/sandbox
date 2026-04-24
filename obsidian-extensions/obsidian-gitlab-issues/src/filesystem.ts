import { Vault, TFile, TAbstractFile, TFolder } from "obsidian";
import { compile } from 'handlebars';
import { ObsidianIssue, ObsidianMergeRequest } from "./GitlabLoader/issue-types";
import { GitlabIssuesSettings } from "./SettingsTab/settings-types";
import { DEFAULT_TEMPLATE, DEFAULT_MR_TEMPLATE, logger } from "./utils/utils";

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

	private writeIssueFile(issue: ObsidianIssue, template: HandlebarsTemplateDelegate) {
		this.vault.create(this.buildIssueFileName(issue), template(issue)).catch((error) => logger(error.message));
	}

	private writeMrFile(mr: ObsidianMergeRequest, template: HandlebarsTemplateDelegate) {
		this.vault.create(this.buildMrFileName(mr), template(mr)).catch((error) => logger(error.message));
	}

	private buildIssueFileName(issue: ObsidianIssue): string {
		return this.settings.outputDir + '/' + issue.filename + '.md';
	}

	private buildMrFileName(mr: ObsidianMergeRequest): string {
		return this.settings.mrOutputDir + '/' + mr.filename + '.md';
	}
}
