"""STDF V4 reader (Loop 32): streams the records a yield/parametric analysis needs and flattens them into tables.
Runs inside the sandbox (python + pyarrow). Written from the STDF V4 spec for the records below; others are skipped.

parts:   lot, wafer, head, site, part (index in the file), part_id, x, y, hard_bin, soft_bin, passed, part_flg, test_t
         passed is NULL when PART_FLG says the flag is not valid; part_flg bits 0/1 = a retest of an earlier part
results: part, test_num, result, failed, test_flg (one row per PTR; result NULL when not valid/executed/aborted)
tests:   test_num, name, lo, hi, units           (from the first PTR of a test that carries them)

Records may end early (optional fields left out, as testers do after the first PTR of a test); a record cut short
by a truncated file stops the read with a note, everything before it is kept.
"""
import struct

FAR, MIR, WIR, WRR, PIR, PRR, PTR = (0, 10), (1, 10), (2, 10), (2, 20), (5, 10), (5, 20), (15, 10)


class Reader:
    """Byte fields of one record; reading past its end gives None (an optional field left out)."""
    __slots__ = ("b", "i", "e")

    def __init__(self, b, endian):
        self.b, self.i, self.e = b, 0, endian

    def _take(self, fmt, n):
        if self.i + n > len(self.b):
            self.i = len(self.b)
            return None
        v = struct.unpack_from(self.e + fmt, self.b, self.i)[0]
        self.i += n
        return v

    def u1(self): return self._take("B", 1)
    def u2(self): return self._take("H", 2)
    def u4(self): return self._take("I", 4)
    def i1(self): return self._take("b", 1)
    def i2(self): return self._take("h", 2)
    def r4(self): return self._take("f", 4)

    def cn(self):
        n = self.u1()
        if n is None:
            return None
        s = bytes(self.b[self.i:self.i + n]).decode("latin-1")
        self.i += n
        return s


def records(f, chunk=8 << 20, progress=None):
    """(rec_typ, rec_sub, body, endian) for every record of an open binary file. progress(bytes_read)."""
    buf, pos, endian, done = bytearray(), 0, "<", 0
    # (a file of several concatenated STDFs switches order at each FAR)
    while True:
        data = f.read(chunk)
        if data:
            buf = buf[pos:] + data
            pos = 0
            done += len(data)
            if progress:
                progress(done)
        while pos + 4 <= len(buf):
            typ, sub = buf[pos + 2], buf[pos + 3]
            if (typ, sub) == FAR and pos + 5 <= len(buf):  # the byte order of everything after it
                endian = ">" if buf[pos + 4] == 1 else "<"  # CPU_TYPE 1 = big-endian (Sun), 2 = little (x86)
            n = struct.unpack_from(endian + "H", buf, pos)[0]
            if pos + 4 + n > len(buf):
                break
            yield typ, sub, memoryview(buf)[pos + 4:pos + 4 + n], endian
            pos += 4 + n
        if not data:
            if pos < len(buf):
                yield None, None, len(buf) - pos, endian  # a truncated last record
            return


def flatten(path, sink, progress=None):
    """Read one STDF file and hand table rows to sink(table, columns dict of lists) in batches."""
    lot, wafer = None, None
    open_parts = {}  # (head, site) -> [part index, [test rows]]
    parts = {k: [] for k in ("lot", "wafer", "head", "site", "part", "part_id", "x", "y", "hard_bin", "soft_bin",
                             "passed", "part_flg", "test_t")}
    res = {k: [] for k in ("part", "test_num", "result", "failed", "test_flg")}
    tests, n_part, notes = {}, 0, []

    def flush(final=False):
        if parts["part"] and (final or len(res["part"]) >= 2_000_000):
            sink("parts", parts)
            sink("results", res)
            for v in (*parts.values(), *res.values()):
                v.clear()

    with open(path, "rb") as f:
        for typ, sub, body, e in records(f, progress=progress):
            if typ is None:
                notes.append(f"{path}: the last record is cut short ({body} bytes), ignored")
                break
            if (typ, sub) == PTR:
                r = Reader(body, e)
                tn, head, site, tflg = r.u4(), r.u1(), r.u1(), r.u1()
                r.u1()  # PARM_FLG
                val = r.r4()
                tflg = tflg or 0
                if tflg & 0x32:  # bit 1 RESULT not valid, 4 test not executed, 5 aborted: no value to count
                    val = None
                op = open_parts.get((head, site))
                if op is not None and tn is not None:
                    op[1].append((tn, val, bool(tflg & 0x80), tflg))
                if tn is not None and tn not in tests:
                    name = r.cn()
                    r.cn()  # ALARM_ID
                    opt = r.u1()
                    r.i1(); r.i1(); r.i1()  # RES/LLM/HLM scale
                    lo, hi = r.r4(), r.r4()
                    units = r.cn()
                    if name is not None:  # the first PTR of a test that names it
                        no_lo = opt is not None and opt & 0x50  # bit 4 LO_LIMIT invalid, 6 no low limit
                        no_hi = opt is not None and opt & 0xA0  # bit 5 HI_LIMIT invalid, 7 no high limit
                        tests[tn] = (name, None if no_lo else lo, None if no_hi else hi, units or "")
            elif (typ, sub) == PIR:
                r = Reader(body, e)
                open_parts[(r.u1(), r.u1())] = [n_part, []]
                n_part += 1
            elif (typ, sub) == PRR:
                r = Reader(body, e)
                head, site, flg = r.u1(), r.u1(), r.u1()
                r.u2()  # NUM_TEST
                hb, sb, x, y, tt, pid = r.u2(), r.u2(), r.i2(), r.i2(), r.u4(), r.cn()
                op = open_parts.pop((head, site), None)
                if op is None:
                    continue
                idx, rows = op
                flg = flg or 0
                passed = None if flg & 0x10 else not flg & 0x08  # bit 4: the pass/fail flag is not valid
                for k, v in zip(parts, (lot, wafer, head, site, idx, pid, x, y, hb, sb, passed, flg, tt)):
                    parts[k].append(v)
                for tn, val, failed, tflg in rows:
                    res["part"].append(idx)
                    res["test_num"].append(tn)
                    res["result"].append(val)
                    res["failed"].append(failed)
                    res["test_flg"].append(tflg)
                flush()
            elif (typ, sub) == WIR:
                r = Reader(body, e)
                r.u1(); r.u1(); r.u4()  # HEAD_NUM, SITE_GRP, START_T
                wafer = r.cn()
            elif (typ, sub) == WRR:
                wafer = None
            elif (typ, sub) == MIR:
                r = Reader(body, e)
                r.u4(); r.u4(); r.u1()  # SETUP_T, START_T, STAT_NUM
                for _ in range(3):
                    r._take("c", 1)  # MODE_COD, RTST_COD, PROT_COD
                r.u2(); r._take("c", 1)  # BURN_TIM, CMOD_COD
                lot = r.cn()
    flush(final=True)
    if open_parts:
        notes.append(f"{path}: {len(open_parts)} parts without a PRR (cut short?), ignored")
    return {"parts": n_part, "tests": tests, "notes": notes}


# ---- writer (the benchmark's generator uses it; testers write these, we only need to read them) ----
def _cn(s):
    b = s.encode("latin-1")[:255]
    return bytes([len(b)]) + b


def rec(typ, sub, body, e="<"):
    return struct.pack(e + "HBB", len(body), typ, sub) + body


def far(e="<"):
    return rec(0, 10, bytes([1 if e == ">" else 2, 4]), e)


def mir(lot, part_typ="DEMO", e="<"):
    body = struct.pack(e + "IIB", 0, 0, 1) + b"PNN" + struct.pack(e + "H", 0) + b" " + _cn(lot) + _cn(part_typ) \
        + _cn("NODE1") + _cn("ATE") + _cn("JOB")
    return rec(1, 10, body, e)


def wir(wafer, e="<"):
    return rec(2, 10, struct.pack(e + "BBI", 1, 255, 0) + _cn(wafer), e)


def wrr(wafer, n, good, e="<"):
    return rec(2, 20, struct.pack(e + "BBIIIIII", 1, 255, 0, n, 0, 0, good, 0) + _cn(wafer), e)


def pir(head, site, e="<"):
    return rec(5, 10, struct.pack(e + "BB", head, site), e)


def prr(head, site, passed, n_test, hb, sb, x, y, part_id, e="<"):
    return rec(5, 20, struct.pack(e + "BBBHHHhhI", head, site, 0 if passed else 0x08, n_test, hb, sb, x, y, 0)
               + _cn(part_id), e)


def ptr(test_num, head, site, value, failed, first=None, e="<"):
    """first=(name, lo, hi, units) for the first PTR of a test; later ones end after RESULT (optional fields)."""
    body = struct.pack(e + "IBBBBf", test_num, head, site, 0x80 if failed else 0, 0, value)
    if first:
        name, lo, hi, units = first
        body += _cn(name) + _cn("") + struct.pack(e + "Bbbbff", 0x0E, 0, 0, 0, lo, hi) + _cn(units)
    return rec(15, 10, body, e)


def mrr(e="<"):
    return rec(1, 20, struct.pack(e + "I", 0), e)
