# Hesk in one page (the language reference this codegen agent works from)

Hesk is a small language for integer programs that read numbers from stdin and print lines. It is like no other
language: read the rules, do not guess them from Python, C or Pascal.

- Every simple statement ends with a period `.`. Blocks end with `done` (no period after `done`).
  Newlines and indentation do not matter. `--` starts a comment to the end of the line.
- Variables: declare every name before its first use with `var`, at the top level only (never inside a block).
  `var n, i, total.` gives integers set to 0. `var a[n].` an array of n integers, `var g[r, c].` a two-index array,
  all 0; sizes can be any expression of earlier variables (`read r, c. var g[r, c].`). Indexes start at 0:
  `a[0]` to `a[n - 1]`, `g[i, j]` with one pair of brackets. Only integers (no size limit), no booleans, no text
  variables. Names: letters, digits, `_`, starting with a letter.
- Assignment: `x <- x + 1.`  `g[i, j] <- 2.` (`=` is a comparison, never an assignment.)
- Arithmetic: `+ - *`, `div` (integer division, toward zero), `mod` (remainder, the sign of the left side),
  unary `-`, parentheses, `min(a, b)`, `max(a, b)`, `abs(a)`. There is no `/` and no `%`.
- Conditions: `=  <>  <  <=  >  >=` between two numbers; join conditions with `and`, `or`, `not`.
  `a < b < c` is an error: write `a < b and b < c`. A number is not a condition: `if n then` is an error.
- `if c then ... elif c then ... else ... done`
- `while c do ... done`
- `for i from a below b do ... done` runs i = a, a+1, ..., b-1 (`through b` instead of `below b`: up to b).
  a and b are computed once; i must be declared with var and cannot be assigned in the body; nothing runs
  when a >= b (below). `stop.` leaves the loop, `skip.` goes to its next round.
- `read x.` reads the next integer of stdin (spaces and newlines between numbers do not matter);
  `read r, c, g[i, j].` several in order.
- `say a, b, c.` prints one line: the items separated by one space. An item is a number or "text in quotes".
- No functions of your own, no recursion. The program is the statements from top to bottom; no main.

Example:
```hesk
-- the sum of the positive numbers at even positions, and how many there were
var n, i, v, total, count.
read n.
for i from 0 below n do
  read v.
  if v > 0 and i mod 2 = 0 then
    total <- total + v.
    count <- count + 1.
  done
done
say total, count.
```
