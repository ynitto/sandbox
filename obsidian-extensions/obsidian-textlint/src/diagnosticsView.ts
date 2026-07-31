import { TextlintMessage } from '@textlint/types';
import { ItemView, Platform } from 'obsidian';
import TextlintPlugin from './main';
import { getActiveEditor, textlintSeverityToDiagnosticSeverity } from './util';

export const TEXTLINT_DIAGNOSTICS_EXTENSION = 'textlint.diagnostics';
export const VIEW_TYPE_TEXTLINT_DIAGNOSTICS = 'textlint-diagnostics-view';

const PREFIX = 'textlint-plugin-diagnostics-view';

const CONTENT_ID = PREFIX + '-content';
const HEADER_ID = PREFIX + '-content-header';
const DIAGNOSTICS_CONTAINER_ID = PREFIX + '-diagnostics-container';
const DIAGNOSTICS_COUNT_ID = PREFIX + '-content-header-metadata-count';
const DIAGNOSTICS_DETAIL_INFO_ID = '-content-header-metadata-severity-info';
const DIAGNOSTICS_DETAIL_WARNING_ID = '-content-header-metadata-severity-warning';
const DIAGNOSTICS_DETAIL_ERROR_ID = '-content-header-metadata-severity-error';
const FIX_BTN_ID = PREFIX + '-fix-btn';

const TAB_BAR_ID = PREFIX + '-tab-bar';
const TAB_BTN_DIAGNOSTICS_ID = PREFIX + '-tab-btn-diagnostics';
const TAB_BTN_OUTPUT_ID = PREFIX + '-tab-btn-output';
const TAB_PANEL_DIAGNOSTICS_ID = PREFIX + '-tab-panel-diagnostics';
const TAB_PANEL_OUTPUT_ID = PREFIX + '-tab-panel-output';

const MESSAGE_CONTAINER_ID = PREFIX + '-content-message-container';
const OUTPUT_TEXTAREA_ID = PREFIX + '-output-textarea';

type DiagnosticsCount = { 0: number; 1: number; 2: number };
type TabName = 'diagnostics' | 'output';

export class TextlintDiagnosticView extends ItemView {
  private currentTab: TabName = 'diagnostics';
  private onFixAll?: () => Promise<void>;

  getViewType() {
    return VIEW_TYPE_TEXTLINT_DIAGNOSTICS;
  }

  getIcon() {
    return 'file-type';
  }

  getDisplayText() {
    return 'textlint diagnostics';
  }

  async onClose() {
    this.contentEl.empty();
  }

  async onOpen() {
    this.contentEl.id = CONTENT_ID;
    this.buildLayout();
  }

  private buildLayout() {
    this.contentEl.empty();

    // --- Header (metadata only) ---
    const header = this.contentEl.createDiv({ attr: { id: HEADER_ID } });
    header.addClass(PREFIX + '-header');

    const metaContainer = header.createDiv({ attr: { id: DIAGNOSTICS_CONTAINER_ID } });
    metaContainer.addClass(PREFIX + '-header-meta');

    const count = metaContainer.createEl('span', { attr: { id: DIAGNOSTICS_COUNT_ID } });
    count.textContent = 'count: 0';

    const severityContainer = metaContainer.createDiv();
    severityContainer.addClass(PREFIX + '-severity-container');

    const info = severityContainer.createEl('span', { attr: { id: DIAGNOSTICS_DETAIL_INFO_ID } });
    info.addClasses([PREFIX + '-severity-item', 'textlint-plugin-severity-info']);
    const warning = severityContainer.createEl('span', { attr: { id: DIAGNOSTICS_DETAIL_WARNING_ID } });
    warning.addClasses([PREFIX + '-severity-item', 'textlint-plugin-severity-warning']);
    const error = severityContainer.createEl('span', { attr: { id: DIAGNOSTICS_DETAIL_ERROR_ID } });
    error.addClasses([PREFIX + '-severity-item', 'textlint-plugin-severity-error']);

    // --- Tab bar (tabs left, Fix All right) ---
    const tabBar = this.contentEl.createDiv({ attr: { id: TAB_BAR_ID } });
    tabBar.addClass(PREFIX + '-tab-bar');

    const diagTab = tabBar.createEl('button', { attr: { id: TAB_BTN_DIAGNOSTICS_ID }, text: 'Diagnostics' });
    diagTab.addClass(PREFIX + '-tab-btn');
    diagTab.addEventListener('click', () => this.activateTab('diagnostics'));

    const outputTab = tabBar.createEl('button', { attr: { id: TAB_BTN_OUTPUT_ID }, text: 'Output' });
    outputTab.addClass(PREFIX + '-tab-btn');
    outputTab.addEventListener('click', () => this.activateTab('output'));

    const fixBtn = tabBar.createEl('button', { attr: { id: FIX_BTN_ID }, text: 'Fix All' });
    fixBtn.addClass(PREFIX + '-fix-btn');
    fixBtn.disabled = true;
    fixBtn.addEventListener('click', async () => {
      if (!this.onFixAll) return;
      fixBtn.textContent = 'Fixing…';
      fixBtn.disabled = true;
      try {
        await this.onFixAll();
      } finally {
        fixBtn.textContent = 'Fix All';
      }
    });

    // --- Diagnostics panel ---
    const diagPanel = this.contentEl.createDiv({ attr: { id: TAB_PANEL_DIAGNOSTICS_ID } });
    diagPanel.addClass(PREFIX + '-tab-panel');
    diagPanel.createDiv({ attr: { id: MESSAGE_CONTAINER_ID } });

    // --- Output panel ---
    const outputPanel = this.contentEl.createDiv({ attr: { id: TAB_PANEL_OUTPUT_ID } });
    outputPanel.addClass(PREFIX + '-tab-panel');
    outputPanel.addClass(PREFIX + '-tab-panel--output');
    const textarea = outputPanel.createEl('textarea', { attr: { id: OUTPUT_TEXTAREA_ID } }) as HTMLTextAreaElement;
    textarea.readOnly = true;
    textarea.addClass(PREFIX + '-output-textarea');

    this.setDiagnosticsMetadata();
    this.activateTab(this.currentTab);
  }

  private activateTab(tab: TabName) {
    this.currentTab = tab;

    const diagPanel = this.contentEl.querySelector<HTMLElement>(`#${TAB_PANEL_DIAGNOSTICS_ID}`);
    const outputPanel = this.contentEl.querySelector<HTMLElement>(`#${TAB_PANEL_OUTPUT_ID}`);
    const diagBtn = this.contentEl.querySelector<HTMLElement>(`#${TAB_BTN_DIAGNOSTICS_ID}`);
    const outputBtn = this.contentEl.querySelector<HTMLElement>(`#${TAB_BTN_OUTPUT_ID}`);

    if (diagPanel) diagPanel.style.display = tab === 'diagnostics' ? '' : 'none';
    if (outputPanel) outputPanel.style.display = tab === 'output' ? '' : 'none';
    if (diagBtn) diagBtn.toggleClass(PREFIX + '-tab-btn--active', tab === 'diagnostics');
    if (outputBtn) outputBtn.toggleClass(PREFIX + '-tab-btn--active', tab === 'output');
  }

  setFixCallback(fn: () => Promise<void>) {
    this.onFixAll = fn;
  }

  clear() {
    this.setDiagnosticsMetadata();
    const mc = this.contentEl.querySelector(`#${MESSAGE_CONTAINER_ID}`);
    if (mc) mc.empty();
    const btn = this.contentEl.querySelector<HTMLButtonElement>(`#${FIX_BTN_ID}`);
    if (btn) btn.disabled = true;
  }

  setCommandOutput(output: string) {
    if (!this.contentEl.querySelector(`#${TAB_PANEL_OUTPUT_ID}`)) {
      this.buildLayout();
    }
    const textarea = this.contentEl.querySelector<HTMLTextAreaElement>(`#${OUTPUT_TEXTAREA_ID}`);
    if (textarea) textarea.value = output;
  }

  setDiagnosticsMetadata(count: DiagnosticsCount = { 0: 0, 1: 0, 2: 0 }) {
    const countEl = this.contentEl.querySelector(`#${DIAGNOSTICS_COUNT_ID}`);
    if (countEl) countEl.textContent = `count: ${count[0] + count[1] + count[2]}`;
    const infoEl = this.contentEl.querySelector(`#${DIAGNOSTICS_DETAIL_INFO_ID}`);
    if (infoEl) infoEl.textContent = `💡${count[0]}`;
    const warningEl = this.contentEl.querySelector(`#${DIAGNOSTICS_DETAIL_WARNING_ID}`);
    if (warningEl) warningEl.textContent = `⚠️ ${count[1]}`;
    const errorEl = this.contentEl.querySelector(`#${DIAGNOSTICS_DETAIL_ERROR_ID}`);
    if (errorEl) errorEl.textContent = `❗${count[2]}`;
  }

  setTextlintDiagnostics(plugin: TextlintPlugin, messages: TextlintMessage[]) {
    if (!this.contentEl.querySelector(`#${MESSAGE_CONTAINER_ID}`)) {
      this.buildLayout();
    }
    const el = this.contentEl.querySelector(`#${MESSAGE_CONTAINER_ID}`);
    if (!el) return;
    el.setChildrenInPlace([]);

    const msgs = messages
      .slice()
      .filter((m) => m.severity >= plugin.settings.minimumSeverityInDiagnosticsView);

    if (msgs.length === 0) {
      this.clear();
      return;
    }

    const count: DiagnosticsCount = { 0: 0, 1: 0, 2: 0 };

    msgs
      .sort((a, b) => b.severity - a.severity)
      .forEach((d) => {
        count[d.severity as 0 | 1 | 2]++;
        el.appendChild(this.createDiagnosticItemElement(plugin, d));
      });

    this.setDiagnosticsMetadata(count);

    const btn = this.contentEl.querySelector<HTMLButtonElement>(`#${FIX_BTN_ID}`);
    if (btn) btn.disabled = !this.onFixAll;
  }

  private createDiagnosticItemElement(plugin: TextlintPlugin, d: TextlintMessage): HTMLElement {
    // Use document.createElement to avoid auto-appending to contentEl
    const container = document.createElement('div');
    container.addClass(PREFIX + '-item');
    container.onClickEvent(() => {
      const leaf = plugin.app.workspace.getMostRecentLeaf();
      if (!leaf) return;
      if (Platform.isMobile) plugin.app.workspace.rightSplit.collapse();
      plugin.app.workspace.setActiveLeaf(leaf);
      const editor = getActiveEditor(plugin);
      if (!editor) return;
      editor.setCursor({ line: d.loc.start.line - 1, ch: d.loc.start.column - 1 });
    });

    const message = container.createEl('span', { text: d.message });
    message.addClasses([
      PREFIX + '-item-message',
      `textlint-plugin-severity-${textlintSeverityToDiagnosticSeverity(d.severity)}`,
    ]);

    const footer = container.createDiv();
    footer.addClass(PREFIX + '-item-footer');

    const rule = footer.createEl('span', { text: d.ruleId });
    rule.addClass(PREFIX + '-item-rule');

    const right = footer.createDiv();
    right.addClass(PREFIX + '-item-footer-right');

    if (d.fix !== undefined) {
      const badge = right.createEl('span', { text: '✓ fixable' });
      badge.addClass(PREFIX + '-item-fixable');
    }

    const loc = right.createEl('span', { text: `[${d.loc.start.line}, ${d.loc.start.column}]` });
    loc.addClass(PREFIX + '-item-loc');

    return container;
  }
}

