"""PythonのテストをASTだけで読む。入力モジュールのimport・実行・evalはしない。"""
import ast
import hashlib
import json
import sys
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def plain(node):
    if isinstance(node, ast.AST):
        return {"type": type(node).__name__, **{key: plain(value) for key, value in ast.iter_fields(node)}}
    if isinstance(node, list):
        return [plain(value) for value in node]
    if isinstance(node, (str, int, float, bool)) or node is None:
        return node
    return repr(node)


def extract(source, file):
    tree = ast.parse(source, filename=file)
    tests, diagnostics = [], []
    aliases = {}
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    writes_by_name, reads_by_name, row_cache = {}, {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            target = writes_by_name if isinstance(node.ctx, ast.Store) else reads_by_name
            target.setdefault(node.id, []).append(node)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for name in node.names:
                aliases[name.asname or name.name.split(".")[0]] = name.name
        if isinstance(node, ast.ImportFrom):
            for name in node.names:
                aliases[name.asname or name.name] = f"{node.module}.{name.name}"

    def dotted(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return dotted(node.value) + "." + node.attr
        return ""

    def is_class(node):
        return isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or any(dotted(base).endswith("TestCase") for base in node.bases))

    def is_test(node):
        return (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
                and not any(dotted(d.func if isinstance(d, ast.Call) else d) == "pytest.fixture" for d in node.decorator_list))

    # ヘルパー・fixture・import・モジュール初期化は、テストのタイトルと独立した実行条件に含める。
    module_context = plain([node for node in tree.body if not is_test(node) and not is_class(node)])
    module_context.append({"relative_imports": [f"{Path(file).resolve().parent}:{n.level}:{n.module}"
        for n in tree.body if isinstance(n, ast.ImportFrom) and n.level]})

    def normalized(body, local_names, structural=False):
        def visit(node, parent=None, field=""):
            if isinstance(node, list):
                return [visit(n, parent, field) for n in node]
            if isinstance(node, ast.Name):
                return {"type": "Name", "id": local_names.get(node.id, node.id), "ctx": type(node.ctx).__name__}
            if isinstance(node, ast.Constant):
                key = (isinstance(parent, ast.Dict) and field == "keys") or (isinstance(parent, ast.Subscript) and field == "slice")
                return {"type": "Constant", "kind": type(node.value).__name__, "value": "<value>" if structural and not key else plain(node.value)}
            if isinstance(node, ast.AST):
                return {"type": type(node).__name__, **{key: visit(value, node, key) for key, value in ast.iter_fields(node)}}
            return plain(node)
        return digest(visit(body))

    def read_rows(decorator):
        if not isinstance(decorator, ast.Call) or dotted(decorator.func) != "pytest.mark.parametrize":
            return None
        if len(decorator.args) < 2:
            return False
        table = decorator.args[1]
        if isinstance(table, ast.Name):
            writes = writes_by_name.get(table.id, [])
            reads = reads_by_name.get(table.id, [])
            assignment = parents.get(id(writes[0])) if len(writes) == 1 else None
            if not isinstance(assignment, (ast.Assign, ast.AnnAssign)) or assignment not in tree.body or assignment.lineno >= decorator.lineno:
                return False
            if isinstance(assignment, ast.Assign) and (len(assignment.targets) != 1 or not isinstance(assignment.targets[0], ast.Name)):
                return False
            for read in reads:
                parent = parents.get(id(read))
                if not isinstance(parent, ast.Call) or dotted(parent.func) != "pytest.mark.parametrize" or len(parent.args) < 2 or parent.args[1] is not read:
                    return False
            table = assignment.value
        if not isinstance(table, (ast.List, ast.Tuple)):
            return False

        def literal(node):
            if isinstance(node, ast.Constant):
                return True
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return literal(node.operand)
            if isinstance(node, (ast.List, ast.Tuple)):
                return all(literal(n) for n in node.elts)
            if isinstance(node, ast.Dict):
                return all(k is not None and literal(k) and literal(v) for k, v in zip(node.keys, node.values))
            return False

        values = table.elts
        if not all(literal(value) for value in values):
            return False
        return [{"key": digest(plain(value)), "line": value.lineno} for value in values]

    def rows_for(decorator):
        key = id(decorator)
        if key not in row_cache:
            row_cache[key] = read_rows(decorator)
        return row_cache[key]

    def collect(fn, cls=None):
        class_context = plain([node for node in cls.body if not is_test(node)]) if cls else []
        context = [module_context, class_context, plain(cls.bases) if cls else [], plain(fn.args), plain(fn.returns), plain(fn.decorator_list), isinstance(fn, ast.AsyncFunctionDef)]
        unsupported = any(isinstance(n, (ast.Global, ast.Nonlocal, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
                          or (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn)
                          or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("eval", "exec", "locals", "globals", "vars"))
                          for n in ast.walk(fn))
        if cls and any(dotted(base) not in ("unittest.TestCase", "unittest.IsolatedAsyncioTestCase", "object") for base in cls.bases):
            unsupported = True
        all_rows = []
        for decorator in fn.decorator_list:
            name = dotted(decorator.func if isinstance(decorator, ast.Call) else decorator)
            if not (name.startswith("pytest.mark.") or name in ("staticmethod", "classmethod", "unittest.skip", "unittest.skipIf", "unittest.skipUnless", "unittest.expectedFailure")):
                unsupported = True
            rows = rows_for(decorator)
            if rows is False or rows == []:
                unsupported = True
            elif rows:
                all_rows.extend(rows)
        # 複数のparametrizeは直積になるため、それぞれの表の行を別々に比較する。
        if sum(rows_for(d) is not None for d in fn.decorator_list) > 1:
            all_rows = []
            unsupported = True
        arguments = {arg.arg for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]}
        local_names = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id not in arguments:
                local_names.setdefault(node.id, f"local:{len(local_names)}")
        body = list(fn.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        full_title = f"{cls.name} > {fn.name}" if cls else fn.name
        identity_sensitive = "request" in arguments or any(isinstance(n, ast.Attribute) and n.attr in ("_testMethodName", "_testMethodDoc", "currentTest")
            for scope in ([fn, cls] if cls else [fn]) for n in ast.walk(scope))
        if identity_sensitive:
            context.append(full_title)
        if unsupported:
            diagnostics.append({"file": file, "line": fn.lineno, "reason": "Pythonの動的な表・継承・デコレーター・スコープ/名前の観測を静的に確定できません"})
        tests.append({"file": file, "line": fn.lineno, "title": fn.name, "fullTitle": full_title,
                      "framework": "unittest" if cls and any(dotted(base).startswith("unittest.") for base in cls.bases) else "pytest", "comparable": not unsupported,
                      "staticTitle": True, "parameterized": any(rows_for(d) is not None for d in fn.decorator_list),
                      "rows": all_rows, "trivial": not body or all(isinstance(n, ast.Pass) for n in body),
                      "context": digest(context), "body": {"exact": normalized(body, local_names), "structural": normalized(body, local_names, True)}})

    handled = set()
    for node in tree.body:
        if is_test(node):
            collect(node)
            handled.add(id(node))
        elif is_class(node):
            for fn in node.body:
                if is_test(fn):
                    collect(fn, node)
                    handled.add(id(fn))
    for node in ast.walk(tree):
        if is_test(node) and id(node) not in handled:
            diagnostics.append({"file": file, "line": node.lineno, "reason": "条件分岐・関数の中などのPythonテスト登録は解析対象外です"})
    return {"tests": tests, "diagnostics": diagnostics}


if __name__ == "__main__":
    request = {}
    try:
        request = json.load(sys.stdin)
        print(json.dumps(extract(request["source"], request["file"]), ensure_ascii=False))
    except (SyntaxError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"tests": [], "diagnostics": [{"file": request.get("file", ""), "line": getattr(error, "lineno", 0), "reason": str(error)}]}))
