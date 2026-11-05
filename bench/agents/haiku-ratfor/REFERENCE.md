# Ratfor in one page (the language reference this codegen agent works from)

Ratfor is Fortran 77 with C-like control flow. The preprocessor turns it into Fortran 77, which gfortran compiles.

- One statement per line (or several separated by `;`). No semicolon at the end of a line. `#` starts a comment.
- Blocks use braces: `if (x > 0) { ... } else { ... }`, `while (i <= n) { ... }`, `for (i = 1; i <= n; i = i + 1) { ... }`,
  `do i = 1, n { ... }`, `repeat { ... } until (done)`, `break`, `next` (= continue).
- An `if` with one statement needs no braces: `if (b == 0) b = 3`. `else if` works.
- Comparisons and logic: `==`, `!=`, `<`, `<=`, `>`, `>=`, `&` (and), `|` (or), `!` (not).
- Declarations first, Fortran style: `integer n, i, x(10)`, `double precision v(8)`. Arrays start at 1.
- Real numbers: use `double precision` and write constants with `d0` (`1.1d0`, `-9999d0`, `50d0`): a constant
  without `d0` is single precision, and `v > 1.1` with `v` double compares against a rounded 1.1.
- Read and write: `read(5,*) n` reads whitespace-separated numbers from stdin (one record per line);
  `read(5,*) x, y, (v(j), j = 1, m)` reads a line into scalars and an array. `write(6,*) x, y, b` prints a line.
- The program ends with `end`. No `program` statement is needed.
- Not available: dynamic memory, strings beyond Fortran `character`, recursion.

Example:
```ratfor
integer n, i, k
double precision s, v
read(5,*) n
s = 0d0
do i = 1, n {
  read(5,*) k, v
  if (v >= 0d0 & k != 0) s = s + v
}
write(6,*) s
end
```
