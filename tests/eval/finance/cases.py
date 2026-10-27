"""eval v0 for docs/PROBLEM.md: synthetic card/bank exports + questions with exact answers.

Expected answers come from the generated records, never from parsing the rendered files, so a parsing bug in
the generator cannot hide the same bug in the agent. Each file imitates one pain point of real exports.
  card_a_2026-08.csv, card_a_2026-09.csv  clean UTF-8, has categories (baseline + month-over-month)
  card_b_2026-09.csv   cp949, "12,300원", "2026.09.03 14:22", 취소/부분취소 rows, 할부
  bank_2026-09.csv     preamble lines, "합계" row, comma amounts, card payments (double counting trap)
  card_c_2026-09.xlsx  two sheets (국내/해외), USD + KRW conversion
  budget.json          per-category budget
"""
import csv
import datetime as dt
import io
import json
import random
from collections import defaultdict
from pathlib import Path

SEED = 7
MERCHANTS = {
    "식비": ["스타벅스", "김밥천국", "배달의민족", "맥도날드", "이마트24"],
    "교통": ["카카오T", "티머니", "GS칼텍스"],
    "쇼핑": ["쿠팡", "무신사", "다이소"],
    "생활": ["올리브영", "온누리약국"],
    "문화": ["CGV", "교보문고"],
}
RANGE = {"식비": (4_000, 32_000), "교통": (1_300, 70_000), "쇼핑": (5_000, 120_000), "생활": (3_000, 45_000),
         "문화": (9_000, 38_000)}
SUBS = [("넷플릭스", 13_500, 5), ("유튜브프리미엄", 14_900, 12), ("멜론", 10_900, 20)]
BUDGET = {"식비": 250_000, "교통": 400_000, "쇼핑": 500_000, "생활": 200_000, "문화": 100_000, "구독": 40_000}
BIG = ["하이마트", "애플스토어", "이케아"]  # installment purchases
CONTRACT = ("\n\n(채점용: 최종 답을 outputs/answer.json 에 {\"answer\": ...} 형태로도 저장해 주세요. "
            "금액은 원 단위 정수로. 형식: %s)")


def _days(year, month):
    d = dt.date(year, month, 1)
    out = []
    while d.month == month:
        out.append(d)
        d += dt.timedelta(days=1)
    return out


def _card_a(rng, year, month, n):
    rows = []
    for _ in range(n):
        cat = rng.choice(list(MERCHANTS))
        lo, hi = RANGE[cat]
        rows.append({"date": rng.choice(_days(year, month)), "merchant": rng.choice(MERCHANTS[cat]),
                     "amount": rng.randrange(lo, hi, 100), "category": cat})
    for m, amt, day in SUBS:
        rows.append({"date": dt.date(year, month, day), "merchant": m, "amount": amt, "category": "구독"})
    return sorted(rows, key=lambda r: (r["date"], r["merchant"]))


def _card_b(rng):
    rows = []
    for i in range(30):
        cat = rng.choice(list(MERCHANTS))
        lo, hi = RANGE[cat]
        months = 1
        amt = rng.randrange(lo, hi, 100)
        if i in (3, 11, 19):  # big purchases on installments
            months, amt = (3, 6, 3)[(3, 11, 19).index(i)], rng.randrange(300_000, 1_200_000, 1_000) + 700
        t = dt.datetime.combine(rng.choice(_days(2026, 9)[:26]), dt.time(rng.randrange(8, 23), rng.randrange(60)))
        merchant = BIG[(3, 11, 19).index(i)] if months > 1 else rng.choice(MERCHANTS[cat])
        rows.append({"time": t, "merchant": merchant, "amount": amt, "status": "승인",
                     "months": months})
    singles = [r for r in rows if r["months"] == 1]
    for r in rng.sample(singles, 2):
        rows.append({**r, "time": r["time"] + dt.timedelta(days=rng.randrange(1, 4), hours=1), "status": "취소"})
    r = rng.choice([s for s in singles if s["amount"] > 20_000 and s["status"] == "승인"])
    rows.append({**r, "time": r["time"] + dt.timedelta(days=2), "status": "부분취소",
                 "amount": r["amount"] * 3 // 10 // 100 * 100})
    return sorted(rows, key=lambda r: r["time"])


def _bank(rng, card_a_aug_total):
    out = [(dt.date(2026, 9, 1), "카드A 결제", card_a_aug_total, 0), (dt.date(2026, 9, 14), "카드B 결제", 412_300, 0),
           (dt.date(2026, 9, 25), "급여", 0, 3_200_000), (dt.date(2026, 9, 26), "월세 이체", 650_000, 0),
           (dt.date(2026, 9, 26), "아파트관리비", 187_420, 0), (dt.date(2026, 9, 21), "SKT 통신요금", 59_400, 0),
           (dt.date(2026, 9, 9), "김민수 이체", 50_000, 0), (dt.date(2026, 9, 17), "ATM 출금", 100_000, 0),
           (dt.date(2026, 9, 3), "이자", 0, 1_284), (dt.date(2026, 9, 28), "중고거래 입금", 0, 35_000),
           (dt.date(2026, 9, 10), "KT 인터넷", 33_000, 0)]
    return sorted(out)


def build(out_dir):
    """Writes the fixture files, returns the cases."""
    rng = random.Random(SEED)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    aug, sep = _card_a(rng, 2026, 8, 38), _card_a(rng, 2026, 9, 45)
    for name, rows in (("card_a_2026-08.csv", aug), ("card_a_2026-09.csv", sep)):
        with open(out / name, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["이용일자", "가맹점명", "이용금액", "카테고리"])
            for r in rows:
                w.writerow([r["date"].isoformat(), r["merchant"], r["amount"], r["category"]])

    b = _card_b(rng)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["거래일시", "가맹점", "금액", "상태", "할부"])
    for r in b:
        w.writerow([r["time"].strftime("%Y.%m.%d %H:%M"), r["merchant"], f"{r['amount']:,}원", r["status"],
                    "일시불" if r["months"] == 1 else f"{r['months']}개월"])
    (out / "card_b_2026-09.csv").write_bytes(buf.getvalue().encode("cp949"))

    aug_total = sum(r["amount"] for r in aug)
    bank = _bank(rng, aug_total)
    bal, lines = 2_418_000, []
    for d, memo, wd, dep in bank:
        bal += dep - wd
        lines.append([d.strftime("%Y-%m-%d"), memo, f"{wd:,}" if wd else "", f"{dep:,}" if dep else "", f"{bal:,}"])
    buf = io.StringIO()
    buf.write("계좌번호: 110-***-482913\n조회기간: 2026-09-01 ~ 2026-09-30\n예금주: 김**\n")
    w = csv.writer(buf)
    w.writerow(["거래일자", "적요", "출금액", "입금액", "잔액"])
    w.writerows(lines)
    w.writerow(["합계", "", f"{sum(x[2] for x in bank):,}", f"{sum(x[3] for x in bank):,}", ""])
    (out / "bank_2026-09.csv").write_text(buf.getvalue(), encoding="utf-8-sig")

    from openpyxl import Workbook
    wb = Workbook()
    dom = wb.active
    dom.title = "국내"
    dom.append(["이용일", "가맹점", "금액"])
    c_dom = [(rng.choice(_days(2026, 9)), rng.choice(MERCHANTS[c]), rng.randrange(*RANGE[c], 100))
             for c in rng.choices(list(MERCHANTS), k=15)]
    for d, m, a in c_dom:
        dom.append([d.strftime("%Y/%m/%d"), m, a])
    ov = wb.create_sheet("해외")
    ov.append(["이용일", "가맹점", "현지금액(USD)", "원화환산"])
    c_ov = []
    for m in ("AMAZON.COM", "APPLE.COM/BILL", "UBER TRIP", "STEAM PURCHASE", "AIRBNB", "OPENAI"):
        usd = rng.randrange(299, 25_000) / 100
        c_ov.append((rng.choice(_days(2026, 9)), m, usd, round(usd * 1385.5)))
    for d, m, usd, krw in c_ov:
        ov.append([d.strftime("%Y/%m/%d"), m, usd, krw])
    wb.save(out / "card_c_2026-09.xlsx")
    (out / "budget.json").write_text(json.dumps(BUDGET, ensure_ascii=False, indent=1))

    # ---- ground truth from records
    def by(rows, key):
        d = defaultdict(int)
        for r in rows:
            d[key(r)] += r["amount"]
        return dict(d)

    a_total = sum(r["amount"] for r in sep)
    a_cat, a_cat_aug = by(sep, lambda r: r["category"]), by(aug, lambda r: r["category"])
    a_merch = sorted(by(sep, lambda r: r["merchant"]).items(), key=lambda kv: -kv[1])
    assert a_merch[2][1] != a_merch[3][1], "tie in top3"
    a_day = sorted(by(sep, lambda r: r["date"]).items(), key=lambda kv: -kv[1])
    assert a_day[0][1] != a_day[1][1]
    inc = sorted(((c, a_cat.get(c, 0) - a_cat_aug.get(c, 0)) for c in a_cat), key=lambda kv: -kv[1])
    assert inc[0][1] != inc[1][1]
    pairs = {(r["merchant"], r["amount"]) for r in aug} & {(r["merchant"], r["amount"]) for r in sep}
    assert {m for m, _ in pairs} == {m for m, _, _ in SUBS}, pairs

    sign = {"승인": 1, "취소": -1, "부분취소": -1}
    b_net = sum(sign[r["status"]] * r["amount"] for r in b)
    b_inst = [r for r in b if r["months"] > 1]
    b_inst_bill = sum(r["amount"] // r["months"] for r in b_inst)
    b_single_net = sum(sign[r["status"]] * r["amount"] for r in b if r["months"] == 1)
    b_merch = defaultdict(int)
    for r in b:
        b_merch[r["merchant"]] += sign[r["status"]] * r["amount"]
    b_top = sorted(b_merch.items(), key=lambda kv: -kv[1])
    assert b_top[0][1] != b_top[1][1]

    bank_out, bank_in = sum(x[2] for x in bank), sum(x[3] for x in bank)
    bank_no_card = sum(x[2] for x in bank if "카드" not in x[1])
    housing = sum(x[2] for x in bank if "월세" in x[1] or "관리비" in x[1])
    c_usd = round(sum(x[2] for x in c_ov), 2)
    c_ov_krw, c_dom_krw = sum(x[3] for x in c_ov), sum(x[2] for x in c_dom)

    A, B, BANK, C = ["card_a_2026-09.csv"], ["card_b_2026-09.csv"], ["bank_2026-09.csv"], ["card_c_2026-09.xlsx"]
    AA = ["card_a_2026-08.csv", "card_a_2026-09.csv"]
    N, D = "정수", '{"카테고리": 금액} 객체'
    cases = [
        ("a_total", A, "9월에 카드A로 총 얼마 썼어?", N, a_total, "clean"),
        ("a_by_cat", A, "9월 카드A 지출을 카테고리별로 합계 내줘.", D, a_cat, "clean"),
        ("a_top3", A, "9월 카드A에서 돈을 가장 많이 쓴 가맹점 3곳을 금액 큰 순서로 알려줘.",
         '가맹점 이름 3개의 배열(큰 순서)', [m for m, _ in a_merch[:3]], "clean"),
        ("a_daily_avg", A, "9월 카드A 하루 평균 지출은? (9월 전체 30일 기준, 원 미만 반올림)", N, round(a_total / 30), "clean"),
        ("a_max_day", A, "9월 중 카드A 지출이 가장 많았던 날은 언제야?", '"YYYY-MM-DD" 문자열',
         a_day[0][0].isoformat(), "clean"),
        ("a_weekend", A, "9월 카드A 지출 중 주말(토·일) 지출 합계는?", N,
         sum(r["amount"] for r in sep if r["date"].weekday() >= 5), "clean"),
        ("a_mom", AA, "카드A 8월 대비 9월에 가장 많이 늘어난 카테고리와 늘어난 금액은?",
         '{"category": 이름, "increase": 금액}', {"category": inc[0][0], "increase": inc[0][1]}, "monthly"),
        ("a_subs", AA, "카드A 내역에서 매달 같은 금액으로 나가는 정기결제(구독)를 찾아줘.", "가맹점 이름 배열",
         {m for m, _, _ in SUBS}, "monthly"),
        ("a_budget", A + ["budget.json"], "budget.json 예산 기준으로 9월 카드A에서 예산을 넘은 카테고리는?",
         "카테고리 이름 배열", {c for c, v in a_cat.items() if v > BUDGET[c]}, "budget"),
        ("last_month", AA, "지난달에 카드A로 얼마 썼어?", N, a_total, "ambiguous"),
        ("b_net", B, "카드B 9월 실제 사용액은? 취소·부분취소는 빼고, 할부는 결제 총액 기준으로.", N, b_net, "cancel"),
        ("b_cancel_count", B, "카드B 9월 내역에 취소나 부분취소 건이 몇 건이야?", N,
         sum(r["status"] != "승인" for r in b), "cancel"),
        ("b_inst_bill", B, "카드B 할부 결제들만 보면 이번 달 청구되는 금액 합계는? (할부 총액 ÷ 개월 수, 원 미만 버림)", N,
         b_inst_bill, "installment"),
        ("b_bill", B, "카드B 9월 이용분 중 이번 달에 청구되는 금액은? 일시불은 취소 반영, 할부는 총액 ÷ 개월 수(원 미만 버림)만.", N,
         b_single_net + b_inst_bill, "installment"),
        ("b_top_merchant", B, "카드B 9월에 취소 반영 후 가장 많이 쓴 가맹점은?", "가맹점 이름 문자열", b_top[0][0], "cancel"),
        ("bank_out", BANK, "9월 은행 출금 합계는 얼마야?", N, bank_out, "format"),
        ("bank_in", BANK, "9월 은행 입금 합계는?", N, bank_in, "format"),
        ("bank_housing", BANK, "은행 내역에서 주거비(적요에 '월세' 또는 '관리비'가 있는 출금) 합계는?", N, housing, "category"),
        ("bank_no_card", BANK, "은행 출금 중 카드대금 결제(적요에 '카드'가 있는 것)를 뺀 출금 합계는?", N, bank_no_card,
         "double_count"),
        ("combined", AA[1:] + B + BANK, "카드A, 카드B, 은행 내역을 합쳐서 9월에 실제로 쓴 돈을 계산해줘. 은행의 카드대금 결제는 "
         "카드 내역과 겹치니 빼고, 은행의 나머지 출금은 모두 지출로 봐. 카드B는 취소 반영, 할부는 총액 기준.", N,
         a_total + b_net + bank_no_card, "double_count"),
        ("c_total", C, "카드C 9월 국내+해외 사용액을 원화로 합치면?", N, c_dom_krw + c_ov_krw, "xlsx"),
        ("c_usd", C, "카드C 해외 결제는 달러로 총 얼마야?", "USD 숫자(소수 둘째 자리)", c_usd, "xlsx"),
        ("c_ov_share", C, "카드C 9월 사용액(원화) 중 해외 결제 비중은 몇 %야? 소수 첫째 자리까지.", "숫자(%)",
         round(100 * c_ov_krw / (c_dom_krw + c_ov_krw), 1), "xlsx"),
        ("a_report", A, "9월 카드A 지출 리포트를 outputs/report.md로 만들어줘. 총액, 카테고리별 합계 표, 카테고리별 막대 차트"
         "(outputs/chart.png)를 넣어줘.", "총액 정수", a_total, "report"),
    ]
    tol = {"c_usd": 0.01, "c_ov_share": 0.05}
    return [{"id": i, "files": f, "prompt": p + CONTRACT % fmt, "expected": e, "tag": t, "tol": tol.get(i, 1)}
            for i, f, p, fmt, e, t in cases]


def _norm(x):
    return str(x).strip().replace(" ", "").lower()


def match(exp, got, tol=1):
    if isinstance(exp, bool) or exp is None:
        return exp == got
    if isinstance(exp, (int, float)):
        try:
            return abs(float(str(got).replace(",", "").replace("원", "").replace("%", "")) - exp) <= tol
        except (TypeError, ValueError):
            return False
    if isinstance(exp, str):
        return _norm(exp) == _norm(got)
    if isinstance(exp, set):
        return isinstance(got, list) and {_norm(g) for g in got} == {_norm(e) for e in exp}
    if isinstance(exp, list):
        return isinstance(got, list) and len(got) == len(exp) and all(match(e, g, tol) for e, g in zip(exp, got))
    if isinstance(exp, dict):
        return isinstance(got, dict) and {_norm(k) for k in got} == {_norm(k) for k in exp} and all(
            match(v, {_norm(k): w for k, w in got.items()}[_norm(k)], tol) for k, v in exp.items())
    raise TypeError(exp)


if __name__ == "__main__":
    import sys
    cs = build(sys.argv[1] if len(sys.argv) > 1 else "/tmp/finance-fixtures")
    for c in cs:
        print(f"{c['id']:16} {c['tag']:12} {c['expected']}")
