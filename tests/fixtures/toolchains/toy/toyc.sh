#!/bin/sh
# Toy's "compiler": every line must be "print <text>"; a compiler that prints gcc-style errors and exits 1.
# toyc.sh <src> <program>
bad=$(grep -nv '^print ' "$1" | sed "s|^\([0-9]*\):.*|$1:\1: expected 'print <text>'|")
[ -n "$bad" ] && { echo "$bad"; exit 1; }
{ echo '#!/bin/sh'; sed 's/^print /echo /' "$1"; } > "$2" && chmod +x "$2"
