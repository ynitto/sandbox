export interface TextlintSettings {
  lintOnOpen: boolean;
  foldersToIgnore: string[];
  textlintrc: string;
}

export const DEFAULT_TEXTLINTRC = JSON.stringify(
  { rules: { 'preset-ai-writing': true } },
  null,
  2
);

export const DEFAULT_SETTINGS: TextlintSettings = {
  lintOnOpen: false,
  foldersToIgnore: [],
  textlintrc: DEFAULT_TEXTLINTRC,
};
