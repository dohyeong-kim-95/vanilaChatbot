---
name: card-transactions
description: Read Korean card and bank export files (CSV in UTF-8 or cp949, XLSX) and answer spending questions or build a monthly spending report. Use for any question about 카드/은행 거래내역, 지출, 카테고리별 합계, 취소, 할부, 해외결제, 예산.
---
# Card and bank transactions

Do not write a parser. `scripts/tx.py` (standard library only) already handles: cp949 or UTF-8,
description lines above the header, 합계/총계 rows, `12,300원` amounts, `2026.09.03 14:22` dates, xlsx with
several sheets, bank 출금/입금 columns, foreign-currency sheets.

## 1. Look at the files (one command)
```bash
python /skills/card-transactions/scripts/tx.py /workspace/uploads/*.csv /workspace/uploads/*.xlsx
```
It prints per file/sheet: encoding, header line, which column became which field, row count, sums, status
values, installment months, skipped lines. Check that it makes sense for the question (e.g. no
"ERROR"/unexpected skipped lines) before computing.

## 2. Compute with a short script
```python
import sys; sys.path.insert(0, "/skills/card-transactions/scripts")
from tx import load, load_all, signed
rows = load("/workspace/uploads/card_b_2026-09.csv")
```
Row fields: `date` "YYYY-MM-DD", `time`, `merchant` (가맹점 or bank 적요), `amount` (KRW, always positive),
`direction` "out"/"in", `status` (raw, "" if none), `cancel` (True for 취소/부분취소 status, 취소여부=Y, or a
negative card amount = refund), `installments` (1 = 일시불), `category` (None if the file
has none), `foreign_amount` + `currency` (foreign sheets; `amount` is then the 원화환산), `file`, `sheet`, `row`.

## 3. Rules (apply unless the user says otherwise)
| Situation | Rule |
|---|---|
| 취소 / 부분취소 / refund rows | Net them: `sum(signed(r) for r in rows)` (uses `cancel`). A 부분취소 row's amount is the cancelled part. |
| Spending per merchant/category with cancels | Group by key and sum `signed(r)`. |
| 할부 (installments > 1) | "사용액/결제 총액" = full amount. "이번 달 청구액" = `amount // installments` (원 미만 버림) unless told otherwise. |
| Bank 출금 that pays a card bill (적요 contains "카드" and "결제"/"대금") | Already counted in the card files. Exclude when combining bank + card spending. |
| Bank 입금 | Income, not spending. Never add to spending. |
| Foreign sheet | `amount` is KRW (원화환산); `foreign_amount` is in `currency`. Answer in what the user asked for. |
| "지난달" with no date given | The latest full month in the data. Say which month you used. |
| Category missing | Do not invent one; say the file has no category, or classify by merchant only if asked. |

## 4. Answer
- Give the number(s) and one line on how (files, rules applied: e.g. "취소 2건, 부분취소 1건 반영").
- Round only at the end. KRW as integers.
- Monthly report for the user to view: call `show_report` with numbers taken from your script output (if that
  tool is available). Optional `headlines` ({summary, categories, top}) override the automatic slide titles; only
  set them when the user's point differs from the default (e.g. 증가율, 예산 초과). For a file report, write markdown
  to `/workspace/outputs/`.
