'use strict';
// パースと束縛の解決だけを行う。設定・テスト・import先のコードを実行しない。
const path = require('path');
const { createHash } = require('node:crypto');
const parser = require('@babel/parser');
const traverse = require('@babel/traverse').default;

const hash = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const MODULES = new Set(['vitest', '@jest/globals', 'node:test', '@playwright/test', 'playwright/test']);
const TESTS = new Set(['test', 'it']);
const SUITES = new Set(['describe', 'suite']);
const MODIFIERS = new Set(['skip', 'only', 'todo', 'fails', 'concurrent', 'sequential']);
const AUXILIARY = new Set(['beforeEach', 'afterEach', 'beforeAll', 'afterAll', 'before', 'after', 'use', 'extend', 'info', 'step', 'setTimeout', 'configure']);
const OMIT = new Set(['start', 'end', 'loc', 'extra', 'leadingComments', 'trailingComments', 'innerComments', 'comments', 'tokens', 'errors']);
const LITERALS = new Set(['StringLiteral', 'NumericLiteral', 'BooleanLiteral', 'BigIntLiteral', 'DecimalLiteral']);

function javascript(source, file, filename, options = {}) {
  const tests = []; const diagnostics = [];
  const ast = parser.parse(source, { sourceType: 'unambiguous', plugins: [
    ...( /\.[cm]?tsx?$/i.test(filename) ? ['typescript'] : []),
    ...( /\.[jt]sx$/i.test(filename) ? ['jsx'] : []),
  ], errorRecovery: false });
  const providers = new Map(); const imports = new Map();
  const extensions = [];
  const sourceKey = (name) => name.startsWith('.') ? path.resolve(path.dirname(filename), name).split(path.sep).join('/') : name;
  function register(binding, framework, name, fixture = false) {
    if (binding) providers.set(binding, { framework, name, fixture });
  }
  function imported(binding, framework, name) {
    imports.set(binding, `${sourceKey(framework)}:${name}`);
    if (MODULES.has(framework)) register(binding, framework, name);
  }
  traverse(ast, {
    ImportDeclaration(p) {
      const framework = p.node.source.value;
      for (const specifier of p.get('specifiers')) {
        const name = specifier.isImportSpecifier() ? (specifier.node.imported.name || specifier.node.imported.value)
          : specifier.isImportNamespaceSpecifier() ? '*' : framework === 'node:test' ? 'test' : 'default';
        imported(p.scope.getBinding(specifier.node.local.name), framework, name);
      }
    },
    VariableDeclarator(p) {
      const init = p.get('init');
      if (!init.isCallExpression()) return;
      const args = init.get('arguments');
      if (init.get('callee').isIdentifier({ name: 'require' }) && !init.scope.getBinding('require') && args.length === 1 && args[0].isStringLiteral()) {
        const framework = args[0].node.value;
        const id = p.get('id');
        if (id.isIdentifier()) imported(p.scope.getBinding(id.node.name), framework, framework === 'node:test' ? 'test' : '*');
        else if (id.isObjectPattern()) for (const property of id.get('properties')) {
          if (property.isObjectProperty() && property.get('value').isIdentifier()) {
            imported(p.scope.getBinding(property.node.value.name), framework, property.node.key.name || property.node.key.value);
          }
        }
      } else if (p.get('id').isIdentifier() && init.get('callee').isMemberExpression() && init.node.callee.property.name === 'extend') {
        extensions.push(p);
      }
    },
  });
  for (const p of extensions) {
    const base = declaration(p.get('init').get('callee').get('object'));
    if (base && TESTS.has(base.name)) register(p.scope.getBinding(p.node.id.name), base.framework, 'test', true);
  }

  function declaration(p) {
    if (!p?.node) return null;
    if (p.isIdentifier()) {
      const binding = p.scope.getBinding(p.node.name);
      const provider = providers.get(binding);
      if (provider) return { ...provider, modifiers: [], table: null, root: binding };
      if (!binding && options.globals && [...TESTS, ...SUITES].includes(p.node.name)) {
        return { framework: 'globals', name: p.node.name, modifiers: [], table: null, root: null };
      }
    }
    if (p.isMemberExpression() && !p.node.computed) {
      const base = declaration(p.get('object'));
      if (!base) return null;
      const property = p.node.property.name;
      if (base.name === '*') return { ...base, name: property };
      if (SUITES.has(property) || AUXILIARY.has(property)) return { ...base, name: property };
      return { ...base, modifiers: [...base.modifiers, property] };
    }
    if (p.isCallExpression()) {
      const base = declaration(p.get('callee'));
      if (!base) return null;
      const last = base.modifiers.at(-1);
      if (last === 'each' || last === 'for') return { ...base, table: p.get('arguments')[0] || null };
      if (last === 'skipIf' || last === 'runIf') return { ...base, guards: [...(base.guards || []), p.node.arguments] };
    }
    return null;
  }

  function normalize(node, { identifiers = new WeakMap(), structural = false } = {}, parent = null, key = '') {
    if (node == null || typeof node !== 'object') return node;
    if (Array.isArray(node)) return node.map((n) => normalize(n, { identifiers, structural }, parent, key));
    if (node.type === 'Identifier' && identifiers.has(node)) return { type: 'Identifier', name: identifiers.get(node) };
    if (LITERALS.has(node.type)) {
      const property = parent && ((['ObjectProperty', 'ObjectMethod'].includes(parent.type) && key === 'key')
        || (['MemberExpression', 'OptionalMemberExpression'].includes(parent.type) && key === 'property'));
      return { type: node.type, value: structural && !property ? '<value>' : node.value };
    }
    return Object.fromEntries(Object.keys(node).filter((k) => !OMIT.has(k)).sort().map((k) => [k, normalize(node[k], { identifiers, structural }, node, k)]));
  }

  function fingerprints(fn) {
    const inside = (binding) => binding && (binding.scope === fn.scope || Boolean(binding.scope.path.findParent((p) => p === fn)));
    const bindings = new Set();
    fn.traverse({ Identifier(p) { const b = p.scope.getBinding(p.node.name); if (inside(b)) bindings.add(b); } });
    for (const b of Object.values(fn.scope.bindings)) if (inside(b)) bindings.add(b);
    // 関数・クラスの名前は.name等から観測できるため、通常のローカル変数のようには消さない。
    const namedValue = (b) => b.path.isFunctionDeclaration() || b.path.isClassDeclaration()
      || (b.path.isVariableDeclarator() && (b.path.get('init').isFunction() || b.path.get('init').isClassExpression()));
    const locals = new Map([...bindings].filter((b) => !namedValue(b)).sort((a, b) => a.identifier.start - b.identifier.start).map((b, i) => [b, `local:${i}`]));
    const identifiers = new WeakMap();
    fn.traverse({ Identifier(p) {
      if ((!p.isReferencedIdentifier() && !p.isBindingIdentifier()) || (p.parentPath.isObjectProperty() && p.key === 'key' && !p.parent.computed)) return;
      const binding = p.scope.getBinding(p.node.name);
      if (locals.has(binding)) identifiers.set(p.node, locals.get(binding));
      else if (imports.has(binding)) identifiers.set(p.node, `import:${imports.get(binding)}`);
      else if (binding && !inside(binding)) identifiers.set(p.node, `external:${file}:${binding.identifier.start}:${binding.identifier.name}`);
    } });
    return { exact: hash(normalize(fn.node.body, { identifiers })), structural: hash(normalize(fn.node.body, { identifiers, structural: true })),
      parameters: hash(normalize(fn.node.params, { identifiers })) };
  }

  function declarationCall(statement) {
    if (!statement.isExpressionStatement()) return null;
    let call = statement.get('expression');
    if (call.isAwaitExpression()) call = call.get('argument');
    if (!call.isCallExpression()) return null;
    const d = declaration(call.get('callee'));
    return d && (TESTS.has(d.name) || SUITES.has(d.name)) ? { call, d } : null;
  }
  function outside(statements) {
    return statements.filter((p) => !declarationCall(p)).map((p) => normalize(p.node));
  }
  function identitySensitive(node) {
    if (!node || typeof node !== 'object') return false;
    if (node.type === 'ThisExpression') return true;
    if (['MemberExpression', 'OptionalMemberExpression'].includes(node.type) && !node.computed
        && ['name', 'title', 'titlePath', 'currentTest', 'getState'].includes(node.property?.name)) return true;
    return Object.values(node).some((value) => typeof value === 'object' && value !== null && identitySensitive(value));
  }
  function tableRows(p) {
    if (!p) return null;
    if (p.isIdentifier()) {
      const binding = p.scope.getBinding(p.node.name);
      if (!binding || binding.kind !== 'const' || !binding.constant || !binding.path.isVariableDeclarator()) return false;
      // const配列でもpushや別関数への受け渡しがあれば値を決めつけない。
      if (!binding.referencePaths.every((r) => {
        if (!r.parentPath.isCallExpression() || !r.parent.arguments.includes(r.node)) return false;
        const declared = declaration(r.parentPath.get('callee'));
        return declared && (TESTS.has(declared.name) || SUITES.has(declared.name)) && ['each', 'for'].includes(declared.modifiers.at(-1));
      })) return false;
      p = binding.path.get('init');
    }
    if (!p.isArrayExpression() || p.node.elements.some((n) => !n)) return false;
    function literal(node) {
      if (node.type === 'NullLiteral' || LITERALS.has(node.type)) return true;
      if (node.type === 'UnaryExpression' && ['+', '-'].includes(node.operator)) return literal(node.argument);
      if (node.type === 'ArrayExpression') return node.elements.every((n) => n && literal(n));
      if (node.type === 'ObjectExpression') return node.properties.every((n) => n.type === 'ObjectProperty' && !n.computed && literal(n.value));
      return false;
    }
    if (!p.node.elements.every(literal)) return false;
    return p.node.elements.map((node) => ({ key: hash(normalize(node)), line: node.loc.start.line }));
  }
  const handled = new Set();
  function scan(statements, context, titles, inherited = [], inheritedComparable = true) {
    const scopeContext = [...context, outside(statements)];
    let barrier = 0;
    for (const statement of statements) {
      const result = declarationCall(statement);
      if (!result) { barrier++; continue; }
      const { call, d } = result;
      handled.add(call.node);
      const args = call.get('arguments');
      const title = args[0]?.isStringLiteral() ? args[0].node.value
        : args[0]?.isTemplateLiteral() && !args[0].node.expressions.length ? args[0].node.quasis[0].value.cooked : null;
      let fn = [...args].reverse().find((p) => p.isArrowFunctionExpression() || p.isFunctionExpression());
      let callbackArg = fn;
      if (!fn && args.at(-1)?.isIdentifier()) {
        callbackArg = args.at(-1);
        const binding = args.at(-1).scope.getBinding(args.at(-1).node.name);
        if (binding?.path.isFunctionDeclaration()) fn = binding.path;
        else if (binding?.path.isVariableDeclarator() && (binding.path.get('init').isArrowFunctionExpression() || binding.path.get('init').isFunctionExpression())) fn = binding.path.get('init');
      }
      if (!fn) {
        if (!d.modifiers.some((m) => ['todo', 'skip'].includes(m))) diagnostics.push({ file, line: call.node.loc.start.line, reason: 'テストまたはsuiteのコールバックを静的に読めません' });
        continue;
      }
      const rows = tableRows(d.table);
      let reflection = false;
      fn.traverse({ CallExpression(p) { if (p.get('callee').isIdentifier() && ['eval', 'Function'].includes(p.node.callee.name)) reflection = true; },
        NewExpression(p) { if (p.get('callee').isIdentifier({ name: 'Function' })) reflection = true; } });
      const guardsStatic = !d.guards || d.guards.flat().every((n) => n.type === 'BooleanLiteral');
      const comparable = inheritedComparable && title != null && rows !== false && !(d.table && !rows?.length) && guardsStatic && !reflection
        && !d.modifiers.some((m) => !MODIFIERS.has(m) && !['each', 'for', 'skipIf', 'runIf'].includes(m));
      if (!comparable) diagnostics.push({ file, line: call.node.loc.start.line, reason: '動的なタイトル・表・宣言修飾子・反射的処理または親suiteを静的に確定できません' });
      const mods = [...inherited, ...d.modifiers, ...(d.guards ? [normalize(d.guards)] : [])];
      const name = title ?? '(動的なタイトル)';
      const fullTitle = [...titles, name].join(' > ');
      const provider = d.fixture ? `fixture:${file}:${d.root.identifier.start}` : d.framework;
      const signature = [...scopeContext, barrier, mods, rows ? normalize(d.table.node) : null, provider,
        args.slice(1).filter((p) => p !== callbackArg).map((p) => normalize(p.node))];
      if (SUITES.has(d.name)) {
        if (rows?.length) tests.push({ file, line: call.node.loc.start.line, title: name, fullTitle, framework: d.framework, comparable: false, staticTitle: false, parameterized: true, rows });
        if (fn.get('body').isBlockStatement()) scan(fn.get('body').get('body'), signature, [...titles, name], mods, comparable);
        else diagnostics.push({ file, line: call.node.loc.start.line, reason: '式だけのsuiteコールバックは解析対象外です' });
        continue;
      }
      const callbackContext = [fn.node.type, fn.node.async, fn.node.generator, fn.node.params.length > 1 ? fullTitle : null];
      // thisやテスト実行コンテキストの名前を観測すると、タイトル自身が振る舞いに関わる。
      const observesIdentity = identitySensitive(fn.node) || identitySensitive(scopeContext);
      const body = fingerprints(fn);
      tests.push({ file, line: call.node.loc.start.line, title: name, fullTitle, framework: d.framework,
        comparable, staticTitle: title != null, parameterized: Boolean(d.table), rows: Array.isArray(rows) ? rows : [],
        trivial: fn.get('body').isBlockStatement() && !fn.node.body.body.length,
        context: hash([signature, callbackContext, body.parameters, observesIdentity ? fullTitle : null, [...imports.values()].sort()]),
        body: { exact: body.exact, structural: body.structural } });
    }
  }
  let program;
  traverse(ast, { Program(p) { program = p; p.stop(); } });
  scan(program.get('body'), [], []);
  traverse(ast, { CallExpression(p) {
    const d = declaration(p.get('callee'));
    if (d && (TESTS.has(d.name) || SUITES.has(d.name)) && !handled.has(p.node)
        && (p.get('arguments')[0]?.isStringLiteral() || p.get('arguments').some((a) => a.isFunction()))) {
      diagnostics.push({ file, line: p.node.loc.start.line, reason: 'ループ・条件分岐・関数の中などのテスト登録は解析対象外です' });
    }
  }, TaggedTemplateExpression(p) {
    const d = declaration(p.get('tag'));
    if (d && (TESTS.has(d.name) || SUITES.has(d.name)) && ['each', 'for'].includes(d.modifiers.at(-1))) {
      diagnostics.push({ file, line: p.node.loc.start.line, reason: 'タグ付きテンプレートのパラメーター表は解析対象外です' });
    }
  } });
  return { tests, diagnostics };
}

module.exports = { javascript };
