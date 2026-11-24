"use strict";

const { ArgParser, UsageError, ArgTypeError } = require("../core/args");

const name = "color";
const help = "色の表記変換・明度調整・混色・コントラスト比の計算を行う";
const usage = "devkit color [--json] [--lighten P] [--darken P] [--mix 色] [--contrast 色] <色>";
const example = "devkit color '#3b82f6' --contrast '#ffffff'";

function numeric(value, min, max, percent = false) {
  if (percent && !value.endsWith("%")) throw new Error();
  const raw = percent ? value.slice(0, -1) : value;
  const number = Number(raw);
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(raw) || !Number.isFinite(number) || number < min || number > max) throw new Error();
  return number;
}

function fromHue(hue, chroma, offset) {
  const wrapped = hue % 360;
  const h = (wrapped < 0 ? wrapped + 360 : wrapped) / 60;
  const x = chroma * (1 - Math.abs(h % 2 - 1));
  const sectors = [[chroma, x, 0], [x, chroma, 0], [0, chroma, x], [0, x, chroma], [x, 0, chroma], [chroma, 0, x]];
  return sectors[Math.floor(h) % 6].map((channel) => (channel + offset) * 255);
}

function fromHsl(h, s, l) {
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  return fromHue(h, chroma, l - chroma / 2);
}

function parseColor(input) {
  const value = input.trim();
  const hex = /^#([\da-f]{3}|[\da-f]{6}|[\da-f]{8})$/i.exec(value);
  if (hex) {
    const digits = hex[1].length === 3 ? [...hex[1]].map((char) => char.repeat(2)).join("") : hex[1];
    const [r, g, b, alpha] = Buffer.from(digits, "hex");
    return { rgb: [r, g, b], alpha: alpha === undefined ? undefined : alpha / 255 };
  }
  const match = /^(rgb|rgba|hsl|hsla|hsv)\(([^()]*)\)$/i.exec(value);
  if (match) {
    const format = match[1].toLowerCase();
    const parts = match[2].split(",").map((part) => part.trim());
    try {
      const hasAlpha = format.endsWith("a");
      if (parts.length !== (hasAlpha ? 4 : 3)) throw new Error();
      const alpha = hasAlpha ? numeric(parts[3], 0, 1) : undefined;
      if (format.startsWith("rgb")) {
        return { rgb: parts.slice(0, 3).map((channel) => numeric(channel, 0, 255)), alpha };
      }
      const h = numeric(parts[0], -Infinity, Infinity);
      const s = numeric(parts[1], 0, 100, true) / 100;
      const level = numeric(parts[2], 0, 100, true) / 100;
      const rgb = format === "hsv" ? fromHue(h, level * s, level * (1 - s)) : fromHsl(h, s, level);
      return { rgb, alpha };
    } catch {
      // 不正な成分も色の形式エラーとして報告する。
    }
  }
  throw new UsageError(`色の形式または成分が不正です: ${input}`);
}

function components(rgb) {
  const [r, g, b] = rgb.map((channel) => channel / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const delta = max - min;
  const l = (max + min) / 2;
  let h = 0;
  if (delta) {
    if (max === r) h = (g - b) / delta;
    else if (max === g) h = (b - r) / delta + 2;
    else h = (r - g) / delta + 4;
    h = (h * 60 + 360) % 360;
  }
  return { h, l, s: delta ? delta / (1 - Math.abs(2 * l - 1)) : 0, v: max, sv: max ? delta / max : 0 };
}

function describe({ rgb, alpha }) {
  const { h, l, s, v, sv } = components(rgb);
  const round = (value) => Number(value.toFixed(2));
  const channels = rgb.map((channel) => Math.round(channel));
  const hex = "#" + Buffer.from(alpha === undefined ? channels : [...channels, Math.round(alpha * 255)]).toString("hex");
  const rgbText = alpha === undefined ? `rgb(${channels.join(", ")})` : `rgba(${channels.join(", ")}, ${Number(alpha.toFixed(4))})`;
  return {
    hex,
    rgb: rgbText,
    hsl: `hsl(${round(h)}, ${round(s * 100)}%, ${round(l * 100)}%)`,
    hsv: `hsv(${round(h)}, ${round(sv * 100)}%, ${round(v * 100)}%)`,
  };
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((channel) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function blend(color, background) {
  const alpha = color.alpha ?? 1;
  return color.rgb.map((channel, i) => channel * alpha + background[i] * (1 - alpha));
}

function contrastOf(foreground, background) {
  const base = blend(background, [255, 255, 255]);
  const a = luminance(blend(foreground, base)), b = luminance(base);
  const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  const grade = (minimum) => ratio >= minimum ? "pass" : "fail";
  return { ratio: Number(ratio.toFixed(2)), normal: { AA: grade(4.5), AAA: grade(7) }, large: { AA: grade(3), AAA: grade(4.5) } };
}

function points(raw) {
  try {
    return numeric(raw, 0, 100);
  } catch {
    throw new ArgTypeError("0〜100 の数値を指定してください");
  }
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addPositional("color");
  parser.addFlag("--json");
  parser.addOption("--lighten", { type: points });
  parser.addOption("--darken", { type: points });
  parser.addOption("--mix");
  parser.addOption("--contrast");
  const args = parser.parse(argv);
  const color = parseColor(args.color);
  const adjustment = (args.lighten ?? 0) - (args.darken ?? 0);
  if (adjustment) {
    const { h, s, l } = components(color.rgb);
    color.rgb = fromHsl(h, s, Math.min(1, Math.max(0, l + adjustment / 100)));
  }
  const result = describe(color);
  if (args.mix !== undefined) {
    const other = parseColor(args.mix);
    const alpha = color.alpha === undefined && other.alpha === undefined
      ? undefined : ((color.alpha ?? 1) + (other.alpha ?? 1)) / 2;
    result.mix = describe({ rgb: color.rgb.map((channel, i) => (channel + other.rgb[i]) / 2), alpha });
  }
  if (args.contrast !== undefined) result.contrast = contrastOf(color, parseColor(args.contrast));
  if (args.json) io.out(JSON.stringify(result) + "\n");
  else {
    const display = (values, prefix = "") => {
      for (const key of ["hex", "rgb", "hsl", "hsv"]) io.out(`${prefix}${key}: ${values[key]}\n`);
    };
    display(result);
    if (result.mix) display(result.mix, "mix ");
    if (result.contrast) {
      const { ratio, normal, large } = result.contrast;
      io.out(`contrast: ${ratio.toFixed(2)}:1\n通常テキスト: AA ${normal.AA}, AAA ${normal.AAA}\n大きなテキスト: AA ${large.AA}, AAA ${large.AAA}\n`);
    }
  }
  return 0;
}

module.exports = { name, help, usage, example, run };
