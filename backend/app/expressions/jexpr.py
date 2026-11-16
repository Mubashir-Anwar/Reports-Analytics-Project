"""Safe interpreter for the Java subset used in Jasper report expressions.

No eval(), no Java, no attribute access outside a fixed whitelist. Supports:
  literals, $F{} $P{} $V{} tokens, + - * / %, comparison, && || !, ternary,
  string methods (equals, trim, isEmpty, length, substring, ...),
  new DecimalFormat(p).format(n), new SimpleDateFormat(p).format(d) / .parse(s),
  new Date(), Double.parseDouble, Integer.parseInt, new Double(x), Math.*, String.valueOf.
Raises JExprError on anything unsupported or on a runtime failure (callers blank the cell).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Callable

MAX_EXPRESSION_CHARS = 4000
MAX_STRING = 100_000
MAX_DEPTH = 60


class JExprError(ValueError):
    pass


# ------------------------------------------------------------------ tokenizer
_TOKEN_RE = re.compile(
    r"""\s*(?:
      (?P<ref>\$[FPV]\{[^{}]+\})
     |(?P<str>"(?:[^"\\]|\\.)*")
     |(?P<num>\d+\.\d*(?:[eE][+-]?\d+)?[dDfF]?|\.\d+[dDfF]?|\d+[lL]?)
     |(?P<name>[A-Za-z_][A-Za-z0-9_]*)
     |(?P<op>\|\||&&|==|!=|<=|>=|[-+*/%<>!?:(),.])
    )""",
    re.X,
)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'", "b": "\b", "f": "\f"}


def _unescape(raw: str) -> str:
    out, i = [], 0
    body = raw[1:-1]
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", body[i + 2:i + 6]):
                out.append(chr(int(body[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _tokenize(text: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    pos = 0
    while pos < len(text):
        if not text[pos:].strip():
            break
        match = _TOKEN_RE.match(text, pos)
        if not match or match.end() == pos:
            raise JExprError(f"Unsupported character near: {text[pos:pos + 20]!r}")
        pos = match.end()
        kind = match.lastgroup
        value = match.group(kind)
        if kind == "str":
            tokens.append(("str", _unescape(value)))
        elif kind == "num":
            if re.fullmatch(r"\d+[lL]?", value):
                tokens.append(("num", int(value.rstrip("lL"))))
            else:
                tokens.append(("num", float(value.rstrip("dDfF"))))
        else:
            tokens.append((kind, value))
    return tokens


# --------------------------------------------------------------------- parser
_STATIC_CLASSES = {"Double", "Integer", "Long", "String", "Math", "Boolean", "BigDecimal", "Float", "Short"}
_NEW_CLASSES = {"Date", "DecimalFormat", "SimpleDateFormat", "Double", "Integer", "Long", "String", "BigDecimal", "Float", "GregorianCalendar"}
_PACKAGE_PREFIXES = ("java", "lang", "util", "text", "math", "sql")


class _Parser:
    def __init__(self, tokens: list[tuple[str, Any]]):
        self.tokens = tokens
        self.pos = 0
        self.depth = 0

    def peek(self, offset: int = 0):
        index = self.pos + offset
        return self.tokens[index] if index < len(self.tokens) else (None, None)

    def take(self):
        token = self.peek()
        self.pos += 1
        return token

    def accept(self, kind: str, value: Any = None) -> bool:
        token = self.peek()
        if token[0] == kind and (value is None or token[1] == value):
            self.pos += 1
            return True
        return False

    def expect(self, kind: str, value: Any = None):
        if not self.accept(kind, value):
            raise JExprError(f"Expected {value or kind}, found {self.peek()[1]!r}")

    def parse(self):
        node = self.ternary()
        if self.pos != len(self.tokens):
            raise JExprError(f"Unexpected token: {self.peek()[1]!r}")
        return node

    def _enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise JExprError("The expression is nested too deeply")

    def ternary(self):
        self._enter()
        cond = self.or_()
        if self.accept("op", "?"):
            yes = self.ternary()
            self.expect("op", ":")
            no = self.ternary()
            self.depth -= 1
            return ("cond", cond, yes, no)
        self.depth -= 1
        return cond

    def or_(self):
        node = self.and_()
        while self.accept("op", "||"):
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.equality()
        while self.accept("op", "&&"):
            node = ("and", node, self.equality())
        return node

    def equality(self):
        node = self.relational()
        while self.peek()[0] == "op" and self.peek()[1] in ("==", "!="):
            op = self.take()[1]
            node = ("cmp", op, node, self.relational())
        return node

    def relational(self):
        node = self.additive()
        while self.peek()[0] == "op" and self.peek()[1] in ("<", ">", "<=", ">="):
            op = self.take()[1]
            node = ("cmp", op, node, self.additive())
        return node

    def additive(self):
        node = self.multiplicative()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.multiplicative())
        return node

    def multiplicative(self):
        node = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/", "%"):
            op = self.take()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        self._enter()
        try:
            if self.accept("op", "!"):
                return ("not", self.unary())
            if self.accept("op", "-"):
                return ("neg", self.unary())
            if self.accept("op", "+"):
                return self.unary()
            return self.postfix(self.primary())
        finally:
            self.depth -= 1

    def args(self) -> list:
        self.expect("op", "(")
        values = []
        if not self.accept("op", ")"):
            values.append(self.ternary())
            while self.accept("op", ","):
                values.append(self.ternary())
            self.expect("op", ")")
        return values

    def postfix(self, node):
        while self.accept("op", "."):
            kind, name = self.take()
            if kind != "name":
                raise JExprError("A method name is expected")
            if self.peek() != ("op", "("):
                raise JExprError(f"Field access .{name} is not supported")
            node = ("call", node, name, self.args())
        return node

    def primary(self):
        kind, value = self.take()
        if kind in ("num", "str"):
            return ("lit", value)
        if kind == "ref":
            return ("ref", value[1], value[3:-1].strip())
        if kind == "op" and value == "(":
            node = self.ternary()
            self.expect("op", ")")
            return node
        if kind == "name":
            if value == "true":
                return ("lit", True)
            if value == "false":
                return ("lit", False)
            if value == "null":
                return ("lit", None)
            if value == "new":
                parts = self._dotted()
                cls = parts[-1]
                if cls not in _NEW_CLASSES:
                    raise JExprError(f"new {cls} is not supported")
                return ("new", cls, self.args())
            parts = [value]
            while self.peek() == ("op", ".") and self.peek(1)[0] == "name" and self.peek(2) == ("op", "."):
                self.take()
                parts.append(self.take()[1])
            if self.peek() == ("op", ".") and self.peek(1)[0] == "name" and self.peek(2) == ("op", "("):
                self.take()
                method = self.take()[1]
                cls = parts[-1]
                if cls not in _STATIC_CLASSES:
                    raise JExprError(f"{'.'.join(parts)}.{method}() is not supported")
                return ("static", cls, method, self.args())
            raise JExprError(f"Unknown name: {value}")
        raise JExprError(f"Unexpected token: {value!r}")

    def _dotted(self) -> list[str]:
        parts = []
        kind, value = self.take()
        if kind != "name":
            raise JExprError("A class name is expected")
        parts.append(value)
        while self.peek() == ("op", ".") and self.peek(1)[0] == "name":
            self.take()
            parts.append(self.take()[1])
        return parts


@lru_cache(maxsize=2048)
def compile_expression(text: str):
    if len(text) > MAX_EXPRESSION_CHARS:
        raise JExprError("The expression is too long")
    return _Parser(_tokenize(text.strip())).parse()


def check_syntax(text: str) -> bool:
    try:
        compile_expression(text)
        return True
    except (JExprError, RecursionError):
        return False


# ---------------------------------------------------------- Java-ish helpers
def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def to_number(value: Any) -> int | float:
    if _is_num(value):
        return value
    if isinstance(value, str):
        try:
            text = value.strip().replace(",", "")
            return int(text) if re.fullmatch(r"[+-]?\d+", text) else float(text)
        except ValueError:
            pass
    if hasattr(value, "to_decimal"):
        return float(value.to_decimal())
    raise JExprError(f"Not a number: {value!r}")


def java_str(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # Java's Double.toString spelling; int(inf/nan) would raise in Python.
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if value == int(value) and abs(value) < 1e7:
            return f"{value:.1f}"
        return repr(value)
    if isinstance(value, (datetime, date)):
        return value.strftime("%a %b %d %H:%M:%S UTC %Y")
    return str(value)


def _cap(text: str) -> str:
    if len(text) > MAX_STRING:
        raise JExprError("The result is too large")
    return text


# --- DecimalFormat
class DecimalFormatter:
    def __init__(self, pattern: str):
        pattern = pattern.split(";")[0]
        self.prefix, self.core, self.suffix = self._split(pattern)
        self.percent = "%" in (self.prefix + self.suffix)
        integer, _, fraction = self.core.partition(".")
        self.min_int = integer.replace(",", "").count("0")
        self.group = len(integer) - integer.rfind(",") - 1 if "," in integer else 0
        self.min_frac = fraction.count("0")
        self.max_frac = len(fraction)

    @staticmethod
    def _split(pattern: str) -> tuple[str, str, str]:
        out, quoted, i = ["", "", ""], False, 0
        stage = 0
        while i < len(pattern):
            char = pattern[i]
            if char == "'":
                if pattern[i + 1:i + 2] == "'":
                    out[stage] += "'"
                    i += 2
                    continue
                quoted = not quoted
                i += 1
                continue
            if not quoted and char in "#0,." and stage < 2 and not (stage == 0 and char == "." and not re.match(r"\.[#0]", pattern[i:])):
                stage = 1
                out[1] += char
            else:
                if stage == 1:
                    stage = 2
                out[stage] += char
            i += 1
        return out[0], out[1], out[2]

    def format(self, value: Any) -> str:
        number = to_number(value)
        if self.percent:
            number = number * 100
        # Java DecimalFormat prints NaN bare and infinity as the ∞ symbol inside prefix/suffix.
        if isinstance(number, float) and math.isnan(number):
            return "NaN"
        negative = number < 0
        number = abs(number)
        if isinstance(number, float) and math.isinf(number):
            return ("-" if negative else "") + self.prefix + "∞" + self.suffix
        from decimal import ROUND_HALF_EVEN, Decimal
        quantum = Decimal(1).scaleb(-self.max_frac)
        dec = Decimal(repr(float(number)) if isinstance(number, float) else number).quantize(quantum, rounding=ROUND_HALF_EVEN)
        text = f"{dec:f}"
        whole, _, frac = text.partition(".")
        frac = frac.rstrip("0")
        frac = frac.ljust(self.min_frac, "0")
        whole = whole.lstrip("0")
        whole = whole.rjust(self.min_int, "0")
        if self.group and whole:
            groups = []
            while len(whole) > self.group:
                groups.insert(0, whole[-self.group:])
                whole = whole[:-self.group]
            groups.insert(0, whole)
            whole = ",".join(groups)
        body = whole + ("." + frac if frac else "")
        if body == "":
            body = "0"
        if negative and body.strip("0.,") != "":
            return "-" + self.prefix + body + self.suffix
        return self.prefix + body + self.suffix


# --- SimpleDateFormat
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _date_tokens(pattern: str) -> list[tuple[str, str]]:
    tokens, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if char == "'":
            j = i + 1
            literal = ""
            while j < len(pattern):
                if pattern[j] == "'":
                    if pattern[j + 1:j + 2] == "'":
                        literal += "'"
                        j += 2
                        continue
                    break
                literal += pattern[j]
                j += 1
            tokens.append(("lit", literal or "'"))
            i = j + 1
        elif char.isalpha():
            j = i
            while j < len(pattern) and pattern[j] == char:
                j += 1
            if char not in "yMdHhmsSaEzZk":
                raise JExprError(f"The date pattern letter '{char}' is not supported")
            tokens.append((char, pattern[i:j]))
            i = j
        else:
            tokens.append(("lit", char))
            i += 1
    return tokens


class DateFormatter:
    def __init__(self, pattern: str):
        self.tokens = _date_tokens(pattern)

    def format(self, value: Any) -> str:
        moment = to_datetime(value)
        out = []
        for kind, text in self.tokens:
            n = len(text)
            if kind == "lit":
                out.append(text)
            elif kind == "y":
                out.append(f"{moment.year % 100:02d}" if n == 2 else f"{moment.year:0{max(n, 1)}d}")
            elif kind == "M":
                out.append(_MONTHS[moment.month - 1] if n >= 4 else _MONTHS[moment.month - 1][:3] if n == 3 else f"{moment.month:0{n}d}")
            elif kind == "d":
                out.append(f"{moment.day:0{n}d}")
            elif kind == "H":
                out.append(f"{moment.hour:0{n}d}")
            elif kind == "k":
                out.append(f"{(moment.hour or 24):0{n}d}")
            elif kind == "h":
                out.append(f"{(moment.hour % 12 or 12):0{n}d}")
            elif kind == "m":
                out.append(f"{moment.minute:0{n}d}")
            elif kind == "s":
                out.append(f"{moment.second:0{n}d}")
            elif kind == "S":
                out.append(f"{moment.microsecond // 1000:03d}"[:n].ljust(n, "0") if n <= 3 else f"{moment.microsecond // 1000:0{n}d}")
            elif kind == "a":
                out.append("AM" if moment.hour < 12 else "PM")
            elif kind == "E":
                name = _DAYS[moment.weekday()]
                out.append(name if n >= 4 else name[:3])
            elif kind == "z":
                out.append("UTC")
            elif kind == "Z":
                out.append("+0000")
        return "".join(out)

    def parse(self, value: Any) -> datetime:
        if not isinstance(value, str):
            raise JExprError("parse() needs text")
        regex, order = "", []
        for kind, text in self.tokens:
            n = len(text)
            if kind == "lit":
                regex += re.escape(text)
            elif kind in "yMdHhmsSkaEzZ":
                if kind == "M" and n >= 3:
                    regex += "([A-Za-z]+)"
                elif kind == "y":
                    regex += r"(\d{4})" if n != 2 else r"(\d{2})"
                elif kind in "aEzZ":
                    regex += r"([A-Za-z+\-0-9:]+)"
                elif kind == "S":
                    regex += r"(\d{1,9})"
                else:
                    regex += r"(\d{1,2})"
                order.append((kind, n))
        match = re.fullmatch(regex, value.strip())
        if not match:
            raise JExprError(f"The date could not be parsed: {value[:40]!r}")
        parts = {"y": 1970, "M": 1, "d": 1, "H": 0, "m": 0, "s": 0, "S": 0}
        pm = None
        for (kind, n), raw in zip(order, match.groups()):
            if kind == "y":
                parts["y"] = int(raw) + (2000 if n == 2 else 0)
            elif kind == "M":
                parts["M"] = (int(raw) if n < 3 else [m[:3].lower() for m in _MONTHS].index(raw[:3].lower()) + 1)
            elif kind in "dHms":
                parts[kind] = int(raw)
            elif kind == "h":
                parts["H"] = int(raw) % 12
            elif kind == "k":
                parts["H"] = int(raw) % 24
            elif kind == "S":
                parts["S"] = int(raw[:3].ljust(3, "0"))
            elif kind == "a":
                pm = raw.upper() == "PM"
        if pm:
            parts["H"] += 12
        try:
            return datetime(parts["y"], parts["M"], parts["d"], parts["H"], parts["m"], parts["s"], parts["S"] * 1000)
        except ValueError as error:
            raise JExprError(str(error)) from error


def to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        text = value.strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            except ValueError:
                pass
    if _is_num(value):
        return datetime(1970, 1, 1) + timedelta(milliseconds=value)
    raise JExprError(f"Not a date: {value!r}")


# ------------------------------------------------------------------ evaluator
def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        raise JExprError("null condition")
    raise JExprError("A condition must be a boolean")


def _equal(left: Any, right: Any) -> bool:
    if _is_num(left) and _is_num(right):
        return left == right
    return left == right


def _arith(op: str, left: Any, right: Any) -> Any:
    if op == "+" and (isinstance(left, str) or isinstance(right, str)):
        return _cap(java_str(left) + java_str(right))
    a, b = to_number(left), to_number(right)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        raise JExprError("Division by zero")
    if op == "/":
        if isinstance(a, int) and isinstance(b, int):
            return int(a / b)
        return a / b
    return math.fmod(a, b) if isinstance(a, float) or isinstance(b, float) else int(math.fmod(a, b))


def _java_regex(pattern: Any) -> "re.Pattern[str]":
    """A Java regular expression (as in String.replaceAll) for Python; the common syntax is the same."""
    text = java_str(pattern)
    if len(text) > 500:
        raise JExprError("The regular expression is longer than 500 characters")
    try:
        return re.compile(text)
    except re.error as exc:
        raise JExprError(f"The regular expression could not be read: {exc}") from None


def _java_replacement(replacement: Any) -> Callable[["re.Match[str]"], str]:
    """Java replacement text: $1 or ${name} insert a group; a backslash makes the next character literal."""
    text = java_str(replacement)

    def build(match: "re.Match[str]") -> str:
        out, i = [], 0
        while i < len(text):
            char = text[i]
            if char == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
            elif char == "$" and i + 1 < len(text) and text[i + 1].isdigit():
                j = i + 1
                while j < len(text) and text[j].isdigit():
                    j += 1
                out.append(match.group(int(text[i + 1:j])) or "")
                i = j
            elif char == "$" and text[i + 1:i + 2] == "{" and "}" in text[i:]:
                end = text.index("}", i)
                out.append(match.group(text[i + 2:end]) or "")
                i = end + 1
            else:
                out.append(char)
                i += 1
        return "".join(out)
    return build


_STRING_METHODS: dict[str, Callable[..., Any]] = {
    "replaceAll": lambda s, a, b: _cap(_java_regex(a).sub(_java_replacement(b), s)),
    "replaceFirst": lambda s, a, b: _cap(_java_regex(a).sub(_java_replacement(b), s, count=1)),
    "matches": lambda s, a: _java_regex(a).fullmatch(s) is not None,
    "equals": lambda s, o: isinstance(o, str) and s == o,
    "equalsIgnoreCase": lambda s, o: isinstance(o, str) and s.lower() == o.lower(),
    "trim": lambda s: s.strip(),
    "isEmpty": lambda s: len(s) == 0,
    "length": lambda s: len(s),
    "toUpperCase": lambda s: s.upper(),
    "toLowerCase": lambda s: s.lower(),
    "contains": lambda s, o: java_str(o) in s,
    "startsWith": lambda s, o: s.startswith(java_str(o)),
    "endsWith": lambda s, o: s.endswith(java_str(o)),
    "indexOf": lambda s, o: s.find(java_str(o)),
    "lastIndexOf": lambda s, o: s.rfind(java_str(o)),
    "replace": lambda s, a, b: _cap(s.replace(java_str(a), java_str(b))),
    "concat": lambda s, o: _cap(s + java_str(o)),
    "toString": lambda s: s,
    "charAt": lambda s, i: s[i] if 0 <= i < len(s) else (_ for _ in ()).throw(JExprError("charAt range")),
}


class Calendar:
    """java.util.GregorianCalendar subset: built from fields, .getTime() gives the Date (a datetime)."""

    def __init__(self, moment: datetime):
        self.moment = moment


def _gregorian(args: list) -> Calendar:
    if not args:
        return Calendar(datetime.now(timezone.utc).replace(tzinfo=None))
    if len(args) not in (3, 5, 6):
        raise JExprError("GregorianCalendar(year, month, day[, hour, minute[, second]]) expected")
    numbers = [int(to_number(a)) for a in args] + [0] * (6 - len(args))
    year, month, day, hour, minute, second = numbers
    # Java is lenient (month 12 = next January, day 0 = last day of previous month): normalise the same way.
    year += month // 12
    month %= 12
    try:
        moment = datetime(year, month + 1, 1) + timedelta(days=day - 1, hours=hour, minutes=minute, seconds=second)
    except (ValueError, OverflowError) as error:
        raise JExprError(f"Invalid GregorianCalendar date: {error}") from None
    return Calendar(moment)


_EPOCH = datetime(1970, 1, 1)
# java.util.Date getters (deprecated in Java but common in Jasper reports): getYear() is year - 1900,
# getMonth() is 0-11, getDay() is 0 = Sunday.
_DATE_METHODS: dict[str, Callable[[datetime], Any]] = {
    "getYear": lambda d: d.year - 1900,
    "getMonth": lambda d: d.month - 1,
    "getDate": lambda d: d.day,
    "getDay": lambda d: (d.weekday() + 1) % 7,
    "getHours": lambda d: d.hour,
    "getMinutes": lambda d: d.minute,
    "getSeconds": lambda d: d.second,
    "getTime": lambda d: int((d - _EPOCH).total_seconds() * 1000),
}


def _java_pow(x: float, y: float) -> float:
    """Math.pow with Java results instead of Python exceptions: overflow -> ±Infinity, domain error -> NaN."""
    try:
        return math.pow(x, y)
    except OverflowError:
        odd_power_of_negative = x < 0 and float(y).is_integer() and int(y) % 2 == 1
        return -math.inf if odd_power_of_negative else math.inf
    except ValueError:
        return math.nan


def _substring(s: str, start: int, end: int | None = None) -> str:
    end = len(s) if end is None else end
    if not (isinstance(start, int) and isinstance(end, int)) or start < 0 or end > len(s) or start > end:
        raise JExprError("substring range galat")
    return s[start:end]


class Evaluator:
    def __init__(self, resolve: Callable[[str, str], Any]):
        self.resolve = resolve
        self.steps = 0

    def run(self, node) -> Any:
        self.steps += 1
        if self.steps > 20000:
            raise JExprError("The expression is too expensive to evaluate")
        kind = node[0]
        if kind == "lit":
            return node[1]
        if kind == "ref":
            return self.resolve(node[1], node[2])
        if kind == "cond":
            return self.run(node[2]) if _truthy(self.run(node[1])) else self.run(node[3])
        if kind == "or":
            return True if _truthy(self.run(node[1])) else _truthy(self.run(node[2]))
        if kind == "and":
            return _truthy(self.run(node[2])) if _truthy(self.run(node[1])) else False
        if kind == "not":
            return not _truthy(self.run(node[1]))
        if kind == "neg":
            return -to_number(self.run(node[1]))
        if kind == "cmp":
            op, left, right = node[1], self.run(node[2]), self.run(node[3])
            if op == "==":
                return _equal(left, right)
            if op == "!=":
                return not _equal(left, right)
            a, b = to_number(left), to_number(right)
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        if kind == "bin":
            return _arith(node[1], self.run(node[2]), self.run(node[3]))
        if kind == "new":
            return self.construct(node[1], [self.run(a) for a in node[2]])
        if kind == "static":
            return self.static(node[1], node[2], [self.run(a) for a in node[3]])
        if kind == "call":
            return self.call(self.run(node[1]), node[2], [self.run(a) for a in node[3]])
        raise JExprError(f"Unknown node {kind}")

    def construct(self, cls: str, args: list) -> Any:
        if cls == "Date":
            return datetime.now(timezone.utc).replace(tzinfo=None) if not args else to_datetime(args[0])
        if cls == "GregorianCalendar":
            return _gregorian(args)
        if cls == "DecimalFormat":
            return DecimalFormatter(str(args[0]))
        if cls == "SimpleDateFormat":
            return DateFormatter(str(args[0]))
        if cls in ("Double", "Float", "BigDecimal"):
            return float(to_number(args[0]))
        if cls in ("Integer", "Long"):
            return int(to_number(args[0]))
        if cls == "String":
            return java_str(args[0]) if args else ""
        raise JExprError(f"new {cls} is not supported")

    def static(self, cls: str, method: str, args: list) -> Any:
        if cls in ("Double", "Float", "BigDecimal") and method in ("parseDouble", "valueOf", "parseFloat"):
            return float(to_number(args[0]))
        if cls in ("Integer", "Long", "Short") and method in ("parseInt", "valueOf", "parseLong"):
            number = to_number(args[0])
            if isinstance(number, float) and number != int(number):
                raise JExprError("Integer parse: decimal value")
            return int(number)
        if cls == "String" and method == "valueOf":
            return java_str(args[0])
        if cls == "Boolean" and method in ("parseBoolean", "valueOf"):
            return str(args[0]).strip().lower() == "true"
        if cls == "Math":
            numbers = [to_number(a) for a in args]
            table = {
                "abs": lambda x: abs(x), "floor": lambda x: float(math.floor(x)), "ceil": lambda x: float(math.ceil(x)),
                "round": lambda x: int(math.floor(x + 0.5)), "sqrt": lambda x: math.sqrt(x) if x >= 0 else math.nan,
                "max": lambda x, y: max(x, y), "min": lambda x, y: min(x, y), "pow": _java_pow,
            }
            if method in table:
                return table[method](*numbers)
        raise JExprError(f"{cls}.{method}() is not supported")

    def call(self, target: Any, method: str, args: list) -> Any:
        if isinstance(target, DecimalFormatter) and method == "format":
            return target.format(args[0])
        if isinstance(target, DateFormatter) and method in ("format", "parse"):
            return getattr(target, method)(args[0])
        if target is None:
            raise JExprError(f".{method}() called on null")
        if isinstance(target, Calendar):
            if method == "getTime":
                return target.moment
            raise JExprError(f"GregorianCalendar.{method}() is not supported")
        if isinstance(target, (date, datetime)):
            moment = to_datetime(target)
            if method in _DATE_METHODS and not args:
                return _DATE_METHODS[method](moment)
            if method in ("before", "after") and len(args) == 1:
                other = to_datetime(args[0])
                return moment < other if method == "before" else moment > other
        if isinstance(target, str):
            if method == "substring":
                return _substring(target, *args)
            if method in _STRING_METHODS:
                return _STRING_METHODS[method](target, *args)
        if _is_num(target) and method in ("intValue", "longValue", "shortValue"):
            return int(target)
        if _is_num(target) and method in ("doubleValue", "floatValue"):
            return float(target)
        if method == "toString":
            return java_str(target)
        if method == "equals":
            return _equal(target, args[0])
        raise JExprError(f".{method}() is not supported")


def evaluate(text: str, resolve: Callable[[str, str], Any]) -> Any:
    """Compile (cached) and evaluate. Raises JExprError on any failure."""
    try:
        return Evaluator(resolve).run(compile_expression(text))
    except JExprError:
        raise
    except RecursionError as error:
        raise JExprError("The expression is nested too deeply") from error
    except (ArithmeticError, ValueError, TypeError, IndexError, KeyError) as error:
        # Row data must never crash the whole report: a Python runtime error becomes a blank cell,
        # like a Jasper expression exception would only affect that expression.
        raise JExprError(f"Expression runtime error: {type(error).__name__}") from error
