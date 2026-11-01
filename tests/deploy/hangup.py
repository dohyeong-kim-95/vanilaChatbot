"""Run a command in a pseudo-terminal and close the terminal once `marker` appears, the way an SSH session that
drops looks to the program (SIGHUP, then EIO on every write). Prints the command's wait status.

    python3 hangup.py '(switching' /src/deploy/install.sh
"""
import os
import pty
import select
import sys

marker, cmd = sys.argv[1].encode(), sys.argv[2:]
pid, fd = pty.fork()
if pid == 0:
    os.execvp(cmd[0], cmd)
seen = b""
while marker not in seen:
    if not select.select([fd], [], [], 900)[0]:
        sys.exit("marker never appeared")
    try:
        chunk = os.read(fd, 4096)
    except OSError:
        sys.exit("command ended before the marker: " + seen.decode(errors="replace")[-500:])
    if not chunk:
        sys.exit("command ended before the marker")
    seen += chunk
os.close(fd)  # the session is gone
_, status = os.waitpid(pid, 0)
print("exit", os.waitstatus_to_exitcode(status))
