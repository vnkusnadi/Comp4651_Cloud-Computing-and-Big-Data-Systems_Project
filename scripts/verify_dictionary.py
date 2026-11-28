"""Verify the AAPL/MSFT company dictionary in information_technology.yaml
against the original 10-K HTML files in data_ingestion_and_processing/staging/.

Checks per concept entry:
  * row_label appears in the filing (exact, case-insensitive, whitespace-normalised;
    falls back to the prefix before a '$' or '(' for long footnote-style labels)
  * the XBRL tag appears as an iXBRL `name="...Tag"` attribute
  * the value appears in some display form (full units / thousands / millions,
    with or without comma grouping)
  * dimension members (segment rows) appear as explicitMember values
Plus re-derives the accounting identities from the YAML values.

Usage (from the repository root):
    python scripts/verify_dictionary.py

No third-party dependencies (has a built-in minimal YAML-subset parser).
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
YAML_PATH = REPO / "configs" / "sectors" / "information_technology.yaml"
STAGING = REPO / "data_ingestion_and_processing" / "staging"


# --------------------------------------------------------------------------
# Minimal YAML-subset parser (block maps/seqs, flow [], {}, "quoted", >- blocks)
# --------------------------------------------------------------------------
class YamlError(Exception):
    pass


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


class Parser:
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
            elif ":" in rest and not rest.startswith('"'):
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


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def label_present(html: str, label: str) -> bool:
    h = norm(html.replace("&#8217;", "\u2019").replace("&#8212;", "\u2014")
               .replace("&nbsp;", " "))
    l = norm(label)
    if l in h:
        return True
    # long footnote-style labels: match the prefix before '$' or '('
    for cut in ("$", "("):
        if cut in label:
            p = norm(label.split(cut)[0])
            if len(p) > 12 and p in h:
                return True
    return False


def tag_present(html: str, tag: str) -> bool:
    bare = tag.split(":")[-1]
    return re.search(r'name="[^"]*[:]' + re.escape(bare) + r'"', html) is not None


def value_present(html: str, value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    candidates = {str(value), f"{value:,}"}
    for div in (1, 1000, 1_000_000, 1_000_000_000):
        q = value / div
        if q == int(q):
            candidates |= {f"{q:,.0f}", str(int(q))}
    return any(c in html for c in candidates)


def member_present(html: str, member: str) -> bool:
    return member in html


def main() -> None:
    doc = Parser(YAML_PATH.read_text(encoding="utf-8")).parse()
    companies = doc.get("companies") or {}
    failures = []

    for ticker, entry in companies.items():
        docname = entry["filing"]["primary_document"]
        html_path = STAGING / f"{ticker}_{docname}"
        html = html_path.read_text(encoding="utf-8", errors="replace")
        print(f"\n===== {ticker}  ({html_path.name}, {len(html):,} bytes) =====")
        concepts = entry["concepts"]
        for name, c in concepts.items():
            problems = []
            if name == "Revenue_segment":
                for side in ("product", "service"):
                    s = c[side]
                    if not label_present(html, s["row_label"]):
                        problems.append(f"label missing: {s['row_label']!r}")
                    if not value_present(html, s["value"]):
                        problems.append(f"value missing: {s['value']}")
                    if not member_present(html, s["dimension_member"]):
                        problems.append(f"dimension member missing: {s['dimension_member']}")
                status = "OK" if not problems else "FAIL"
                print(f"  Revenue_segment {status}: "
                      f"product={c['product']['row_label']} ({c['product']['value']:,}) "
                      f"service={c['service']['row_label']} ({c['service']['value']:,})")
                for p in problems:
                    print(f"    - {p}")
            else:
                label = c.get("row_label")
                tag = c.get("xbrl_tag")
                val = c.get("value")
                if c.get("status") == "missing":
                    print(f"  {name:<45s} SKIP (marked missing in dictionary)   "
                          f"tag={tag}")
                    continue
                if label and not label_present(html, label):
                    problems.append(f"label missing: {label!r}")
                if tag and not tag_present(html, tag):
                    problems.append(f"tag missing: {tag}")
                if val is not None and not value_present(html, val):
                    problems.append(f"value missing: {val}")
                status = "OK" if not problems else "FAIL"
                print(f"  {name:<45s} {status}   "
                      f"label={label!r:<38s} tag={tag}  value={val}")
                for p in problems:
                    print(f"    - {p}")
            if problems:
                failures.append((ticker, name, problems))

        # re-derive identities from the YAML values
        def val_of(name):
            return concepts[name]["value"]
        print("  -- identities re-derived from YAML values --")
        gp = val_of("GrossProfit")
        rev = val_of("Revenue_total")
        cor = val_of("CostOfRevenue")
        print(f"  gross_profit: {gp:,} == {rev:,} - {cor:,} "
              f"-> {abs(gp - (rev - cor)):,} diff")
        opi = val_of("OperatingIncomeLoss")
        opex = val_of("OperatingExpenses")
        print(f"  op_income:    {opi:,} == {gp:,} - {opex:,} "
              f"-> {abs(opi - (gp - opex)):,} diff")
        eps = val_of("EarningsPerShareDiluted")
        ni = val_of("NetIncomeLoss")
        sh = val_of("WeightedAverageNumberOfDilutedSharesOutstanding")
        if sh:
            print(f"  eps:          {eps} ~= {ni:,} / {sh:,} = {ni / sh:,.2f} "
                  f"(gap {abs(eps - ni / sh) / eps * 100:.2f}%)")
        da = concepts.get("DepreciationAndAmortization", {}).get("value")
        if da:
            print(f"  ebitda:       {opi:,} + {da:,} = {opi + da:,}")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAIL: {len(failures)} concept(s) not found in the original filings")
        for t, n, p in failures:
            print(f"  {t}.{n}: {p}")
        sys.exit(1)
    print("ALL DICTIONARY ENTRIES VERIFIED against the original 10-K files")


if __name__ == "__main__":
    main()
