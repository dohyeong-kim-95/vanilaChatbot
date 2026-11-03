#!/usr/bin/env bash
# Install or update vanilaChatbot on Ubuntu 24.04+ / Debian 12+. Run as root from a git checkout:
#
#   sudo deploy/install.sh --app-host chat.example.com --render-host render.example.net
#   sudo deploy/install.sh                 # update: after git pull; hosts and secrets stay as they are
#
#   --app-host H[:PORT]     the address people open (https only); the renderer needs a different host name
#   --render-host H[:PORT]  artifacts are served from here (better a different domain, SPEC §9.7)
#   --tls acme|internal     acme (default): public certificates, both names must resolve here with 80/443 open.
#                           internal: Caddy's own CA, for a server without public DNS (browsers must trust it)
#   --email ADDR            contact for the acme account (optional)
#   --no-packages           do not apt-get (python3-venv, git, caddy, docker, curl, fontconfig must be there)
#   --no-start              first install only, files only: no docker, systemctl or caddy (to build an image)
#
# Layout: code in /opt/vanila/releases/<rev> (current -> the running one, the previous good one is kept for
# rollback), data in /var/lib/vanila/data, settings in /etc/vanila/*.env. Accounts: sudo vanila-auth add <name>.
set -euo pipefail
umask 022  # files Caddy and the service account read; a hardened root umask (027/077) would hide them
export LC_ALL=C  # tool output is parsed (apt-cache "Candidate:"); a Korean locale would translate it

APP_HOST= RENDER_HOST= TLS= EMAIL= PACKAGES=1 START=1
usage() { sed -n '2,/^set -e/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 2; }
while (($#)); do
  case $1 in
    --app-host) APP_HOST=${2:?}; shift ;;
    --render-host) RENDER_HOST=${2:?}; shift ;;
    --tls) TLS=${2:?}; shift ;;
    --email) EMAIL=${2:?}; shift ;;
    --no-packages) PACKAGES=0 ;;
    --no-start) START=0 ;;
    *) usage ;;
  esac
  shift
done

SRC=$(cd "$(dirname "$0")/.." && pwd)
OPT=/opt/vanila HOME_DIR=/var/lib/vanila ETC=/etc/vanila
DATA=$HOME_DIR/data
ENV=$ETC/vanila.env GWENV=$ETC/gateway.env
FONTS=/usr/local/share/fonts/vanilachatbot
CADDY_ADMIN=/var/lib/caddy/admin.sock
PORTS=(80 2019 8100 8200 8201)  # Caddy's http and admin, the services
say() { printf '\n== %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; show_log; exit 1; }
LOG=/opt/vanila/install.log LOG_FROM=  # every directory on the way is root's alone (/var/log is group-writable)
show_log() {  # back to the terminal and what was logged since the switch began; a closed terminal is no error now
  [[ -n $LOG_FROM ]] || return 0
  exec 1>&3 2>&4 3>&- 4>&-
  tail -n +"$LOG_FROM" "$LOG" 2>/dev/null || true
  LOG_FROM=
}
getenv() { [[ ! -f $2 ]] || sed -n "s/^$1=//p" "$2" | tail -n 1; }  # nothing (and no error) without the file
secret() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

[[ $EUID -eq 0 ]] || die "run as root (sudo)"
exec 9>/run/vanila-install.lock
flock -n 9 || die "another install.sh is running"
git -c safe.directory="$SRC" -C "$SRC" rev-parse --git-dir >/dev/null 2>&1 || die "$SRC is not a git checkout"
((START)) || [[ ! -e $OPT/current ]] ||
  die "--no-start on an installed server would leave an unchecked release for the next boot"

# --- settings: from the arguments, else from the last install --------------------------------------------------
OLD_APP_HOST=$(getenv AGENT_APP_ORIGIN "$ENV" | sed 's|^https://||')
OLD_RENDER_HOST=$(getenv AGENT_RENDER_ORIGIN "$ENV" | sed 's|^https://||')
OLD_TLS=$(cat "$ETC/tls" 2>/dev/null || echo acme)
OLD_EMAIL=$(cat "$ETC/email" 2>/dev/null || true)
APP_HOST=${APP_HOST:-$OLD_APP_HOST} RENDER_HOST=${RENDER_HOST:-$OLD_RENDER_HOST}
TLS=${TLS:-$OLD_TLS} EMAIL=${EMAIL:-$OLD_EMAIL}
[[ -z $EMAIL || $EMAIL =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$ ]] || die "--email: name@domain"
[[ -n $APP_HOST && -n $RENDER_HOST ]] || die "first install: --app-host and --render-host are required"
for h in "$APP_HOST" "$RENDER_HOST"; do  # the app compares this string with the browser's Origin header
  [[ $h =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$ ]] || die "$h: lowercase host name, optionally :port"
  [[ $h != *:443 ]] || die "$h: leave out :443 (browsers do)"
  for p in "${PORTS[@]}"; do [[ $h != *:$p ]] || die "$h: port $p is taken (${PORTS[*]})"; done
done
[[ ${APP_HOST%%:*} != "${RENDER_HOST%%:*}" ]] || die "app and renderer need different host names (cookies ignore ports)"
[[ $TLS == acme || $TLS == internal ]] || die "--tls: acme or internal"
site() { awk -F. '{ print (NF < 2 ? $0 : $(NF-1) "." $NF) }' <<<"${1%%:*}"; }
[[ $(site "$APP_HOST") != "$(site "$RENDER_HOST")" ]] ||
  echo "note: $APP_HOST and $RENDER_HOST look like one site; a separate domain for the renderer is safer (SPEC §9.7)"
if ! git -c safe.directory="$SRC" -C "$SRC" diff --quiet HEAD; then
  echo "note: uncommitted changes in $SRC are not installed (only HEAD is)"
fi

# --- packages ---------------------------------------------------------------------------------------------------
if ((PACKAGES)); then
  say packages
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  [[ $(apt-cache policy caddy) =~ Candidate:\ [0-9] ]] ||
    die "no caddy package here: use Ubuntu 24.04+ / Debian 12+, or add Caddy's apt repository first"
  pkgs=(python3-venv git caddy curl ca-certificates fontconfig)
  command -v docker >/dev/null || pkgs+=(docker.io)  # keep an existing Docker (e.g. docker-ce) as it is
  # keep our edits to Caddy's conffile (the Caddyfile) when an upgrade comes with a new one, without asking
  apt-get install -y -q -o Dpkg::Options::=--force-confold "${pkgs[@]}"
fi
need=(python3 git curl fc-cache)
((!START)) || need+=(caddy docker systemctl)
for c in "${need[@]}"; do command -v "$c" >/dev/null || die "$c not found"; done

# --- account and directories ------------------------------------------------------------------------------------
say "user vanila"
id vanila >/dev/null 2>&1 ||
  useradd --system --user-group --home-dir "$HOME_DIR" --shell /usr/sbin/nologin vanila
getent group docker >/dev/null || groupadd --system docker
install -d -o vanila -g vanila -m 0700 "$HOME_DIR" "$DATA"
install -d -o root -g vanila -m 0750 "$ETC"
install -d -m 0755 "$OPT" "$OPT/releases"

# --- code: a new release next to the old ones -------------------------------------------------------------------
REV=$(git -c safe.directory="$SRC" -C "$SRC" rev-parse --short=12 HEAD)
REL=$OPT/releases/$(date +%Y%m%d-%H%M%S)-$REV
say "release $REL"
mkdir "$REL"
git -c safe.directory="$SRC" -C "$SRC" archive HEAD | tar -x -C "$REL"
python3 -m venv "$REL/.venv"
"$REL/.venv/bin/pip" install -q --disable-pip-version-check -r "$REL/requirements.txt"
# report sections are captured with headless Chromium; Playwright keeps its browsers here, shared by releases
export PLAYWRIGHT_BROWSERS_PATH=$OPT/ms-playwright
if ((PACKAGES)); then "$REL/.venv/bin/playwright" install --with-deps chromium; else
  "$REL/.venv/bin/playwright" install chromium; fi
chmod -R a+rX "$OPT/ms-playwright" "$REL"

# --- fonts for captures (OFL, DESIGN.md 4.2) --------------------------------------------------------------------
if [[ ! -s $FONTS/OFL.txt ]]; then
  say fonts
  install -d -m 0755 "$FONTS"
  for f in arimo/Arimo%5Bwght%5D.ttf notosanskr/NotoSansKR%5Bwght%5D.ttf arimo/OFL.txt; do
    curl -fsSL -m 120 -o "$FONTS/${f##*/}" "https://raw.githubusercontent.com/google/fonts/main/ofl/$f"
  done
  chmod 0644 "$FONTS"/*
  fc-cache -f "$FONTS" >/dev/null
fi

cat >/usr/local/bin/vanila-auth <<EOF
#!/bin/sh
# accounts: vanila-auth add|passwd|remove|list [name]  (as root)
root=\$(sed -n 's/^AGENT_ROOT=//p' $ENV | tail -n 1)
cd $OPT/current && exec runuser -u vanila -- env AGENT_ROOT="\$root" .venv/bin/python -m server.auth "\$@"
EOF
chmod 0755 /usr/local/bin/vanila-auth

# --- before touching the running release or its settings: what can fail without them -----------------------------
if ((START)); then
  say "docker image"
  systemctl enable -q --now docker  # a fresh docker.io is not always running yet
  IMAGE=$(getenv SANDBOX_IMAGE "$ENV")
  if [[ -z $IMAGE ]]; then  # ours: tagged by the Dockerfile's hash (agent/sandbox.py), built here once per change
    IMAGE=vanilachatbot/sandbox:$(sha256sum "$SRC/sandbox/Dockerfile" | cut -c1-12)
    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
      say "building the sandbox image $IMAGE (a few minutes, needs the network once)"
      # pip in the build does not see the host's proxy or pip.conf: pass them on when they are set
      args=()
      for v in HTTPS_PROXY HTTP_PROXY NO_PROXY https_proxy http_proxy no_proxy PIP_INDEX_URL PIP_TRUSTED_HOST; do
        [[ -n ${!v:-} ]] && args+=(--build-arg "$v=${!v}")
      done
      docker build -q "${args[@]}" -t "$IMAGE" "$SRC/sandbox" >/dev/null \
        || die "sandbox image $IMAGE: build failed (behind a proxy? set HTTPS_PROXY / PIP_INDEX_URL, or SANDBOX_IMAGE)"
    fi
  elif ! docker pull -q "$IMAGE" >/dev/null; then  # a locally built image, or the registry is down or rate-limited
    docker image inspect "$IMAGE" >/dev/null 2>&1 || die "sandbox image $IMAGE: cannot pull it and it is not here"
    echo "warning: could not pull $IMAGE; using the copy already here"
  fi
fi

# --- from here until the switch is done: what this install changes is kept to put back -----------------------------
# edited in place, at the file a symlinked Caddyfile points to (it stays a symlink); Caddy is always given the link
CADDYFILE=$(readlink -f /etc/caddy/Caddyfile || echo /etc/caddy/Caddyfile)
CHANGED=("$ENV" "$ETC/tls" "$ETC/email" "$CADDYFILE" /etc/caddy/vanila.caddy
         /etc/systemd/system/vanila-{app,render,gateway}.service)
NEW=()
for f in "${CHANGED[@]}"; do
  [[ ! -e $f.prev ]] || die "$f.prev is left from an interrupted install: compare it with $f, keep one, run again"
done
restore() {  # settings, Caddy files and units as they were before this install
  local f
  for f in "${CHANGED[@]}"; do
    if [[ -f $f.prev ]]; then mv -f "$f.prev" "$f"; elif [[ " ${NEW[*]} " == *" $f "* ]]; then rm -f "$f"; fi
  done
}
# A closed SSH session must not stop it halfway (current switched, settings not back): signals are ignored, and
# output goes to a file, since writing to a terminal that is gone fails and set -e would end the script there.
trap '' HUP INT TERM PIPE
echo "(switching: interrupts are ignored and output goes to $LOG until the end)"
touch "$LOG" && chmod 0600 "$LOG"
LOG_FROM=$(($(wc -l <"$LOG") + 1))
exec 3>&1 4>&2 1>>"$LOG" 2>&1
echo "== install.sh $(date -Is) $REL"
SWITCHING=1
trap '((!SWITCHING)) || { restore; echo "install.sh: stopped while switching; settings put back" >&2 || true; }; show_log' EXIT
for f in "${CHANGED[@]}"; do if [[ -f $f ]]; then cp -p "$f" "$f.prev"; else NEW+=("$f"); fi; done

# --- settings files: secrets are made once and never overwritten ---------------------------------------------------
say "settings $ETC"
if [[ ! -f $GWENV ]]; then
  ( umask 077; cat >"$GWENV" <<EOF
# LLM gateway (vanila-gateway.service): OpenAI-compatible API on 127.0.0.1:8100 with the Anthropic API behind it.
# Put your key here, then: systemctl restart vanila-gateway
GATEWAY_BACKEND=anthropic
ANTHROPIC_API_KEY=
GATEWAY_MODEL=claude-haiku-5-5
GATEWAY_EFFORT=low
# the app's LLM_API_KEY: other accounts on this server cannot use the gateway without it
GATEWAY_API_KEY=$(secret)
EOF
  )
fi
if [[ ! -f $ENV ]]; then
  ( umask 077; cat >"$ENV" <<EOF
AGENT_ROOT=$DATA
AGENT_APP_ORIGIN=https://$APP_HOST
AGENT_RENDER_ORIGIN=https://$RENDER_HOST
AGENT_RENDER_SECRET=$(secret)
# "" = Playwright's Chromium (installed by install.sh); "chrome" = an installed Google Chrome
AGENT_CHROME_CHANNEL=
PLAYWRIGHT_BROWSERS_PATH=$OPT/ms-playwright
AGENT_EFFORT=low
# big files and long jobs (bytes; the defaults are shown): file, project, user, free disk kept, cache, parallel jobs
#AGENT_MAX_FILE=4294967296
#AGENT_UPLOAD_QUOTA=21474836480
#AGENT_USER_QUOTA=42949672960
#AGENT_DISK_RESERVE=2147483648
#AGENT_CACHE_MAX=21474836480
#AGENT_JOB_SLOTS=
# any OpenAI-compatible server; the default is the gateway on this machine (gateway.env)
LLM_BASE_URL=http://127.0.0.1:8100/v1
LLM_API_KEY=$(getenv GATEWAY_API_KEY "$GWENV")
LLM_MODEL=claude-haiku-5-5
EOF
  )
else  # hosts given again: the origins follow
  sed -i -e "s|^AGENT_APP_ORIGIN=.*|AGENT_APP_ORIGIN=https://$APP_HOST|" \
         -e "s|^AGENT_RENDER_ORIGIN=.*|AGENT_RENDER_ORIGIN=https://$RENDER_HOST|" "$ENV"
fi
chown root:vanila "$ENV"
chmod 0640 "$ENV"
chown root:root "$GWENV"  # the API key: systemd reads it as root, the services never need the file
chmod 0600 "$GWENV"
echo "$TLS" >"$ETC/tls"
echo "$EMAIL" >"$ETC/email"

# --- switch: units, Caddy site and current from one release; then all three services must answer -----------------
# never through a proxy from the environment (sudo reads /etc/environment): these are this machine's own ports
probe() { curl -s -m 3 --noproxy '*' -o /dev/null -w '%{http_code}' "$@" || true; }
answers() {  # app 401 (asks for a login), renderer 404 (no such artifact), gateway any HTTP answer
  [[ $(probe http://127.0.0.1:8200/me) == 401 && $(probe http://127.0.0.1:8201/) == 404 &&
     $(probe http://127.0.0.1:8100/v1/models) != 000 ]]
}
restarted() {  # systemd brought a unit back since our restart (a manual restart sets the count to 0)
  local u
  for u in vanila-app vanila-render vanila-gateway; do
    [[ $(systemctl show -p NRestarts --value "$u") == 0 ]] || return 0
  done
  return 1
}
healthy() {  # answering, and a few seconds later still the same processes (not answering again after a crash)
  for _ in $(seq 30); do
    if answers; then
      sleep 3
      # one unit at a time: is-active with several units succeeds when any one of them is active
      systemctl is-active -q vanila-app && systemctl is-active -q vanila-render &&
        systemctl is-active -q vanila-gateway && ! restarted && answers
      return
    fi
    sleep 1
  done
  return 1
}
caddy_admin() {  # Caddy's admin API listens on localhost:2019 for every local account; a socket only caddy can use
  local json admin
  json=$(caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile) || return 1
  admin=$(python3 -c '
import json, sys
a = json.load(sys.stdin).get("admin") or {}
print("off" if a.get("disabled") else a.get("listen", ""))' <<<"$json") || return 1
  case $admin in
    off) CADDY_RESTART=1; return 0 ;;  # no admin API: only a restart applies a new config
    "") ;;                              # the default, localhost:2019
    *) [[ $admin == unix/* ]] || echo "note: Caddy's admin API listens on $admin, which every local account can reach"
       return 0 ;;                      # chosen by whoever runs this server
  esac
  # into the global options block (the first token is `{`), or a new one in front; the file is rewritten in place
  python3 - "$CADDYFILE" "$CADDY_ADMIN" <<'PY' || return 1
import re, sys
path, sock = sys.argv[1:]
text = open(path).read()
start = re.match(r"(?:\s|#[^\n]*)*", text).end()
line = f"\tadmin unix/{sock}\n"
if text[start:start + 1] == "{" and not text.startswith(("{$", "{env."), start):  # those are site addresses
    end = text.find("\n", start)
    end = len(text) if end < 0 else end
    rest = text[start + 1:end].strip()
    if rest and not rest.startswith("#"):
        sys.exit(f"{path}: put `admin unix/{sock}` in the global options block yourself (it is on one line)")
    new = text[:end] + "\n" + line + text[end + 1:]
else:
    new = "{\n" + line + "}\n\n" + text
with open(path, "r+") as f:
    f.write(new)
    f.truncate()
PY
  CADDY_RESTART=1  # the running Caddy still listens on the old address, so a reload cannot reach it
}
activate() {  # every step checked by hand: set -e does not apply inside a function called from `if`
  local rel=$1 tls_line
  install -m 0644 "$rel"/deploy/vanila-{app,render,gateway}.service /etc/systemd/system/ || return 1
  install -d -m 0755 /etc/caddy || return 1
  tls_line=$([[ $TLS == internal ]] && echo "tls internal" || echo "${EMAIL:+tls $EMAIL}")
  sed -e "s|@APP_HOST@|$APP_HOST|" -e "s|@RENDER_HOST@|$RENDER_HOST|" -e "s|@TLS@|$tls_line|" \
    "$rel/deploy/vanila.caddy" >/etc/caddy/vanila.caddy.new || return 1
  mv /etc/caddy/vanila.caddy.new /etc/caddy/vanila.caddy || return 1
  [[ -f $CADDYFILE ]] || install -m 0644 /dev/null "$CADDYFILE" || return 1
  grep -Eq '^[[:space:]]*import[[:space:]]+(/etc/caddy/)?vanila\.caddy[[:space:]]*$' "$CADDYFILE" ||
    printf '\nimport /etc/caddy/vanila.caddy\n' >>"$CADDYFILE" || return 1
  { ln -sfn "$rel" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"; } || return 1
  ((START)) || return 0
  caddy_admin || return 1
  # as the caddy account: root can read files that Caddy itself cannot
  runuser -u caddy -- caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null || return 1
  # reset-failed: a release that crashed in a loop leaves its units start-limited, and going back must still start
  { systemctl daemon-reload && systemctl enable -q vanila-app vanila-render vanila-gateway caddy &&
    { systemctl reset-failed vanila-app vanila-render vanila-gateway 2>/dev/null || true; } &&
    systemctl restart vanila-gateway vanila-render vanila-app; } || return 1
  if ((${CADDY_RESTART:-0})); then systemctl restart caddy || return 1; CADDY_RESTART=0
  else systemctl reload-or-restart caddy || return 1; fi
  healthy
}

PREV=$(readlink "$OPT/current" 2>/dev/null || true)
say "switch to $REL"
if ! activate "$REL"; then
  ((!START)) || journalctl -u vanila-app -u vanila-render -u vanila-gateway -u caddy -n 40 --no-pager || true
  rm -rf "$REL"
  # first install: nothing to go back to; leave no services, site or units behind
  [[ -n $PREV ]] || ((!START)) || systemctl disable -q --now vanila-app vanila-render vanila-gateway || true
  restore  # a new setting may be the cause
  SWITCHING=0
  if [[ -z $PREV ]]; then
    rm -f "$OPT/current"
    # restart, not reload: the running Caddy may already listen on the admin socket the restored file no longer has
    ((!START)) || { systemctl daemon-reload; systemctl restart caddy || echo "warning: caddy did not restart"; }
    die "the first release did not start (journalctl output above)"
  fi
  APP_HOST=$OLD_APP_HOST RENDER_HOST=$OLD_RENDER_HOST TLS=$OLD_TLS EMAIL=$OLD_EMAIL
  activate "$PREV" || die "the new release did not start, and neither did $PREV after going back"
  die "the new release did not start; back on $PREV with its settings"
fi
SWITCHING=0
for f in "${CHANGED[@]}"; do rm -f "$f.prev"; done
# only the app needs Docker (its unit adds the group): docker membership is root on this machine (SPEC 부록 C).
# Not the account's own group, which every process of it (renderer, gateway, vanila-auth) would get. Only now:
# a release from before that unit line, gone back to, still needs the account's membership.
[[ " $(id -nG vanila) " != *" docker "* ]] || gpasswd -d vanila docker >/dev/null ||
  echo "warning: could not take vanila out of the docker group (gpasswd -d vanila docker)"

if ((!START)); then
  say "done (--no-start: nothing started)"
  show_log
  exit 0
fi
touch "$REL/.ok"

# keep the running release and the newest earlier one that ran; failed or half-made ones go
keep=$(ls -1d "$OPT"/releases/*/.ok 2>/dev/null | sed 's|/\.ok$||' | grep -vx "$REL" | sort -r | head -n 1 || true)
for old in "$OPT"/releases/*; do
  [[ $old == "$REL" || $old == "$PREV" || $old == "$keep" ]] || rm -rf "$old"
done

say "running: https://$APP_HOST"
# through Caddy as a browser would come (certificate checked for acme): warn only, DNS may still be on its way
host=${APP_HOST%%:*} port=443
[[ $APP_HOST != *:* ]] || port=${APP_HOST##*:}
[[ $(probe $([[ $TLS == internal ]] && echo -k) --resolve "$host:$port:127.0.0.1" "https://$APP_HOST/me") == 401 ]] ||
  echo "warning: https://$APP_HOST does not answer through Caddy yet (journalctl -u caddy: certificate, DNS, ports)"
[[ -n $(getenv ANTHROPIC_API_KEY "$GWENV") || $(getenv LLM_BASE_URL "$ENV") != http://127.0.0.1:8100/v1 ]] ||
  echo "next: ANTHROPIC_API_KEY in $GWENV, then systemctl restart vanila-gateway (or LLM_* in $ENV)"
[[ -n $(vanila-auth list 2>/dev/null) ]] || echo "next: sudo vanila-auth add <name>"
[[ $TLS == acme ]] || echo "browsers must trust Caddy's CA: /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt"
show_log
