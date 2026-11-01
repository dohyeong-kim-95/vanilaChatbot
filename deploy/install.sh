#!/usr/bin/env bash
# Install or update vanilaChatbot on a Debian/Ubuntu server. Run as root from a git checkout:
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
#   --no-start              files only: no docker, systemctl or caddy (for building an image or a test)
#
# Layout: code in /opt/vanila/releases/<rev> (current -> the running one, the previous ones are kept for
# rollback), data in /var/lib/vanila/data, settings in /etc/vanila/*.env. Accounts: sudo vanila-auth add <name>.
set -euo pipefail

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
say() { printf '\n== %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
getenv() { sed -n "s/^$1=//p" "$2" 2>/dev/null | tail -n 1; }
secret() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

[[ $EUID -eq 0 ]] || die "run as root (sudo)"
git -c safe.directory="$SRC" -C "$SRC" rev-parse --git-dir >/dev/null 2>&1 || die "$SRC is not a git checkout"

# --- settings: from the arguments, else from the last install --------------------------------------------------
APP_HOST=${APP_HOST:-$(getenv AGENT_APP_ORIGIN "$ENV" | sed 's|^https://||')}
RENDER_HOST=${RENDER_HOST:-$(getenv AGENT_RENDER_ORIGIN "$ENV" | sed 's|^https://||')}
TLS=${TLS:-$(cat "$ETC/tls" 2>/dev/null || echo acme)}
[[ -n $APP_HOST && -n $RENDER_HOST ]] || die "first install: --app-host and --render-host are required"
for h in "$APP_HOST" "$RENDER_HOST"; do  # the app compares this string with the browser's Origin header
  [[ $h =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$ ]] || die "$h: lowercase host name, optionally :port"
  [[ $h != *:443 ]] || die "$h: leave out :443 (browsers do)"
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

# --- settings files (made once; secrets are never overwritten) -------------------------------------------------
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
chown root:vanila "$ENV" "$GWENV"
chmod 0640 "$ENV" "$GWENV"
echo "$TLS" >"$ETC/tls"

cat >/usr/local/bin/vanila-auth <<EOF
#!/bin/sh
# accounts: vanila-auth add|passwd|remove|list [name]  (as root)
set -a; . $ENV; set +a
cd $OPT/current && exec runuser -u vanila -- .venv/bin/python -m server.auth "\$@"
EOF
chmod 0755 /usr/local/bin/vanila-auth

# --- systemd and Caddy ------------------------------------------------------------------------------------------
say "systemd units, Caddy site"
install -m 0644 "$REL"/deploy/vanila-{app,render,gateway}.service /etc/systemd/system/
install -d -m 0755 /etc/caddy
tls_line=$([[ $TLS == internal ]] && echo "tls internal" || echo "${EMAIL:+tls $EMAIL}")
sed -e "s|@APP_HOST@|$APP_HOST|" -e "s|@RENDER_HOST@|$RENDER_HOST|" -e "s|@TLS@|$tls_line|" \
  "$REL/deploy/vanila.caddy" >/etc/caddy/vanila.caddy
touch /etc/caddy/Caddyfile
grep -qx 'import vanila.caddy' /etc/caddy/Caddyfile || printf '\nimport vanila.caddy\n' >>/etc/caddy/Caddyfile

PREV=$(readlink "$OPT/current" 2>/dev/null || true)
switch() { ln -sfn "$1" "$OPT/current.new" && mv -T "$OPT/current.new" "$OPT/current"; }
switch "$REL"

if ((!START)); then
  say "done (--no-start: nothing started)"
  exit 0
fi

say "start"
systemctl enable -q --now docker  # a fresh docker.io is not always running yet
docker pull -q python:3.12-slim >/dev/null
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl daemon-reload
systemctl enable -q vanila-app vanila-render vanila-gateway caddy
systemctl restart vanila-gateway vanila-render vanila-app
systemctl reload-or-restart caddy

healthy() {  # 401 = the app is up and asks for a login
  for _ in $(seq 30); do
    [[ $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8200/me) == 401 ]] && return 0
    sleep 1
  done
  return 1
}
if ! healthy; then
  journalctl -u vanila-app -n 30 --no-pager || true
  if [[ -n $PREV ]]; then
    switch "$PREV"
    systemctl restart vanila-gateway vanila-render vanila-app
    healthy || die "the new release did not start, and neither did $PREV after going back"
    die "the new release did not start; back on $PREV"
  fi
  die "the app did not start (journalctl -u vanila-app)"
fi

# keep the running release, the one before it and one more
ls -1d "$OPT"/releases/*/ | sed 's|/$||' | sort -r | tail -n +4 | while read -r old; do
  [[ $old == "$REL" || $old == "$PREV" ]] || rm -rf "$old"
done

say "running: https://$APP_HOST"
[[ -n $(getenv ANTHROPIC_API_KEY "$GWENV") || $(getenv LLM_BASE_URL "$ENV") != http://127.0.0.1:8100/v1 ]] ||
  echo "next: ANTHROPIC_API_KEY in $GWENV, then systemctl restart vanila-gateway (or LLM_* in $ENV)"
[[ -n $(vanila-auth list 2>/dev/null) ]] || echo "next: sudo vanila-auth add <name>"
[[ $TLS == acme ]] || echo "browsers must trust Caddy's CA: /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt"
