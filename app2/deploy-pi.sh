#!/usr/bin/env bash
# Put the PPE Monitor page on pi-07 and restart the app there.
#
#   ./deploy-pi.sh            copy index.html to pi-07:~/app and restart
#   ./deploy-pi.sh --check    only report what is on the Pi, change nothing
#
# The Pi's starter server (~/app/server.js) reads index.html once at start-up
# and serves it on :3000 behind nginx on :80, so a restart is required.
set -euo pipefail

HOST="${PI_HOST:-pi-07}"           # ssh config alias (user hacker7)
APP_DIR='~/app'
HERE="$(cd "$(dirname "$0")" && pwd)"
PAGE="$HERE/index.html"

step() { printf '\033[1m==> %s\033[0m\n' "$*"; }

check() {
  step "State of $HOST:$APP_DIR"
  ssh "$HOST" "cd $APP_DIR && ls -la && echo && head -c 300 index.html | grep -o '<title>[^<]*' ; echo; systemctl is-active app; curl -s -o /dev/null -w 'port 3000 -> HTTP %{http_code}\n' http://127.0.0.1:3000/"
}

if [ "${1:-}" = "--check" ]; then check; exit 0; fi

[ -f "$PAGE" ] || { echo "no index.html next to this script" >&2; exit 1; }

step "Backing up the current page on the Pi (first time keeps the starter page as index.starter.html)"
ssh "$HOST" "cd $APP_DIR && { [ -f index.starter.html ] || cp index.html index.starter.html; } && cp index.html index.prev.html"

step "Copying index.html"
scp -q "$PAGE" "$HOST:$APP_DIR/index.html"

step "Restarting the app"
ssh "$HOST" "sudo /usr/bin/systemctl restart app && sleep 2 && curl -s -o /dev/null -w 'port 3000 -> HTTP %{http_code}\n' http://127.0.0.1:3000/ && curl -s http://127.0.0.1:3000/ | grep -o '<title>[^<]*'"

ip="$(ssh "$HOST" "hostname -I | awk '{print \$1}'")"
step "Done. Open http://pi-07.local or http://$ip"
