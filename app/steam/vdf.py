"""Valve KeyValues (VDF/ACF) parser for libraryfolders.vdf and appmanifest_*.acf.

Drafted by the local agent, reviewed and fixed (the key token was read twice,
so nothing parsed). Pure function: text in, dict out, None when malformed.
"""

from typing import Optional


class _VdfParseError(Exception):
    """Internal parser error."""


class _VdfParser:
    __slots__ = ("text", "index", "length")

    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0
        self.length = len(text)

    def _skip_whitespace_and_comments(self) -> None:
        text = self.text
        length = self.length
        index = self.index

        while index < length:
            char = text[index]

            if char.isspace():
                index += 1
            elif char == "/" and index + 1 < length and text[index + 1] == "/":
                while index < length and text[index] != "\n":
                    index += 1
            else:
                break

        self.index = index

    def _next_token(self) -> Optional[tuple]:
        self._skip_whitespace_and_comments()

        if self.index >= self.length:
            return None

        char = self.text[self.index]

        if char == "{":
            self.index += 1
            return ("open", "{")

        if char == "}":
            self.index += 1
            return ("close", "}")

        if char == '"':
            return ("value", self._read_quoted())

        start = self.index
        text = self.text
        length = self.length

        while self.index < length:
            char = text[self.index]

            if char.isspace() or char == '"' or char == "{" or char == "}":
                break

            if char == "/" and self.index + 1 < length and text[self.index + 1] == "/":
                break

            self.index += 1

        if self.index == start:
            raise _VdfParseError("unexpected character")

        return ("value", text[start:self.index])

    def _read_quoted(self) -> str:
        text = self.text
        length = self.length
        index = self.index + 1
        parts = []

        while index < length:
            char = text[index]

            if char == '"':
                self.index = index + 1
                return "".join(parts)

            if char == "\\":
                if index + 1 >= length:
                    raise _VdfParseError("unterminated quoted string")

                escaped = text[index + 1]

                if escaped == "\\":
                    parts.append("\\")
                elif escaped == '"':
                    parts.append('"')
                elif escaped == "n":
                    parts.append("\n")
                elif escaped == "t":
                    parts.append("\t")
                else:
                    parts.append("\\" + escaped)

                index += 2
                continue

            parts.append(char)
            index += 1

        raise _VdfParseError("unterminated quoted string")

    def _parse_entry(self, result: dict, key_token: tuple) -> None:
        """Parse the value for an already-read key token."""
        key_kind, key = key_token

        if key_kind == "open":
            raise _VdfParseError("stray open brace")

        if key_kind == "close":
            raise _VdfParseError("stray close brace")

        value_token = self._next_token()

        if value_token is None:
            raise _VdfParseError("key has no value")

        value_kind, value = value_token

        if value_kind == "open":
            child = {}
            self._parse_dict(child)
            result[key] = child
        elif value_kind == "close":
            raise _VdfParseError("stray close brace")
        else:
            result[key] = value

    def _parse_dict(self, result: dict) -> None:
        while True:
            token = self._next_token()

            if token is None:
                raise _VdfParseError("missing close brace")

            kind, value = token

            if kind == "close":
                return

            if kind == "open":
                raise _VdfParseError("stray open brace")

            self._parse_entry(result, token)

    def parse(self) -> dict:
        result = {}

        while True:
            token = self._next_token()

            if token is None:
                return result

            kind, value = token

            if kind == "close":
                raise _VdfParseError("stray close brace")

            if kind == "open":
                raise _VdfParseError("stray open brace")

            self._parse_entry(result, token)


def parse_vdf(text: str) -> Optional[dict]:
    if not isinstance(text, str):
        return None

    if text.startswith("\ufeff"):
        text = text[1:]

    try:
        parser = _VdfParser(text)
        return parser.parse()
    except Exception:
        return None
