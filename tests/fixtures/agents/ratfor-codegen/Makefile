# Built by the operator before the agent is connected (the sandbox has no network and /agents is read-only).
# Static, so it runs in the sandbox image whatever libc the build machine has.
bin/codegen: codegen.c
	mkdir -p bin
	$(CC) -O2 -static -Wall -o $@ codegen.c
