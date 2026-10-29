"""Load Korean card / bank export files (CSV in UTF-8 or cp949, XLSX) into one list of plain dicts.
Standard library only (the sandbox has no pandas/openpyxl).

CLI:   python tx.py FILE [FILE ...] [--json OUT.json]   -> prints what was detected in each file/sheet
Python:
    import sys; sys.path.insert(0, "/skills/card-transactions/scripts")
    from tx import load, signed
    rows = load("/workspace/uploads/card_b_2026-09.csv")      # or load_all([...])

Each row: file, sheet, row (1-based line/row in the file), date "YYYY-MM-DD", time "HH:MM" or None,
merchant (가맹점 or 적요), amount (KRW, positive), direction "out" | "in", status (raw text, "" if none),
installments (int, 1 = 일시불), category (or None), foreign_amount / currency (e.g. USD, or None).
"""
import csv
import datetime as dt
import io
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

# header text -> field. First match wins per column; matched on the header with spaces removed.
COLUMNS = [
    ("date", r"(이용|거래|승인|사용|매출)?(일자|일시|일|날짜)$|^date"),
    ("merchant", r"가맹점|적요|거래내용|내용|사용처|이용처|merchant|description"),
    ("foreign", r"현지|해외금액|외화|usd|달러"),
    ("krw_conv", r"원화환산|환산금액|원화금액"),
    ("out", r"출금|찾으신|지급"),
    ("in", r"입금|맡기신"),
    ("amount", r"(이용|승인|결제|청구|거래)?금액|amount"),
    ("status", r"상태|승인구분|구분|취소여부"),
    ("installments", r"할부"),
    ("category", r"카테고리|업종|분류|category"),
    ("balance", r"잔액|balance"),
]
TOTAL_RE = re.compile(r"^\s*(합\s*계|총\s*계|소\s*계|total)", re.I)


def _field(header):
    h = re.sub(r"\s+", "", str(header or "")).lower()
    if not h:
        return None
    for name, pat in COLUMNS:
        if re.search(pat, h):
            return name
    return None


def number(x):
    """'12,300원' -> 12300, '(1,000)' -> -1000, '' -> None. Floats stay floats."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return x
    s = re.sub(r"[,\s원₩$]|KRW|USD", "", str(x), flags=re.I)
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s in ("", "-"):
        return None
    v = float(s)
    v = -v if neg else v
    return int(v) if v.is_integer() and "." not in s else v


def date_time(x):
    """-> ("YYYY-MM-DD", "HH:MM" | None) or None. Accepts 2026-09-03, 2026.09.03 14:22, 2026/09/03, 20260903,
    26.09.03, and Excel serial day numbers."""
    if isinstance(x, (int, float)) and 20000 < x < 80000:
        d = dt.datetime(1899, 12, 30) + dt.timedelta(days=float(x))
        return d.date().isoformat(), (d.strftime("%H:%M") if d.time() != dt.time() else None)
    s = str(x or "").strip()
    m = re.match(r"^(\d{2,4})[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})일?(?:[ T]+(\d{1,2}):(\d{2}))?", s)
    if not m:
        return None
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    y += 2000 if y < 100 else 0
    try:
        day = dt.date(y, mo, d).isoformat()
    except ValueError:
        return None
    return day, (f"{int(m[4]):02d}:{m[5]}" if m[4] else None)


def installments(x):
    s = str(x or "").strip()
    if not s or "일시" in s:
        return 1
    m = re.search(r"\d+", s)
    return int(m[0]) if m and int(m[0]) > 0 else 1


def signed(r):
    """Spending with cancellations netted: rows whose status contains 취소 (취소, 부분취소, 승인취소) count negative.
    A 부분취소 row's amount is the cancelled part, so summing signed() over all rows gives the net."""
    return -r["amount"] if "취소" in (r["status"] or "") else r["amount"]


# ---- reading tables ---------------------------------------------------------------------------------------------
def _decode(raw):
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace"), "utf-8 (with replacement characters)"


def _csv_tables(path):
    """Delimiter = the one whose parse has the best header row (csv.Sniffer is fooled by preamble lines and by
    commas inside amounts)."""
    text, enc = _decode(Path(path).read_bytes())
    best, best_score = None, -1
    for delim in (",", "\t", ";", "|"):
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        try:
            _, fields = _header_row(rows)
        except ValueError:
            continue
        score = sum(f is not None for f in fields)
        if score > best_score:
            best, best_score = rows, score
    return [(None, best if best is not None else list(csv.reader(io.StringIO(text))), enc)]


def _xlsx_tables(path):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = []
    for sh in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns):
        target = rels[sh.get(rel_ns)].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        rows = []
        for r in ET.fromstring(z.read(target)).iter(f"{{{ns['m']}}}row"):
            cells = {}
            for c in r.findall("m:c", ns):
                ref, t = c.get("r"), c.get("t")
                col = 0
                for ch in re.match(r"[A-Z]+", ref)[0]:
                    col = col * 26 + ord(ch) - 64
                v = c.find("m:v", ns)
                if t == "s":
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
                elif v is None:
                    val = ""
                elif t in ("str", "e"):
                    val = v.text or ""
                else:
                    val = number(v.text)
                cells[col - 1] = val
            idx = int(r.get("r")) - 1
            while len(rows) < idx:
                rows.append([])
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
        out.append((sh.get("name"), rows, "xlsx"))
    return out


def _header_row(rows):
    """First of the first 30 rows where >= 2 cells look like known column names and one is a date column."""
    for i, row in enumerate(rows[:30]):
        fields = [_field(c) for c in row]
        if "date" in fields and sum(f is not None for f in fields) >= 2:
            return i, fields
    raise ValueError("no header row found (expected a date column plus amount/merchant columns in the first 30 rows)")


def _parse(path, sheet, rows, enc):
    hi, fields = _header_row(rows)
    col = {}
    for i, f in enumerate(fields):
        if f and f not in col:
            col[f] = i
    if "krw_conv" in col:  # foreign sheet: KRW conversion is the amount, local currency is the foreign amount
        col["amount"] = col["krw_conv"]
    cur = None
    if "foreign" in col:
        m = re.search(r"[A-Z]{3}", str(rows[hi][col["foreign"]]).upper())
        cur = m[0] if m else "USD"
    get = lambda row, f: row[col[f]] if f in col and col[f] < len(row) else None  # noqa: E731
    out, skipped = [], []
    for n, row in enumerate(rows[hi + 1:], hi + 2):
        if not any(str(c).strip() for c in row):
            continue
        if any(TOTAL_RE.match(str(c)) for c in row[:3]):
            skipped.append((n, "total row"))
            continue
        dtm = date_time(get(row, "date"))
        if not dtm:
            skipped.append((n, f"no date: {row[:4]}"))
            continue
        out_amt, in_amt = number(get(row, "out")), number(get(row, "in"))
        if "out" in col or "in" in col:
            amount, direction = (out_amt, "out") if out_amt else (in_amt or 0, "in")
        else:
            amount, direction = number(get(row, "amount")), "out"
            if amount is None:
                skipped.append((n, f"no amount: {row[:4]}"))
                continue
            if amount < 0:  # some exports write refunds as negative amounts
                amount, direction = -amount, "in"
        out.append({"file": Path(path).name, "sheet": sheet, "row": n, "date": dtm[0], "time": dtm[1],
                    "merchant": str(get(row, "merchant") or "").strip(), "amount": amount, "direction": direction,
                    "status": str(get(row, "status") or "").strip(),
                    "installments": installments(get(row, "installments")),
                    "category": (str(get(row, "category")).strip() or None) if "category" in col else None,
                    "foreign_amount": number(get(row, "foreign")) if "foreign" in col else None,
                    "currency": cur})
    info = {"file": Path(path).name, "sheet": sheet, "encoding": enc, "header_row": hi + 1,
            "columns": {f: str(rows[hi][i]) for f, i in col.items() if f != "krw_conv" or "amount" not in col},
            "rows": len(out), "skipped": skipped}
    return out, info


def load(path, with_info=False):
    """All sheets of one file. with_info=True also returns what was detected (encoding, header, skipped rows)."""
    path = str(path)
    tables = _xlsx_tables(path) if path.lower().endswith((".xlsx", ".xlsm")) else _csv_tables(path)
    rows, infos = [], []
    for sheet, table, enc in tables:
        try:
            r, info = _parse(path, sheet, table, enc)
        except ValueError as e:
            infos.append({"file": Path(path).name, "sheet": sheet, "error": str(e)})
            continue
        rows += r
        infos.append(info)
    return (rows, infos) if with_info else rows


def load_all(paths):
    return [r for p in paths for r in load(p)]


def describe(rows, info):
    """Short human summary of one table, so the agent can check the parse before trusting it."""
    if "error" in info:
        return f"{info['file']} [{info['sheet']}]: ERROR {info['error']}"
    mine = [r for r in rows if r["file"] == info["file"] and r["sheet"] == info["sheet"]]
    lines = [f"{info['file']}" + (f" [sheet {info['sheet']}]" if info["sheet"] else "") +
             f": {info['rows']} rows, encoding {info['encoding']}, header on line {info['header_row']}",
             "  columns: " + ", ".join(f"{k}={v!r}" for k, v in info["columns"].items())]
    if mine:
        dates = sorted(r["date"] for r in mine)
        lines.append(f"  dates {dates[0]} .. {dates[-1]}")
        for d in ("out", "in"):
            s = [r for r in mine if r["direction"] == d]
            if s:
                lines.append(f"  {d}: {len(s)} rows, sum of amount {sum(r['amount'] for r in s):,}")
        st = sorted({r["status"] for r in mine if r["status"]})
        if st:
            lines.append(f"  status values: {st}  (signed() nets rows containing 취소)")
        inst = sorted({r["installments"] for r in mine if r["installments"] > 1})
        if inst:
            lines.append(f"  installment months present: {inst}")
        if any(r["foreign_amount"] is not None for r in mine):
            lines.append(f"  foreign amounts in {mine[0]['currency']}: sum {sum(r['foreign_amount'] or 0 for r in mine):,.2f}")
        if info["columns"].get("category"):
            lines.append(f"  categories: {sorted({r['category'] for r in mine if r['category']})}")
        lines.append(f"  first row: {json.dumps({k: mine[0][k] for k in ('date', 'merchant', 'amount', 'status')}, ensure_ascii=False)}")
    for n, why in info["skipped"]:
        lines.append(f"  skipped line {n}: {why}")
    return "\n".join(lines)


def main(argv):
    out = None
    if "--json" in argv:
        i = argv.index("--json")
        out, argv = argv[i + 1], argv[:i] + argv[i + 2:]
    rows = []
    for p in argv:
        r, infos = load(p, with_info=True)
        rows += r
        for info in infos:
            print(describe(r, info))
    if out:
        Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
