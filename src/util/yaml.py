"""YAML loading with a bundled fallback parser.

Team environments install PyYAML; this module keeps the pipeline runnable
on machines without it by falling back to a minimal YAML-subset parser that
covers what configs/*.yaml uses: block mappings and sequences, flow
[..] and {..}, double-quoted scalars, plain scalars, `>-` / `|` block
scalars, comments, and null/int/float/bool typing.

Usage:
    from src.util.yaml import load
    doc = load(path)          # or load_text(text)
"""
from pathlib import Path

try:
    import yaml as _pyyaml          # PyYAML when available
except ImportError:                 # pragma: no cover
    _pyyaml = None


class YamlError(Exception):
    pass


# ---------------------------------------------------------------------------
# Minimal YAML-subset parser (fallback when PyYAML is not installed)
# ---------------------------------------------------------------------------
def _scalar(v: str):
    v = v.strip()
    if not v or v == "~" or v in ("null", "Null", "NULL"):
        return None
    if v.startswith('"'):
        if not v.endswith('"') or len(v) < 2:
            raise YamlError(f"unterminated string: {v!r}")
        return v[1:-1]
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _strip_comment(v: str) -> str:
    if v.startswith('"'):
        end = v.find('"', 1)
        if end == -1:
            return v
        tail = v[end + 1:]
        i = tail.find("#")
        return v if i == -1 else v[: end + 1 + i]
    i = v.find(" #")
    return v[:i] if i != -1 else v


def _flow(v: str):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    if v.startswith("{") and v.endswith("}"):
        inner = v[1:-1].strip()
        out = {}
        for part in inner.split(","):
            k, val = part.split(":", 1)
            out[k.strip()] = _scalar(val)
        return out
    return _scalar(v)


class _Parser:
    def __init__(self, text: str):
        self.lines = []
        for n, raw in enumerate(text.splitlines(), 1):
            s = raw.rstrip()
            if not s.strip() or s.lstrip().startswith("#"):
                continue
            self.lines.append((n, len(s) - len(s.lstrip(" ")), s.strip()))
        self.i = 0

    def parse(self):
        return self._block(-1)

    def _block(self, parent):
        n, indent, content = self.lines[self.i]
        if content.startswith("- "):
            return self._sequence(parent)
        return self._mapping(parent)

    def _sequence(self, parent):
        out = []
        while self.i < len(self.lines):
            n, indent, content = self.lines[self.i]
            if indent <= parent:
                break
            if not content.startswith("- "):
                raise YamlError(f"line {n}: expected '- ' item")
            rest = content[2:].strip()
            self.i += 1
            if rest == "":
                if self.i < len(self.lines) and self.lines[self.i][1] > indent:
                    out.append(self._block(indent))
                else:
                    out.append(None)
            elif (": " in rest or rest.endswith(":")) and not rest.startswith('"'):
                key, vrest = rest.split(":", 1)
                item = {}
                vrest = vrest.strip()
                if vrest.startswith("#"):
                    vrest = ""
                if vrest in ("|", ">", ">-", ">+", "|-", "|+"):
                    item[key.strip()] = self._block_scalar(indent, vrest)
                elif vrest == "":
                    if self.i < len(self.lines) and self.lines[self.i][1] > indent:
                        item[key.strip()] = self._block(indent)
                    else:
                        item[key.strip()] = None
                else:
                    item[key.strip()] = _flow(_strip_comment(vrest))
                while self.i < len(self.lines) and self.lines[self.i][1] > indent:
                    if self.lines[self.i][2].startswith("- "):
                        break
                    n2, ind2, c2 = self.lines[self.i]
                    if ":" not in c2:
                        raise YamlError(f"line {n2}: expected key: value in item")
                    k2, r2 = c2.split(":", 1)
                    r2 = r2.strip()
                    if r2.startswith("#"):
                        r2 = ""
                    if r2 in ("|", ">", ">-", ">+", "|-", "|+"):
                        item[k2.strip()] = self._block_scalar(indent, r2)
                    elif r2 == "":
                        self.i += 1
                        if self.i < len(self.lines) and self.lines[self.i][1] > ind2:
                            item[k2.strip()] = self._block(ind2)
                        else:
                            item[k2.strip()] = None
                    else:
                        item[k2.strip()] = _flow(_strip_comment(r2))
                        self.i += 1
                out.append(item)
            else:
                out.append(_flow(_strip_comment(rest)))
        return out

    def _mapping(self, parent):
        out = {}
        while self.i < len(self.lines):
            n, indent, content = self.lines[self.i]
            if indent <= parent:
                break
            if ":" not in content:
                raise YamlError(f"line {n}: expected key: value")
            key, rest = content.split(":", 1)
            rest = rest.strip()
            if rest.startswith("#"):
                rest = ""
            if rest in ("|", ">", ">-", ">+", "|-", "|+"):
                out[key.strip()] = self._block_scalar(parent, rest)
            elif rest == "":
                self.i += 1
                if self.i < len(self.lines) and self.lines[self.i][1] > indent:
                    out[key.strip()] = self._block(indent)
                else:
                    out[key.strip()] = None
            else:
                out[key.strip()] = _flow(_strip_comment(rest))
                self.i += 1
        return out

    def _block_scalar(self, parent, style):
        self.i += 1
        chunks = []
        while self.i < len(self.lines) and self.lines[self.i][1] > parent:
            chunks.append(self.lines[self.i][2])
            self.i += 1
        return " ".join(chunks) if style.startswith(">") else "\n".join(chunks)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_text(text: str):
    if _pyyaml is not None:
        return _pyyaml.safe_load(text)
    return _Parser(text).parse()


def load(path):
    return load_text(Path(path).read_text(encoding="utf-8"))
