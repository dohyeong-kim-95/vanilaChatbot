# A fresh Ubuntu server for tests/test_l19_deploy.py: systemd as PID 1, git for the checkout, nothing else.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y -q systemd systemd-sysv dbus git && rm -rf /var/lib/apt/lists/* \
    && systemctl mask getty.target console-getty.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
