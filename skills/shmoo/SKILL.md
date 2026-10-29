---
name: shmoo
description: Shmoo plots from semiconductor test (ATE) logs - pass/fail over two swept conditions such as VDD x frequency or timing, per device/die and temperature. Use to show an interactive shmoo view, find abnormal devices (holes, walls, ceilings, low pass rate), or answer margin questions (Vmin, Fmax, pass rate).
---
# Shmoo

Do not write a parser or a plotting page. `scripts/shmoo.py` (standard library) reads the log and analyzes it;
the `show_shmoo` tool builds the interactive view from the same code.

## 1. Look at the log (one command)
```bash
python /skills/shmoo/scripts/shmoo.py /workspace/uploads/LOG.csv
```
It skips `#` preamble lines, guesses the column roles and prints them as `mapping:`, then a summary: axes, devices,
conditions, pass rate per condition, and findings. If a guess is wrong, pass it explicitly:
`--x FREQ_MHZ --y VDD_V --result RESULT --device WAFER,DIE_X,DIE_Y --filters TEMP_C [--pass PASS,1]`.
`--ascii DEVICE` prints the classic tester grid (`*` pass, `.` fail) for one device.

Roles: x = horizontal sweep (frequency, period, timing), y = vertical sweep (voltage), result = pass/fail,
device = columns that together identify one die (e.g. wafer + die x/y, or a serial), filters = other conditions
the user picks from (temperature, test name, pattern). Device labels are the device column values joined with "-".

## 2. Show it
Call `show_shmoo` with the file and the same mapping (`x`, `y`, `result`, `device`, `filters`, optional `title`).
One call; no HTML. If the user gave a spec window or an operating point, pass them in the log's own units:
`spec: {"x": [1600, 2400], "y": [1.14, 1.26]}`, `op: {"x": 2000, "y": 1.2}` (drawn as a dashed box and a circle;
the slide title then says whether the operating point or the spec window fails). Never invent them.
The user copies the view they set up as a slide image (sizes L/M/S) with the panel's capture button.

## 3. Answer questions with a short script
```python
import sys; sys.path.insert(0, "/skills/shmoo/scripts"); import shmoo
g = shmoo.load(path, x="FREQ_MHZ", y="VDD_V", result="RESULT", device=["WAFER", "DIE_X", "DIE_Y"], filters=["TEMP_C"])
combo = ("85",)                                  # one value per filter column, as strings
shmoo.all_pass_max_x(g, combo, "1.1")            # largest x where every device passes at y=1.1
shmoo.pass_rate(g, combo=combo)                  # % of tested cells passing (device=... for one device)
g.cell("1-3-2", {"TEMP_C": "85"}, "2000", "1.2") # "P" / "F" / None
shmoo.analyze(g)                                 # findings
```
Axis values are strings normalized like the summary prints them (`1.1`, not `1.10`; `2000`).

## 4. Reading findings
| kind | meaning | typical cause to mention |
|---|---|---|
| hole | a pocket of connected fail cells inside the passing region (pass on both sides in x and in y); separate pockets are separate findings | a pattern/timing-specific defect or a noise/resonance window; not a margin limit |
| wall | at the highest y the device still fails above an x far below the others | a slow path; more voltage does not help |
| ceiling | top-y row fails where lower y passes | over-stress at high voltage |
| low | pass rate far below the median device for the condition | general weakness; check with the plot |
Single flipped cells near the pass/fail boundary are normal marginal noise, not findings.
Name the device, condition and the x/y range in the answer.
