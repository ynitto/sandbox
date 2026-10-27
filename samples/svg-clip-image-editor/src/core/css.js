// CSS を「セレクタと、宣言されているプロパティ名」の組に分ける。
// 契約の検査（D4）が要るのはこれだけなので、値やカスケードは読まない。

/**
 * 小さな CSS の読み手。@media などの中の規則も拾う（条件は見ない＝厳しめに判定する）。
 * @returns {{ selector: string, properties: string[] }[]}
 */
export function parseCssRules(text) {
  const src = String(text ?? '').replace(/\/\*[\s\S]*?\*\//g, '');
  const rules = [];
  const re = /([^{}]*)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(src))) {
    let selector = m[1].trim();
    // "@media screen { g { … } }" の "g" のように、直前の @ 規則の頭が混ざらないようにする
    const lastBrace = selector.lastIndexOf('{');
    if (lastBrace >= 0) selector = selector.slice(lastBrace + 1).trim();
    if (!selector || selector.startsWith('@')) continue;
    rules.push({ selector, properties: declarationNames(m[2]) });
  }
  return rules;
}

/** "a: 1; b: 2" → ['a', 'b']（小文字） */
export function declarationNames(text) {
  return String(text ?? '')
    .split(';')
    .map((decl) => decl.split(':')[0].trim().toLowerCase())
    .filter(Boolean);
}
