"""Evaluate arithmetic without executing Python code."""

import ast
import math
import operator
import re

_TOKEN = re.compile(r"[0-9]+(?:\.[0-9]*)?|\.[0-9]+|[+*/()%\-]|\s+")
_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def evaluate(expression):
    """Evaluate +, -, *, / and postfix percentage (50% equals 0.5)."""
    expression = expression.strip().replace("×", "*").replace("÷", "/").replace("−", "-")
    if not expression or len(expression) > 500:
        raise ValueError("1～500文字の式を入力してください。")

    parts = []
    groups = []
    operand_start = None
    position = 0
    for match in _TOKEN.finditer(expression):
        if match.start() != position:
            raise ValueError("使用できない文字があります。")
        position = match.end()
        token = match.group()
        if token.isspace():
            continue
        if token == "%":
            if operand_start is None:
                raise ValueError("% の前に数値または括弧が必要です。")
            parts.insert(operand_start, "(")
            parts.extend(("/", "100", ")"))
            operand_start = None
            continue
        if token == "(":
            groups.append(len(parts))
            operand_start = None
        elif token == ")":
            if not groups:
                raise ValueError("括弧が対応していません。")
            operand_start = groups.pop()
        elif token in "+-*/":
            if parts and parts[-1] == token and token in "*/":
                raise ValueError("使用できない演算子です。")
            operand_start = None
        else:
            operand_start = len(parts)
        parts.append(token)

    if position != len(expression) or groups:
        raise ValueError("式を確認してください。")
    try:
        tree = ast.parse(" ".join(parts), mode="eval")
        return _evaluate_node(tree.body)
    except (SyntaxError, ArithmeticError) as error:
        raise ValueError("計算できない式です。") from error


def _evaluate_node(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        result = node.value
    elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        result = _BINARY[type(node.op)](_evaluate_node(node.left), _evaluate_node(node.right))
    elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        result = _UNARY[type(node.op)](_evaluate_node(node.operand))
    else:
        raise ValueError("使用できない式です。")
    if isinstance(result, float) and not math.isfinite(result):
        raise ValueError("計算結果が大きすぎます。")
    return result
