"""Loop 15: skills. Deterministic (no LLM, Docker for the sandbox test).
python tests/test_l10_skills.py
The LLM comparison is the finance eval: python tests/eval/finance/run.py 3 5 --skills
"""
import datetime as dt
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "eval" / "finance"))
sys.path.insert(0, str(ROOT / "skills" / "card-transactions" / "scripts"))
from agent import skills  # noqa: E402
from agent.loop import Context, run_tool  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from cases import build, match  # noqa: E402
from tx import load, load_all, signed  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l10-"))


def t_discover_and_tool():
    sk = skills.discover()
    assert "card-transactions" in sk, sk
    p = skills.prompt(sk)
    assert "card-transactions:" in p and "use_skill" in p and "scripts/tx.py" not in p  # body not in the prompt
    ctx = Context(skills=sk)
    out = run_tool(ctx, "use_skill", '{"name": "card-transactions"}')
    assert "/skills/card-transactions/scripts/tx.py" in out and "signed" in out, out[:300]
    assert run_tool(ctx, "use_skill", '{"name": "nope"}').startswith("ERROR"), "unknown skill must error"
    bad = TMP / "bad" / "x"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: other\ndescription: d\n---\nbody")
    try:
        skills.discover(bad.parent)
        raise AssertionError("name != folder must fail")
    except ValueError:
        pass
    print("PASS discover_and_tool")


def t_parser_matches_ground_truth():
    """Answers computed only from tx.load rows + the SKILL.md rules equal the eval's expected values."""
    fx = TMP / "fixtures"
    exp = {c["id"]: c["expected"] for c in build(fx)}
    a = load(fx / "card_a_2026-09.csv")
    b = load(fx / "card_b_2026-09.csv")
    bank = load(fx / "bank_2026-09.csv")
    c = load(fx / "card_c_2026-09.xlsx")
    cat = {}
    for r in a:
        cat[r["category"]] = cat.get(r["category"], 0) + r["amount"]
    out = [r for r in bank if r["direction"] == "out"]
    got = {
        "a_total": sum(r["amount"] for r in a),
        "a_by_cat": cat,
        "b_net": sum(signed(r) for r in b),
        "b_cancel_count": sum("취소" in r["status"] for r in b),
        "b_inst_bill": sum(r["amount"] // r["installments"] for r in b if r["installments"] > 1),
        "bank_out": sum(r["amount"] for r in out),
        "bank_in": sum(r["amount"] for r in bank if r["direction"] == "in"),
        "bank_no_card": sum(r["amount"] for r in out if "카드" not in r["merchant"]),
        "c_total": sum(r["amount"] for r in c),
        "c_usd": round(sum(r["foreign_amount"] or 0 for r in c), 2),
    }
    bad = {k: (v, exp[k]) for k, v in got.items() if not match(exp[k], v, 0.01 if k == "c_usd" else 1)}
    assert not bad, bad
    assert len(load_all([fx / "card_a_2026-08.csv", fx / "card_a_2026-09.csv"])) == 41 + 48
    print(f"PASS parser_matches_ground_truth ({len(got)} answers)")


def t_unseen_formats():
    """Formats the eval fixtures do not have: other header names, BOM + preamble + 총계 row, ₩ and negative refunds,
    two-digit years, tab separated; xlsx with real date cells (Excel serial numbers) and a title row."""
    d = TMP / "unseen"
    d.mkdir()
    (d / "card_d.csv").write_text(
        "﻿[카드D] 이용내역\n조회일: 2026.10.01\n\n승인일자\t이용 가맹점\t승인 금액\t승인구분\t할부개월\n"
        "26.09.02\t스타벅스 강남\t₩4,500\t정상\t00\n"
        "26.09.05\t쿠팡\t₩129,000\t정상\t03\n"
        "26.09.06\t쿠팡\t-29,000\t환불\t\n"
        "26.09.07\t스타벅스 강남\t4,500\t승인취소\t\n"
        "총계\t\t109,000\t\t\n", encoding="utf-8")
    rows, info = load(d / "card_d.csv", with_info=True)
    assert info[0]["header_row"] == 4 and info[0]["skipped"] == [(9, "total row")], info
    assert [r["amount"] for r in rows] == [4500, 129000, 29000, 4500], rows
    assert rows[0]["date"] == "2026-09-02" and rows[1]["installments"] == 3 and rows[0]["installments"] == 1
    assert rows[2]["cancel"] and rows[3]["cancel"] and not rows[1]["cancel"]  # -29,000 refund, 승인취소
    assert sum(signed(r) for r in rows) == 4500 + 129000 - 29000 - 4500, [signed(r) for r in rows]

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["2026년 9월 이용대금 명세서"])
    ws.append([])
    ws.append(["거래일", "사용처", "결제금액", "업종"])
    ws.append([dt.datetime(2026, 9, 3, 12, 30), "GS25", 3200, "편의점"])
    ws.append([dt.date(2026, 9, 4), "교보문고", 18000.0, "도서"])
    ws.append(["소계", None, 21200, None])
    wb.save(d / "card_e.xlsx")
    rows, info = load(d / "card_e.xlsx", with_info=True)
    assert [(r["date"], r["time"], r["merchant"], r["amount"], r["category"]) for r in rows] == [
        ("2026-09-03", "12:30", "GS25", 3200, "편의점"), ("2026-09-04", None, "교보문고", 18000, "도서")], rows
    assert info[0]["header_row"] == 3 and info[0]["skipped"] == [(6, "total row")], info
    print("PASS unseen_formats")


def t_sandbox_mount():
    """The skill runs inside the real sandbox image (no pandas/openpyxl) from the read-only mount."""
    ws = TMP / "ws"
    build(ws / "uploads")
    sb = Sandbox(ws, readonly=("uploads",), mounts={skills.DIR: skills.MOUNT})
    try:
        out = sb.exec("python /skills/card-transactions/scripts/tx.py uploads/*.csv uploads/*.xlsx")
        assert out.startswith("exit_code=0") and "encoding cp949" in out and "[sheet 해외]" in out, out
        out = sb.exec("touch /skills/x; echo hi > /skills/card-transactions/SKILL.md")
        assert "Read-only file system" in out, out
        assert "description:" in (skills.DIR / "card-transactions" / "SKILL.md").read_text()
    finally:
        sb.close()
    print("PASS sandbox_mount")


if __name__ == "__main__":
    t_discover_and_tool()
    t_parser_matches_ground_truth()
    t_unseen_formats()
    t_sandbox_mount()
