"use strict";

const CALC_ERROR = "計算できない式です。";
const TOKEN = /[0-9]+(?:\.[0-9]*)?|\.[0-9]+|[+*/()%\-]|\s+/y;

function tokenize(expression) {
  const tokens = [];
  let depth = 0;
  let position = 0;
  while (position < expression.length) {
    TOKEN.lastIndex = position;
    const match = TOKEN.exec(expression);
    if (!match) throw new Error("使用できない文字があります。");
    position = TOKEN.lastIndex;
    const [token] = match;
    if (!token.trim()) continue;

    const previous = tokens.at(-1);
    if (token === "(") depth++;
    if (token === ")" && --depth < 0) throw new Error("括弧が対応していません。");
    if (token === "%" && !(previous === ")" || /^[.0-9]/.test(previous ?? ""))) {
      throw new Error("% の前に数値または括弧が必要です。");
    }
    if ((token === "*" || token === "/") && previous === token) {
      throw new Error("使用できない演算子です。");
    }
    tokens.push(token);
  }
  if (depth) throw new Error("括弧が対応していません。");
  return tokens;
}

function evaluate(expression) {
  const input = expression.trim().replaceAll("×", "*").replaceAll("÷", "/").replaceAll("−", "-");
  if (!input || input.length > 500) throw new Error("1～500文字の式を入力してください。");
  const tokens = tokenize(input);
  let index = 0;

  function finite(value) {
    if (!Number.isFinite(value)) throw new Error(CALC_ERROR);
    return value;
  }

  function operand() {
    const token = tokens[index++];
    if (token === "+") return operand();
    if (token === "-") return -operand();

    let value;
    if (token === "(") {
      value = sum();
      if (tokens[index++] !== ")") throw new Error(CALC_ERROR);
    } else {
      if (token === undefined || !/^[.0-9]/.test(token) || /^0+[1-9][0-9]*$/.test(token)) {
        throw new Error(CALC_ERROR);
      }
      value = finite(Number(token));
    }
    if (tokens[index] === "%") {
      index++;
      value /= 100;
    }
    return value;
  }

  function product() {
    let value = operand();
    while (tokens[index] === "*" || tokens[index] === "/") {
      const operator = tokens[index++];
      const right = operand();
      value = finite(operator === "*" ? value * right : value / right);
    }
    return value;
  }

  function sum() {
    let value = product();
    while (tokens[index] === "+" || tokens[index] === "-") {
      const operator = tokens[index++];
      const right = product();
      value = finite(operator === "+" ? value + right : value - right);
    }
    return value;
  }

  const result = sum();
  if (index !== tokens.length) throw new Error(CALC_ERROR);
  return result;
}

module.exports = { evaluate };
