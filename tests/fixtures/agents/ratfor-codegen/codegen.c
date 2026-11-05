/* A mock codegen agent written in C (Loop 40): stands in for another team's code generator of a fictional
 * language (Ratfor here). Deterministic: what it writes depends on the task and on the errors of the last attempt.
 *   codegen --task <task> --out <file.r> [--prev <file.r> --errors <errors.json>]
 * Tasks: "sum" (first a Ratfor error, then a Fortran error, then right), "bounds" (builds, fails when run, then
 * right), "stuck" (the same error every time), "crash" (SIGSEGV), "nofile" (writes nothing), "plant" (a program
 * that writes to /tmp and to the workspace: it must land in a throwaway container). */
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static char *slurp(const char *path) {
    FILE *f = path ? fopen(path, "rb") : NULL;
    if (!f) return calloc(1, 1);
    char *buf = calloc(1, 65537);
    if (fread(buf, 1, 65536, f) == 0 && ferror(f)) perror(path);
    fclose(f);
    return buf;
}

static void mkdirs(const char *file) {
    char d[4096];
    snprintf(d, sizeof d, "%s", file);
    for (char *p = d + 1; *p; p++)
        if (*p == '/') { *p = 0; if (mkdir(d, 0755) && errno != EEXIST) { perror(d); exit(2); } *p = '/'; }
}

static const char *SUM_RATFOR_ERR =  /* unbalanced parentheses: ratfor */
    "integer i, s\ns = 0\ndo i = 1, 10 {\n  s = s + (i\n}\nwrite(6,*) s\nend\n";
static const char *SUM_FORTRAN_ERR = /* "i >" with nothing after it: ratfor lets it through, gfortran does not */
    "integer i, s\ns = 0\ndo i = 1, 10 {\n  if (i > ) {\n    s = s + i\n  }\n}\nwrite(6,*) s\nend\n";
static const char *SUM_OK =
    "integer i, s\ns = 0\ndo i = 1, 10 {\n  s = s + i\n}\nwrite(6,*) s\nend\n";
static const char *BOUNDS_BAD =
    "integer a(10), i, s\ns = 0\ndo i = 1, 11 {\n  a(i) = i\n}\ndo i = 1, 10 {\n  s = s + a(i)\n}\nwrite(6,*) s\nend\n";

static const char *PLANT =
    "integer k\nopen(10, file='/tmp/planted', iostat=k)\nwrite(10,*) 1\nclose(10)\n"
    "open(11, file='/workspace/planted', iostat=k)\nwrite(6,*) 'planted', k\nend\n";

int main(int argc, char **argv) {
    const char *task = "", *out = NULL, *errors = NULL;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--task")) task = argv[i + 1];
        else if (!strcmp(argv[i], "--out")) out = argv[i + 1];
        else if (!strcmp(argv[i], "--errors")) errors = argv[i + 1];
    }
    if (!out) { fprintf(stderr, "codegen: --out is required\n"); return 2; }
    char *last = slurp(errors);
    fprintf(stderr, "codegen: task=%s errors=%s\n", task, errors ? errors : "-");
    const char *code;
    if (strstr(task, "crash")) raise(SIGSEGV);
    if (strstr(task, "nofile")) { puts("I thought about it."); return 0; }
    if (strstr(task, "plant")) code = PLANT;
    else if (strstr(task, "stuck")) code = SUM_RATFOR_ERR;
    else if (strstr(task, "bounds")) code = strstr(last, "\"run\"") ? SUM_OK : BOUNDS_BAD;
    else if (strstr(last, "\"fortran\"")) code = SUM_OK;
    else if (strstr(last, "\"ratfor\"")) code = SUM_FORTRAN_ERR;
    else code = SUM_RATFOR_ERR;
    mkdirs(out);
    FILE *f = fopen(out, "w");
    if (!f) { perror(out); return 1; }
    fputs(code, f);
    fclose(f);
    printf("wrote %s\n", out);
    return 0;
}
