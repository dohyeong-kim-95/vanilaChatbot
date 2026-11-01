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
SETTINGS=("$ENV" "$ETC/tls" "$ETC/email")  # what an install may change; restored when it goes back
FONTS=/usr/local/share/fonts/vanilachatbot
CADDY_ADMIN=/var/lib/caddy/admin.sock
PORTS=(80 2019 8100 8200 8201)  # Caddy's http and admin, the services
say() { printf '\n== %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
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
  apt-get install -y -q "${pkgs[@]}"
fi
need=(python3 git curl fc-cache)
((!START)) || need+=(caddy docker systemctl)
for c in "${need[@]}"; do command -v "$c" >/dev/null || die "$c not found"; done

# --- account and directories ------------------------------------------------------------------------------------
say "user vanila"
id vanila >/dev/null 2>&1 ||
  useradd --system --user-group --home-dir "$HOME_DIR" --shell /usr/sbin/nologin vanila
getent group docker >/dev/null || groupadd --system docker
usermod -aG docker vanila  # needed to start sandboxes; this makes the account as powerful as root (SPEC §9.7)
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
    curl -fsSL -o "$FONTS/${f##*/}" "https://raw.githubusercontent.com/google/fonts/main/ofl/$f"
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
  docker pull -q "${IMAGE:-python:3.12-slim}" >/dev/null
fi

# --- settings files: secrets are made once and never overwritten; the rest is kept to go back to ------------------
say "settings $ETC"
for f in "${SETTINGS[@]}"; do [[ ! -f $f ]] || cp -p "$f" "$f.prev"; done
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
probe() { curl -s -m 3 -o /dev/null -w '%{http_code}' "$@" || true; }
answers() {  # app 401 (asks for a login), renderer 404 (no such artifact), gateway any HTTP answer
  [[ $(probe http://127.0.0.1:8200/me) == 401 && $(probe http://127.0.0.1:8201/) == 404 &&
     $(probe http://127.0.0.1:8100/v1/models) != 000 ]]
}
healthy() {  # answering, and still up a few seconds later (a release that dies right after starting is not good)
  for _ in $(seq 30); do
    if answers; then
      sleep 3
      systemctl is-active -q vanila-app vanila-render vanila-gateway && answers
      return
    fi
    sleep 1
  done
  return 1
}
caddy_admin() {  # Caddy's admin API is open to every local account on localhost:2019; a socket only caddy can use
  local f=/etc/caddy/Caddyfile
  ! grep -q '^[[:space:]]*admin[[:space:]]' "$f" || return 0  # already chosen by whoever runs this server
  if [[ $(grep -v '^[[:space:]]*\(#.*\)\?$' "$f" | head -n 1) == "{" ]]; then  # a global options block: add to it
    sed -i "0,/^{[[:space:]]*\$/s||{\n\tadmin unix/$CADDY_ADMIN|" "$f"
  else
    { printf '{\n\tadmin unix/%s\n}\n\n' "$CADDY_ADMIN"; cat "$f"; } >"$f.new" && mv "$f.new" "$f"
  fi
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
  [[ -f /etc/caddy/Caddyfile ]] || install -m 0644 /dev/null /etc/caddy/Caddyfile || return 1
  grep -qx 'import vanila.caddy' /etc/caddy/Caddyfile || printf '\nimport vanila.caddy\n' >>/etc/caddy/Caddyfile ||
    return 1
  caddy_admin || return 1
  { ln -sfn "$rel" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"; } || return 1
  ((START)) || return 0
  # as the caddy account: root can read files that Caddy itself cannot
  runuser -u caddy -- caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null || return 1
  { systemctl daemon-reload && systemctl enable -q vanila-app vanila-render vanila-gateway caddy &&
    systemctl restart vanila-gateway vanila-render vanila-app; } || return 1
  if ((${CADDY_RESTART:-0})); then systemctl restart caddy || return 1; CADDY_RESTART=0
  else systemctl reload-or-restart caddy || return 1; fi
  healthy
}

PREV=$(readlink "$OPT/current" 2>/dev/null || true)
say "switch to $REL"
if ! activate "$REL"; then
  ((!START)) || journalctl -u vanila-app -u vanila-render -u vanila-gateway -u caddy -n 40 --no-pager || true
  [[ -n $PREV ]] || die "the first release did not start (journalctl -u vanila-app)"
  rm -rf "$REL"
  for f in "${SETTINGS[@]}"; do [[ ! -f $f.prev ]] || mv "$f.prev" "$f"; done  # a new setting may be the cause
  APP_HOST=$OLD_APP_HOST RENDER_HOST=$OLD_RENDER_HOST TLS=$OLD_TLS EMAIL=$OLD_EMAIL
  activate "$PREV" || die "the new release did not start, and neither did $PREV after going back"
  die "the new release did not start; back on $PREV with its settings"
fi
for f in "${SETTINGS[@]}"; do rm -f "$f.prev"; done

if ((!START)); then
  say "done (--no-start: nothing started)"
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
