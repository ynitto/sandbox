import { Vault, TFile, TAbstractFile, TFolder } from "obsidian";
import { compile } from 'handlebars';
import { ObsidianJiraIssue } from "./JiraLoader/issue-types";
import { JiraTasksSettings } from "./SettingsTab/settings-types";
import { DEFAULT_TEMPLATE, logger } from "./utils/utils";

export default class Filesystem {
	private vault: Vault;
	private settings: JiraTasksSettings;

	constructor(vault: Vault, settings: JiraTasksSettings) {
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

	public processIssues(issues: Array<ObsidianJiraIssue>) {
		this.vault.adapter.read(this.settings.templateFile)
			.then((rawTemplate: string) => {
				issues.map((issue: ObsidianJiraIssue) => this.writeFile(issue, compile(rawTemplate)));
			})
			.catch(() => {
				issues.map((issue: ObsidianJiraIssue) => this.writeFile(issue, compile(DEFAULT_TEMPLATE)));
			});
	}

	private writeFile(issue: ObsidianJiraIssue, template: HandlebarsTemplateDelegate) {
		this.vault.create(this.buildFileName(issue), template(issue))
			.catch((error) => logger(error.message));
	}

	private buildFileName(issue: ObsidianJiraIssue): string {
		return this.settings.outputDir + '/' + issue.filename + '.md';
	}
}
