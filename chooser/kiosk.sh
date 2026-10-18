#!/usr/bin/env bash
# Opens the app chooser full-screen on the Pi's display at login.
# Waits for the chooser service, then runs Chromium in kiosk mode with its own
# profile so it never collides with a normal Chromium window. Alt+F4 exits.
URL="http://localhost:8080"
# The desktop is labwc (Wayland); Chromium needs to be told so explicitly.
: "${WAYLAND_DISPLAY:=wayland-0}" "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
export WAYLAND_DISPLAY XDG_RUNTIME_DIR
for _ in $(seq 1 90); do
  curl -fs -o /dev/null "$URL/api/apps" && break
  sleep 1
done
exec chromium --kiosk --ozone-platform=wayland --noerrdialogs --disable-infobars --no-first-run \
  --password-store=basic --hide-crash-restore-bubble \
  --autoplay-policy=no-user-gesture-required \
  --user-data-dir="$HOME/.config/chooser-kiosk" "$URL"
