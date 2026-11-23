"""Validate, compare, bump, and match SemVer 2.0.0 versions."""

import argparse
import operator
import re
import sys
from dataclasses import dataclass


NUMBER = r"(?:0|[1-9][0-9]*)"
IDENTIFIER = r"[0-9A-Za-z-]+"
IDENTIFIERS = rf"{IDENTIFIER}(?:\.{IDENTIFIER})*"
VERSION = re.compile(
    rf"v?({NUMBER})\.({NUMBER})\.({NUMBER})"
    rf"(?:-({IDENTIFIERS}))?(?:\+{IDENTIFIERS})?"
)
TILDE_PARTIAL = re.compile(rf"v?({NUMBER})(?:\.({NUMBER}))?")
WILDCARD = re.compile(rf"v?({NUMBER})(?:\.({NUMBER}))?\.x")
COMPARATOR = re.compile(r"(>=|<=|>|<|=)(.+)")
OPERATORS = {
    ">=": operator.ge, "<=": operator.le, ">": operator.gt,
    "<": operator.lt, "=": operator.eq,
}


def numeric_key(number):
    return len(number), number


def increment(number):
    # SemVer places no limit on the length of numeric identifiers.
    prefix = number.rstrip("9")
    zeros = "0" * (len(number) - len(prefix))
    if not prefix:
        return "1" + zeros
    return prefix[:-1] + str(int(prefix[-1]) + 1) + zeros


@dataclass(frozen=True)
class Version:
    core: tuple
    prerelease: tuple = ()

    @classmethod
    def parse(cls, value):
        match = VERSION.fullmatch(value)
        if not match:
            raise ValueError(f"invalid SemVer: {value!r}")
        major, minor, patch, pre = match.groups()
        prerelease = tuple(pre.split(".")) if pre else ()
        if any(part.isdigit() and len(part) > 1 and part.startswith("0")
               for part in prerelease):
            raise ValueError(f"leading zero in numeric prerelease: {value!r}")
        return cls((major, minor, patch), prerelease)

    def precedence(self):
        prerelease = tuple(
            (0, numeric_key(part)) if part.isdigit() else (1, part)
            for part in self.prerelease
        )
        return tuple(map(numeric_key, self.core)), not self.prerelease, prerelease

    def next_core(self, index):
        return Version(self.core[:index] + (increment(self.core[index]),)
                       + ("0",) * (2 - index))

    def bump(self, part, pre_id):
        if part != "pre":
            return self.next_core(("major", "minor", "patch").index(part))
        pre = self.prerelease
        if not pre:
            pre = (pre_id, "1")
        elif pre[-1].isdigit():
            pre = pre[:-1] + (increment(pre[-1]),)
        else:
            pre += ("1",)
        return Version(self.core, pre)

    def __str__(self):
        core = ".".join(self.core)
        return f"{core}-{'.'.join(self.prerelease)}" if self.prerelease else core


def interval(lower, index):
    return [(">=", lower), ("<", lower.next_core(index))]


def range_conditions(expression):
    terms = expression.split()
    if not terms:
        raise ValueError("empty version range")

    if len(terms) == 1:
        term = terms[0]
        if term in ("*", "x"):
            return []
        if term.startswith("^"):
            lower = Version.parse(term[1:])
            index = next((index for index, value in enumerate(lower.core)
                          if value != "0"), 2)
            return interval(lower, index)
        if term.startswith("~"):
            partial = TILDE_PARTIAL.fullmatch(term[1:])
            if partial:
                major, minor = partial.groups()
                return interval(Version((major, minor or "0", "0")), int(minor is not None))
            return interval(Version.parse(term[1:]), 1)
        wildcard = WILDCARD.fullmatch(term)
        if wildcard:
            major, minor = wildcard.groups()
            return interval(Version((major, minor or "0", "0")), int(minor is not None))
        if not COMPARATOR.fullmatch(term):
            return [("=", Version.parse(term))]

    conditions = []
    for term in terms:
        match = COMPARATOR.fullmatch(term)
        if not match:
            raise ValueError(f"invalid comparator: {term!r}")
        op, value = match.groups()
        conditions.append((op, Version.parse(value)))
    return conditions


def prerelease_id(value):
    if not re.fullmatch(IDENTIFIER, value) or (
        value.isdigit() and len(value) > 1 and value.startswith("0")
    ):
        raise argparse.ArgumentTypeError(f"invalid prerelease identifier: {value!r}")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", allow_abbrev=False).add_argument("version")
    compare = commands.add_parser("compare", allow_abbrev=False)
    compare.add_argument("a")
    compare.add_argument("b")
    bump = commands.add_parser("bump", allow_abbrev=False)
    bump.add_argument("version")
    bump.add_argument("--part", required=True, choices=("major", "minor", "patch", "pre"))
    bump.add_argument("--pre-id", default="rc", type=prerelease_id)
    satisfies = commands.add_parser("satisfies", allow_abbrev=False)
    satisfies.add_argument("version")
    satisfies.add_argument("range")
    args = parser.parse_args(argv)

    try:
        if args.command == "compare":
            a = Version.parse(args.a).precedence()
            b = Version.parse(args.b).precedence()
            print((a > b) - (a < b))
            return 0

        version = Version.parse(args.version)
        if args.command == "check":
            print("valid")
        elif args.command == "bump":
            print(version.bump(args.part, args.pre_id))
        else:
            conditions = range_conditions(args.range)
            precedence = version.precedence()
            matches = all(OPERATORS[op](precedence, bound.precedence())
                          for op, bound in conditions)
            print("true" if matches else "false")
            return 0 if matches else 1
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1 if args.command == "check" else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
