#!/usr/bin/env bash
# Ставит бота на сервер как службу: запуск при старте сервера и перезапуск при сбоях.
# Запуск на сервере из папки с ботом:  sudo bash deploy/install.sh
# Повторный запуск обновляет код и config.toml, а базу на сервере не трогает.
set -euo pipefail

APP_DIR=/opt/ticket_alert
APP_USER=ticketalert
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите от root: sudo bash deploy/install.sh"
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "Нужен Python 3.11 или новее — выберите Ubuntu 24.04 или Debian 12."
  exit 1
fi
if [ ! -f "$SRC_DIR/config.toml" ]; then
  echo "В папке $SRC_DIR нет config.toml — скопируйте его на сервер вместе с ботом."
  exit 1
fi

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"

if [ "$SRC_DIR" != "$APP_DIR" ]; then
  rm -rf "$APP_DIR/ticket_alert"
  cp -r "$SRC_DIR/ticket_alert" "$APP_DIR/"
  cp "$SRC_DIR/config.toml" "$APP_DIR/config.toml"
  # База (направления, доступы, история) переносится только в первый раз:
  # дальше живая копия — на сервере.
  for f in ticket_alert.db cities.json countries.json; do
    if [ -f "$SRC_DIR/$f" ] && [ ! -f "$APP_DIR/$f" ]; then
      cp "$SRC_DIR/$f" "$APP_DIR/"
    fi
  done
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/config.toml"

install -m 644 "$SRC_DIR/deploy/ticket-alert.service" /etc/systemd/system/ticket-alert.service
systemctl daemon-reload
systemctl enable --quiet ticket-alert
systemctl restart ticket-alert
sleep 5
systemctl --no-pager --lines=15 status ticket-alert || true

echo
echo "Готово. Бот работает как служба ticket-alert."
echo "  Лог в реальном времени:  journalctl -u ticket-alert -f"
echo "  Перезапустить:           systemctl restart ticket-alert"
echo "  Остановить:              systemctl stop ticket-alert"
