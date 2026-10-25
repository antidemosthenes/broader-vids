from dataclasses import dataclass
import json
import re

from .http import AccessPaused


@dataclass(frozen=True)
class Reference:
    name: str


TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[A-Za-z_$][A-Za-z0-9_$]*|[{}\[\]:,();=])')


class DataParser:
    def __init__(self, text):
        self.tokens = []
        position = 0
        while position < len(text):
            match = TOKEN.match(text, position)
            if not match and not text[position:].strip():
                break
            if not match or len(self.tokens) >= 100000:
                raise ValueError("Unsupported serialized-data token.")
            self.tokens.append(match[1])
            position = match.end()
        self.index = 0

    def take(self, expected=None):
        if self.index >= len(self.tokens):
            raise ValueError("Incomplete serialized data.")
        token = self.tokens[self.index]
        self.index += 1
        if expected is not None and token != expected:
            raise ValueError("Unexpected serialized-data token.")
        return token

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def value(self, depth=0):
        if depth > 100:
            raise ValueError("Serialized data is too deeply nested.")
        token = self.take()
        if token == "{":
            result = {}
            if self.peek() != "}":
                while True:
                    key = self.take()
                    if key.startswith('"'):
                        key = json.loads(key)
                    elif not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*|\d+", key):
                        raise ValueError("Unsupported data key.")
                    self.take(":")
                    result[key] = self.value(depth + 1)
                    if self.peek() != ",":
                        break
                    self.take(",")
            self.take("}")
            return result
        if token == "[":
            result = []
            if self.peek() != "]":
                while True:
                    result.append(self.value(depth + 1))
                    if self.peek() != ",":
                        break
                    self.take(",")
            self.take("]")
            return result
        if token == "void":
            self.take("0")
            return None
        if token == "undefined":
            return None
        if token.startswith('"') or token in {"null", "true", "false"} or re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", token):
            return json.loads(token)
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token):
            return Reference(token)
        raise ValueError("Only serialized data is supported.")


def pinia_state(body):
    match = re.search(r"window\.__pinia\s*=\s*(.*?)</script\s*>", body, re.S)
    if not match:
        return None
    try:
        parser = DataParser(match[1])
        parser.take("(")
        parser.take("function")
        parser.take("(")
        names = []
        while parser.peek() != ")":
            name = parser.take()
            if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name) or name in names:
                raise ValueError("Invalid data alias.")
            names.append(name)
            if parser.peek() != ",":
                break
            parser.take(",")
        parser.take(")")
        parser.take("{")
        parser.take("return")
        template = parser.value()
        if parser.peek() == ";":
            parser.take(";")
        parser.take("}")
        if parser.peek() == ")":
            parser.take(")")
        parser.take("(")
        arguments = []
        while parser.peek() != ")":
            arguments.append(parser.value())
            if parser.peek() != ",":
                break
            parser.take(",")
        parser.take(")")
        parser.take(")")
        if parser.peek() == ";":
            parser.take(";")
        if parser.peek() is not None or len(names) != len(arguments):
            raise ValueError("Incomplete data aliases or unexpected executable content.")
        aliases = dict(zip(names, arguments))
        def resolve(value, depth=0, allow_alias=True):
            if depth > 100:
                raise ValueError("Data alias nesting exceeded the limit.")
            if isinstance(value, Reference):
                if not allow_alias or value.name not in aliases:
                    raise ValueError("Unresolved data alias.")
                return resolve(aliases[value.name], depth + 1, False)
            if isinstance(value, dict):
                return {key: resolve(item, depth + 1, allow_alias) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item, depth + 1, allow_alias) for item in value]
            return value
        return resolve(template)
    except (ValueError, TypeError, RecursionError) as error:
        raise AccessPaused("Bilibili's serialized public state uses an unsupported data schema; no page scripts were executed.") from error
