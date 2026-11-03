//! STDF V4 -> Parquet (Loop 34). The same records, rules and tables as sandbox/vtools/stdf.py + ingest.py, so the
//! two give equal tables (tests/test_l27_rust.py checks it); this one is for GB files.
//!
//!   stdf2parquet OUT_DIR FILE...
//! writes OUT_DIR/{parts,results,tests}.parquet, prints `PROGRESS done total file` lines and, last, one JSON line
//! {"parts": n, "results": n, "tests": n, "notes": [...]}.
use std::collections::{BTreeMap, HashMap};
use std::fs::File;
use std::io::{BufWriter, Read, Write};
use std::sync::Arc;

use arrow::array::{
    ArrayRef, BooleanBuilder, Float32Builder, Int16Builder, Int64Builder, StringBuilder, UInt16Builder, UInt32Builder,
    UInt8Builder,
};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use parquet::arrow::ArrowWriter;
use parquet::basic::{Compression, ZstdLevel};
use parquet::file::properties::WriterProperties;

const FLUSH_AT: usize = 2_000_000; // results rows per batch, as the Python reader

/// Fields of one record; reading past its end gives None (an optional field left out).
struct Rd<'a> {
    b: &'a [u8],
    i: usize,
    be: bool,
}

impl<'a> Rd<'a> {
    fn take<const N: usize>(&mut self) -> Option<[u8; N]> {
        if self.i + N > self.b.len() {
            self.i = self.b.len();
            return None;
        }
        let mut a = [0u8; N];
        a.copy_from_slice(&self.b[self.i..self.i + N]);
        self.i += N;
        Some(a)
    }
    fn u1(&mut self) -> Option<u8> {
        self.take::<1>().map(|a| a[0])
    }
    fn i1(&mut self) -> Option<i8> {
        self.take::<1>().map(|a| a[0] as i8)
    }
    fn u2(&mut self) -> Option<u16> {
        let be = self.be;
        self.take::<2>().map(|a| if be { u16::from_be_bytes(a) } else { u16::from_le_bytes(a) })
    }
    fn i2(&mut self) -> Option<i16> {
        let be = self.be;
        self.take::<2>().map(|a| if be { i16::from_be_bytes(a) } else { i16::from_le_bytes(a) })
    }
    fn u4(&mut self) -> Option<u32> {
        let be = self.be;
        self.take::<4>().map(|a| if be { u32::from_be_bytes(a) } else { u32::from_le_bytes(a) })
    }
    fn r4(&mut self) -> Option<f32> {
        let be = self.be;
        self.take::<4>().map(|a| if be { f32::from_be_bytes(a) } else { f32::from_le_bytes(a) })
    }
    fn cn(&mut self) -> Option<String> {
        let n = self.u1()? as usize;
        let end = (self.i + n).min(self.b.len());
        let s: String = self.b[self.i..end].iter().map(|&c| c as char).collect(); // latin-1
        self.i = end;
        Some(s)
    }
}

struct Parts {
    file: StringBuilder,
    lot: StringBuilder,
    wafer: StringBuilder,
    head: UInt8Builder,
    site: UInt8Builder,
    part: Int64Builder,
    part_id: StringBuilder,
    x: Int16Builder,
    y: Int16Builder,
    hard_bin: UInt16Builder,
    soft_bin: UInt16Builder,
    passed: BooleanBuilder,
    part_flg: UInt8Builder,
    test_t: UInt32Builder,
    n: usize,
}

struct Results {
    file: StringBuilder,
    part: Int64Builder,
    test_num: UInt32Builder,
    result: Float32Builder,
    failed: BooleanBuilder,
    test_flg: UInt8Builder,
    n: usize,
}

fn parts_schema() -> Arc<Schema> {
    let s = |n: &str| Field::new(n, DataType::Utf8, true);
    Arc::new(Schema::new(vec![
        s("file"), s("lot"), s("wafer"),
        Field::new("head", DataType::UInt8, true), Field::new("site", DataType::UInt8, true),
        Field::new("part", DataType::Int64, true), s("part_id"),
        Field::new("x", DataType::Int16, true), Field::new("y", DataType::Int16, true),
        Field::new("hard_bin", DataType::UInt16, true), Field::new("soft_bin", DataType::UInt16, true),
        Field::new("passed", DataType::Boolean, true), Field::new("part_flg", DataType::UInt8, true),
        Field::new("test_t", DataType::UInt32, true),
    ]))
}

fn results_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("file", DataType::Utf8, true), Field::new("part", DataType::Int64, true),
        Field::new("test_num", DataType::UInt32, true), Field::new("result", DataType::Float32, true),
        Field::new("failed", DataType::Boolean, true), Field::new("test_flg", DataType::UInt8, true),
    ]))
}

impl Parts {
    fn new() -> Self {
        Parts {
            file: StringBuilder::new(), lot: StringBuilder::new(), wafer: StringBuilder::new(),
            head: UInt8Builder::new(), site: UInt8Builder::new(), part: Int64Builder::new(),
            part_id: StringBuilder::new(), x: Int16Builder::new(), y: Int16Builder::new(),
            hard_bin: UInt16Builder::new(), soft_bin: UInt16Builder::new(), passed: BooleanBuilder::new(),
            part_flg: UInt8Builder::new(), test_t: UInt32Builder::new(), n: 0,
        }
    }
    fn batch(&mut self) -> RecordBatch {
        self.n = 0;
        let cols: Vec<ArrayRef> = vec![
            Arc::new(self.file.finish()), Arc::new(self.lot.finish()), Arc::new(self.wafer.finish()),
            Arc::new(self.head.finish()), Arc::new(self.site.finish()), Arc::new(self.part.finish()),
            Arc::new(self.part_id.finish()), Arc::new(self.x.finish()), Arc::new(self.y.finish()),
            Arc::new(self.hard_bin.finish()), Arc::new(self.soft_bin.finish()), Arc::new(self.passed.finish()),
            Arc::new(self.part_flg.finish()), Arc::new(self.test_t.finish()),
        ];
        RecordBatch::try_new(parts_schema(), cols).unwrap()
    }
}

impl Results {
    fn new() -> Self {
        Results {
            file: StringBuilder::new(), part: Int64Builder::new(), test_num: UInt32Builder::new(),
            result: Float32Builder::new(), failed: BooleanBuilder::new(), test_flg: UInt8Builder::new(), n: 0,
        }
    }
    fn batch(&mut self) -> RecordBatch {
        self.n = 0;
        let cols: Vec<ArrayRef> = vec![
            Arc::new(self.file.finish()), Arc::new(self.part.finish()), Arc::new(self.test_num.finish()),
            Arc::new(self.result.finish()), Arc::new(self.failed.finish()), Arc::new(self.test_flg.finish()),
        ];
        RecordBatch::try_new(results_schema(), cols).unwrap()
    }
}

type Test = (String, Option<f32>, Option<f32>, String);
struct Open {
    idx: i64,
    rows: Vec<(u32, Option<f32>, bool, u8)>,
}

struct Out {
    parts: Parts,
    res: Results,
    pw: ArrowWriter<File>,
    rw: ArrowWriter<File>,
    n_parts: u64,
    n_results: u64,
}

impl Out {
    fn flush(&mut self, force: bool) {
        if self.parts.n > 0 && (force || self.res.n >= FLUSH_AT) {
            self.pw.write(&self.parts.batch()).unwrap();
            self.rw.write(&self.res.batch()).unwrap();
        }
    }
}

fn flatten(path_os: &std::ffi::OsStr, out: &mut Out, tests: &mut BTreeMap<u32, Test>, notes: &mut Vec<String>, base: u64, total: u64) {
    let name = std::path::Path::new(path_os).file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let path = path_os.to_string_lossy().into_owned(); // for notes only; the file is opened by its real name
    let mut f = File::open(path_os).expect("open");
    let (mut lot, mut wafer): (Option<String>, Option<String>) = (None, None);
    let mut open: HashMap<(Option<u8>, Option<u8>), Open> = HashMap::new();
    let mut n_part: i64 = 0;
    let mut buf: Vec<u8> = Vec::new();
    let mut chunk = vec![0u8; 8 << 20];
    let (mut pos, mut done, mut be) = (0usize, 0u64, false);
    let stdout = std::io::stdout();
    loop {
        let got = f.read(&mut chunk).expect("read");
        if got > 0 {
            buf.drain(..pos);
            pos = 0;
            buf.extend_from_slice(&chunk[..got]);
            done += got as u64;
            let _ = writeln!(stdout.lock(), "PROGRESS {} {} {}", base + done, total, name);
        }
        while pos + 4 <= buf.len() {
            let (typ, sub) = (buf[pos + 2], buf[pos + 3]);
            if typ == 0 && sub == 10 && pos + 5 <= buf.len() {
                be = buf[pos + 4] == 1; // CPU_TYPE 1 = big-endian (Sun), 2 = little (x86)
            }
            let n = if be { u16::from_be_bytes([buf[pos], buf[pos + 1]]) } else { u16::from_le_bytes([buf[pos], buf[pos + 1]]) } as usize;
            if pos + 4 + n > buf.len() {
                break;
            }
            let body = &buf[pos + 4..pos + 4 + n];
            let mut r = Rd { b: body, i: 0, be };
            match (typ, sub) {
                (15, 10) => {
                    let (tn, head, site, tflg) = (r.u4(), r.u1(), r.u1(), r.u1());
                    r.u1(); // PARM_FLG
                    let mut val = r.r4();
                    let tflg = tflg.unwrap_or(0);
                    if tflg & 0x32 != 0 {
                        val = None; // RESULT not valid / not executed / aborted
                    }
                    if let (Some(o), Some(t)) = (open.get_mut(&(head, site)), tn) {
                        o.rows.push((t, val, tflg & 0x80 != 0, tflg));
                    }
                    if let Some(t) = tn {
                        if !tests.contains_key(&t) {
                            let tname = r.cn();
                            r.cn(); // ALARM_ID
                            let opt = r.u1();
                            r.i1();
                            r.i1();
                            r.i1();
                            let (lo, hi, units) = (r.r4(), r.r4(), r.cn());
                            if let Some(tname) = tname {
                                let no_lo = opt.map_or(false, |o| o & 0x50 != 0);
                                let no_hi = opt.map_or(false, |o| o & 0xA0 != 0);
                                tests.insert(t, (tname, if no_lo { None } else { lo }, if no_hi { None } else { hi }, units.unwrap_or_default()));
                            }
                        }
                    }
                }
                (5, 10) => {
                    let k = (r.u1(), r.u1());
                    open.insert(k, Open { idx: n_part, rows: Vec::new() });
                    n_part += 1;
                }
                (5, 20) => {
                    let (head, site, flg) = (r.u1(), r.u1(), r.u1());
                    r.u2(); // NUM_TEST
                    let (hb, sb, x, y, tt, pid) = (r.u2(), r.u2(), r.i2(), r.i2(), r.u4(), r.cn());
                    if let Some(o) = open.remove(&(head, site)) {
                        let flg = flg.unwrap_or(0);
                        let p = &mut out.parts;
                        p.file.append_value(&name);
                        p.lot.append_option(lot.as_deref());
                        p.wafer.append_option(wafer.as_deref());
                        p.head.append_option(head);
                        p.site.append_option(site);
                        p.part.append_value(o.idx);
                        p.part_id.append_option(pid.as_deref());
                        p.x.append_option(x);
                        p.y.append_option(y);
                        p.hard_bin.append_option(hb);
                        p.soft_bin.append_option(sb);
                        p.passed.append_option(if flg & 0x10 != 0 { None } else { Some(flg & 0x08 == 0) });
                        p.part_flg.append_value(flg);
                        p.test_t.append_option(tt);
                        p.n += 1;
                        out.n_parts += 1;
                        let rs = &mut out.res;
                        for (t, v, failed, tf) in o.rows {
                            rs.file.append_value(&name);
                            rs.part.append_value(o.idx);
                            rs.test_num.append_value(t);
                            rs.result.append_option(v);
                            rs.failed.append_value(failed);
                            rs.test_flg.append_value(tf);
                            rs.n += 1;
                            out.n_results += 1;
                        }
                        out.flush(false);
                    }
                }
                (2, 10) => {
                    r.u1();
                    r.u1();
                    r.u4();
                    wafer = r.cn();
                }
                (2, 20) => wafer = None,
                (1, 10) => {
                    r.u4();
                    r.u4();
                    r.u1();
                    r.u1();
                    r.u1();
                    r.u1(); // MODE_COD, RTST_COD, PROT_COD
                    r.u2();
                    r.u1(); // BURN_TIM, CMOD_COD
                    lot = r.cn();
                }
                _ => {}
            }
            pos += 4 + n;
        }
        if got == 0 {
            if pos < buf.len() {
                notes.push(format!("{}: the last record is cut short ({} bytes), ignored", path, buf.len() - pos));
            }
            break;
        }
    }
    if !open.is_empty() {
        notes.push(format!("{}: {} parts without a PRR (cut short?), ignored", path, open.len()));
    }
}

fn main() {
    // args_os: a file name that is not UTF-8 (a CP949 zip) must not panic; names are shown lossily
    let args: Vec<std::ffi::OsString> = std::env::args_os().collect();
    if args.len() < 3 {
        eprintln!("usage: stdf2parquet OUT_DIR FILE...");
        std::process::exit(2);
    }
    let dir = std::path::Path::new(&args[1]);
    let props = || WriterProperties::builder().set_compression(Compression::ZSTD(ZstdLevel::default())).build();
    let pw = ArrowWriter::try_new(File::create(dir.join("parts.parquet")).unwrap(), parts_schema(), Some(props())).unwrap();
    let rw = ArrowWriter::try_new(File::create(dir.join("results.parquet")).unwrap(), results_schema(), Some(props())).unwrap();
    let mut out = Out { parts: Parts::new(), res: Results::new(), pw, rw, n_parts: 0, n_results: 0 };
    let total: u64 = args[2..].iter().map(|p| std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)).sum();
    let (mut tests, mut notes, mut base) = (BTreeMap::new(), Vec::new(), 0u64);
    for p in &args[2..] {
        flatten(p.as_os_str(), &mut out, &mut tests, &mut notes, base, total);
        base += std::fs::metadata(p).map(|m| m.len()).unwrap_or(0);
    }
    out.flush(true);
    out.pw.close().unwrap();
    out.rw.close().unwrap();
    // tests: test_num, name, lo, hi, units
    let schema = Arc::new(Schema::new(vec![
        Field::new("test_num", DataType::UInt32, true), Field::new("name", DataType::Utf8, true),
        Field::new("lo", DataType::Float32, true), Field::new("hi", DataType::Float32, true),
        Field::new("units", DataType::Utf8, true),
    ]));
    let (mut tn, mut nm, mut lo, mut hi, mut un) =
        (UInt32Builder::new(), StringBuilder::new(), Float32Builder::new(), Float32Builder::new(), StringBuilder::new());
    for (k, (n, l, h, u)) in &tests {
        tn.append_value(*k);
        nm.append_value(n);
        lo.append_option(*l);
        hi.append_option(*h);
        un.append_value(u);
    }
    let cols: Vec<ArrayRef> = vec![Arc::new(tn.finish()), Arc::new(nm.finish()), Arc::new(lo.finish()), Arc::new(hi.finish()), Arc::new(un.finish())];
    let mut tw = ArrowWriter::try_new(File::create(dir.join("tests.parquet")).unwrap(), schema.clone(), Some(props())).unwrap();
    tw.write(&RecordBatch::try_new(schema, cols).unwrap()).unwrap();
    tw.close().unwrap();
    // JSON string escape: \, " and every control character (a file name may hold a tab or a newline)
    let esc = |s: &str| {
        let mut o = String::with_capacity(s.len());
        for c in s.chars() {
            match c {
                '\\' => o.push_str("\\\\"),
                '"' => o.push_str("\\\""),
                c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
                c => o.push(c),
            }
        }
        o
    };
    let mut w = BufWriter::new(std::io::stdout());
    writeln!(w, "{{\"parts\": {}, \"results\": {}, \"tests\": {}, \"notes\": [{}]}}", out.n_parts, out.n_results, tests.len(),
             notes.iter().map(|n| format!("\"{}\"", esc(n))).collect::<Vec<_>>().join(", ")).unwrap();
}
