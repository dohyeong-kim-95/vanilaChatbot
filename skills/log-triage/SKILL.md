---
name: log-triage
description: First step for "이 로그/파일 분석해줘" when the user has not said what kind of file it is or what to look for. Identifies each file (test log, card/bank export, application text log, JSON lines, table), asks the user what to focus on with options that fit that file, then hands off to the matching skill and view.
---
# Log triage

## 1. Look (one command, all files)
```bash
python /skills/log-triage/scripts/sniff.py /workspace/uploads/*
```
Per file: size, lines, encoding, first lines, kind (table / text log / json lines / spreadsheet), and `fits:` the
skills whose loaders accept it. Trust `fits` over your own guess from the first lines.

## 2. Ask what matters (one ask_user call), unless the user already said it
Start the question with one sentence on what the file is, in the user's language, with concrete facts from step 1
(e.g. "L2610A 로트의 shmoo 테스트 로그예요. 장치 24개, 25/85°C, VDD 1.0–1.3V × 1600–3200MHz."). Then
"무엇을 중점적으로 볼까요?" with 2–4 options for this kind of file; first = the most common need. Put in each
option's description what you will show.

| fits | options to offer (adapt to the data) |
|---|---|
| shmoo | 이상한 칩 찾기 (shmoo 뷰 + 이상 패턴) / 조건별 마진 비교 (온도별 Vmin·Fmax) / 특정 칩 자세히 / 전체 요약 |
| card-transactions | 월간 지출 리포트 (고정 리포트) / 카테고리·가맹점별 합계 / 특정 질문(취소, 할부, 해외) |
| text log | 에러 패턴과 시점 (logview, 에러 먼저) / 시간대별 추이 (logview, 전체 레벨) / 특정 패턴 추적 |
| table / json lines (no fit) | 컬럼 요약·분포 / 특정 컬럼 기준 집계 / 이상값 찾기 |

If there are several unrelated files, ask which file first (one option per file) instead.

## 3. Do it
- Load the matching skill with use_skill (shmoo, card-transactions) and follow it; its view tool is the artifact
  (`show_shmoo`, `show_report`).
- Text logs (kind: text log): call `show_logview` with the file. It is the artifact: entries over time by level and
  masked message patterns; the user clicks levels, patterns and time buckets and copies the chart. Use its summary
  (levels, top patterns, busiest error buckets) for your answer; `python /skills/log-triage/scripts/logs.py FILE`
  prints the same summary, and `import logs; entries, _ = logs.parse(path); logs.aggregate(entries)` gives the numbers for follow-ups.
  Do not show raw log lines.
- Other tables / JSON lines: compute with bash (awk, sort | uniq -c) or python, then a markdown artifact.
- Finish with 2–3 sentences: what you found and what the user can click or ask next.
