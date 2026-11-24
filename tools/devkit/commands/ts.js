"use strict";

const { ArgParser, UsageError } = require("../core/args");
const { pyRepr, pyJsonStringify } = require("../core/py");
const { formatIso, formatOffset, localZoneName } = require("../core/tz");
const {
  isIanaZone, parseIsoDateTime, isoToEpochMicro, floorSeconds, zoneOffset,
  MICROS_PER_SECOND, MIN_SECOND, MAX_SECOND, EARLY_SAMPLE, DAY_SECONDS,
} = require("../core/datetime");

const NUMBER = /^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/;
const help = "Unix タイムスタンプと ISO 8601 日時を相互変換します";
const usage = "devkit ts [--unit {s,ms,auto}] [--tz TZ] [--now] [--json] [values ...]";

function roundRatio(numerator, denominator) {
  let quotient = numerator / denominator;
  let remainder = numerator % denominator;
  if (remainder < 0n) {
    quotient -= 1n;
    remainder += denominator;
  }
  const twice = remainder * 2n;
  return twice > denominator || (twice === denominator && quotient % 2n !== 0n)
    ? quotient + 1n : quotient;
}

function parseEpoch(value, unit) {
  const [whole, fraction = ""] = value.split(".");
  const numerator = BigInt(whole + fraction);
  const denominator = 10n ** BigInt(fraction.length);
  const threshold = 1000000000000n * denominator;
  const milliseconds = unit === "ms" || (unit === "auto" &&
    (numerator >= threshold || numerator <= -threshold));
  const epoch = roundRatio(numerator * (milliseconds ? 1000n : MICROS_PER_SECOND), denominator);
  const microsPerDay = BigInt(DAY_SECONDS) * MICROS_PER_SECOND;
  const days = (epoch < 0n ? epoch - microsPerDay + 1n : epoch) / microsPerDay;
  if (days < -2147483648n || days > 2147483647n) {
    throw new RangeError("Python int too large to convert to C int");
  }
  if (days < -999999999n || days > 999999999n) {
    throw new RangeError(`days=${days}; must have magnitude <= 999999999`);
  }
  return epoch;
}

function checkRange(seconds) {
  if (seconds < MIN_SECOND || seconds > MAX_SECOND) throw new RangeError("date value out of range");
}

function rfc3339(epoch, zone) {
  const seconds = floorSeconds(epoch);
  const offset = zoneOffset(zone, seconds);
  checkRange(seconds + offset);
  if (offset % 60 === 0 && seconds >= EARLY_SAMPLE) return formatIso(epoch, zone);
  const rounded = Number(roundRatio(BigInt(offset), 60n)) * 60;
  checkRange(seconds + rounded);
  const shifted = epoch + BigInt(rounded) * MICROS_PER_SECOND;
  return formatIso(shifted, "UTC").slice(0, -6) + formatOffset(rounded);
}

function epochNumber(epoch, scale) {
  if (epoch % scale === 0n) return epoch / scale;
  // Convert the decimal quotient directly so the numerator is not rounded first.
  const magnitude = epoch < 0n ? -epoch : epoch;
  const digits = scale.toString().length - 1;
  const fraction = (magnitude % scale).toString().padStart(digits, "0");
  return Number(`${epoch < 0n ? "-" : ""}${magnitude / scale}.${fraction}`);
}

function describe(source, epoch, zone, zoneName) {
  checkRange(floorSeconds(epoch));
  return {
    input: source,
    epoch_s: epochNumber(epoch, MICROS_PER_SECOND),
    epoch_ms: epochNumber(epoch, 1000n),
    utc: formatIso(epoch, "UTC"),
    local: rfc3339(epoch, zone),
    timezone: zoneName,
  };
}

function epochText(value) {
  if (typeof value === "bigint") return value.toString();
  if (Math.abs(value) < 0.0001) {
    return value.toExponential().replace(/e(-?)(\d+)$/, (_, sign, exponent) =>
      `e${sign || "+"}${exponent.padStart(2, "0")}`);
  }
  return Number.isInteger(value) ? `${value}.0` : String(value);
}

function run(argv, io) {
  const parser = new ArgParser({
    prog: "devkit ts", usage, description: "Convert Unix timestamps and ISO 8601 dates.",
    negativePattern: NUMBER,
  });
  parser.addPositional("values", { nargs: "*", help: "Unix epoch or ISO 8601 values" });
  parser.addOption("--unit", { key: "unit", choices: ["s", "ms", "auto"], def: "auto" });
  parser.addOption("--tz", { key: "zone", help: "IANA time zone name" });
  parser.addFlag("--now", { key: "now", help: "include the current time first" });
  parser.addFlag("--json", { key: "json", help: "output JSON" });
  const args = parser.parse(argv);
  if (args.zone !== undefined && !isIanaZone(args.zone)) {
    throw new UsageError(`invalid IANA time zone: ${pyRepr(args.zone)}`);
  }
  const zone = args.zone ?? localZoneName();
  const zoneName = args.zone ?? "Local";
  const stdinMode = args.values.length === 0 && !io.stdinIsTTY;
  const results = [];
  let printed = false;

  function emit(result, epochInput) {
    if (args.json) {
      results.push(result);
      return;
    }
    const utc = `UTC: ${result.utc}`;
    const local = `${result.timezone}: ${result.local}`;
    const epochs = [`Epoch (s): ${epochText(result.epoch_s)}`, `Epoch (ms): ${epochText(result.epoch_ms)}`];
    const lines = epochInput ? [local, utc, ...epochs] : [...epochs, utc, local];
    io.out((printed ? "\n" : "") + lines.join("\n") + "\n");
    printed = true;
  }

  if (args.now) emit(describe("now", BigInt(Date.now()) * 1000n, zone, zoneName), true);

  const values = stdinMode
    ? io.readStdin().toString("utf8").split(/\r\n?|\n/).map((line) => line.trim()).filter(Boolean)
    : args.values;
  const count = values.length + Number(args.now);
  if (!count) throw new UsageError("provide a value, nonempty standard input, or --now");
  let failed = false;
  for (const source of values) {
    const value = source.trim();
    const epochInput = NUMBER.test(value);
    try {
      const epoch = epochInput ? parseEpoch(value, args.unit) :
        isoToEpochMicro(parseIsoDateTime(value), zone, args.zone === undefined);
      emit(describe(source, epoch, zone, zoneName), epochInput);
    } catch (error) {
      if (!(error instanceof RangeError)) throw error;
      io.err(`${pyRepr(source)}: ${error.message}\n`);
      failed = true;
    }
  }
  if (args.json) io.out(pyJsonStringify(count === 1 && results.length ? results[0] : results) + "\n");
  return failed ? (stdinMode ? 1 : 2) : 0;
}

module.exports = { name: "ts", help, usage, example: "devkit ts 0", run };
