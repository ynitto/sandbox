'use strict';

const host = require('./host');

function preparePatch(patch, current, safeStorage) {
  if (!patch?.audit || !Object.hasOwn(patch.audit, 'shareToken')) return patch;
  const audit = { ...patch.audit };
  const token = String(audit.shareToken || '').trim();
  delete audit.shareToken;
  if (token) {
    const url = String(audit.shareRepo ?? current.shareRepo ?? '').trim();
    if (!/^https:\/\//i.test(url)) throw new Error('トークンを使う公開先は HTTPS のURLを指定してください。');
    if (!safeStorage.isEncryptionAvailable() || safeStorage.getSelectedStorageBackend?.() === 'basic_text') {
      throw new Error('この環境ではトークンを暗号化して保存できません。OSの資格情報ストアを有効にしてください。');
    }
    audit.shareTokenEncrypted = safeStorage.encryptString(token).toString('base64');
  } else audit.shareTokenEncrypted = '';
  return { ...patch, audit };
}

function readToken(config, safeStorage) {
  const encrypted = config?.audit?.shareTokenEncrypted;
  if (!encrypted) return '';
  try { return safeStorage.decryptString(Buffer.from(encrypted, 'base64')); }
  catch { throw new Error('保存したアクセストークンを読み出せません。設定で入力し直してください。'); }
}

async function gitExec(shell, argv, options, { url = '', token = '' } = {}) {
  if (!token || !/^https:\/\//i.test(url)) return shell.exec(argv, options);
  const scope = new URL(url);
  scope.username = ''; scope.password = ''; scope.search = ''; scope.hash = '';
  const authorization = `Authorization: Basic ${Buffer.from(`oauth2:${token}`).toString('base64')}`;
  // スクリプトは常駐シェルの stdin へ渡す。資格情報はコマンドラインや Git config に残さない。
  const env = {
    GIT_CONFIG_COUNT: '2', GIT_CONFIG_KEY_0: `http.${scope.href}.extraHeader`,
    GIT_CONFIG_VALUE_0: authorization, GIT_CONFIG_KEY_1: 'credential.helper', GIT_CONFIG_VALUE_1: '',
    GIT_TERMINAL_PROMPT: '0',
  };
  const script = `${Object.entries(env).map(([key, value]) => `${key}=${host.sq(value)}`).join(' ')} ${host.quoteArgv(argv)}`;
  const result = await shell.run(script, options);
  const scrub = (value) => String(value || '').split(token).join('[redacted]')
    .split(authorization).join('[redacted]').split(authorization.slice('Authorization: Basic '.length)).join('[redacted]');
  return { ...result, output: scrub(result.output), error: scrub(result.error) };
}

module.exports = { preparePatch, readToken, gitExec };
