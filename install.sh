#!/usr/bin/env bash
# Установка сервиса из папки с текущим ботом.
set -euo pipefail

BOT_NAME="playerokuniversal"
BOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICE_FILE="/etc/systemd/system/${BOT_NAME}.service"
CMD="pluniversal"
COMMAND_FILE="/usr/local/bin/${CMD}"
PYTHON="python3.12"
VENV_PYTHON="${BOT_DIR}/venv/bin/python"

info() { printf '\n%s\n' "$*"; }
error() { printf '\nОшибка: %s\n' "$*" >&2; exit 1; }

is_configured() {
  "$VENV_PYTHON" - "$BOT_DIR/bot_settings/config.json" <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding='utf-8-sig') as stream:
        config = json.load(stream)
    values = (config['playerok']['api']['cookies'],
              config['telegram']['api']['token'],
              config['telegram']['bot']['password'])
    sys.exit(0 if all(isinstance(value, str) and value.strip() for value in values) else 1)
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(1)
PY
}

install_dependencies() {
  if ! command -v "$PYTHON" >/dev/null 2>&1; then
    info "Устанавливаю Python 3.12..."
    command -v apt-get >/dev/null 2>&1 || error "Установите Python 3.12 и модуль venv."
    apt-get update
    local ubuntu_version=""
    if [[ -f /etc/os-release ]]; then
      ubuntu_version="$(. /etc/os-release; printf '%s' "${VERSION_ID:-}")"
      if [[ "$(. /etc/os-release; printf '%s' "${ID:-}")" == ubuntu && "${ubuntu_version%%.*}" -lt 24 ]]; then
        apt-get install -y software-properties-common
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update
      fi
    fi
    apt-get install -y python3.12 python3.12-venv
  fi
  if [[ ! -x "$VENV_PYTHON" ]] || ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    info "Создаю виртуальное окружение в ${BOT_DIR}/venv..."
    if ! "$PYTHON" -m venv "$BOT_DIR/venv"; then
      command -v apt-get >/dev/null 2>&1 || error "Установите модуль venv для Python 3.12."
      apt-get update
      apt-get install -y python3.12-venv
      "$PYTHON" -m venv "$BOT_DIR/venv"
    fi
  fi
  info "Устанавливаю зависимости бота..."
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install -r "$BOT_DIR/requirements.txt"
}

write_launcher() {
  cat > "$BOT_DIR/run.sh" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
BOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$BOT_DIR"
export PYTHONUNBUFFERED=1
export TERM=xterm-256color
exec "$BOT_DIR/venv/bin/python" -u "$BOT_DIR/bot.py" "$@"
WRAPPER
  chmod 755 "$BOT_DIR/run.sh"
}

write_service() {
  "$VENV_PYTHON" - "$SERVICE_FILE" "$BOT_DIR" "$BOT_NAME" <<'PY'
from pathlib import Path
import sys

target, folder, name = sys.argv[1:]
def quote(value, command=False):
    if any(character in value for character in '\n\r\0'):
        raise ValueError('Путь к боту содержит неподдерживаемые символы')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if command:
        value = value.replace('$', '$$')
    return '"' + value + '"'

Path(target).write_text(
    '[Unit]\n'
    'Description=Telegram Bot - Playerok Universal\n'
    'After=network-online.target\n'
    'Wants=network-online.target\n\n'
    '[Service]\n'
    'Type=simple\n'
    # WorkingDirectory принимает путь целиком; кавычки были бы частью пути.
    f'WorkingDirectory={folder.replace("%", "%%")}/\n'
    f'ExecStart=/bin/bash {quote(folder + "/run.sh", command=True)}\n'
    'Restart=on-failure\n'
    'RestartSec=5\n'
    'StandardOutput=journal\n'
    'StandardError=journal\n'
    f'SyslogIdentifier={name}\n'
    'Environment=PYTHONUNBUFFERED=1\n'
    'Environment=TERM=xterm-256color\n\n'
    '[Install]\n'
    'WantedBy=multi-user.target\n', encoding='utf-8')
PY
  chmod 644 "$SERVICE_FILE"
}

write_controller() {
  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf 'SERVICE=%q\nBOT_DIR=%q\n' "$BOT_NAME" "$BOT_DIR"
    cat <<'BOTCMD'
PYTHON="$BOT_DIR/venv/bin/python"
CONFIG="$BOT_DIR/bot_settings/config.json"

is_configured() {
  "$PYTHON" - "$CONFIG" <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding='utf-8-sig') as stream:
        config = json.load(stream)
    values = (config['playerok']['api']['cookies'],
              config['telegram']['api']['token'],
              config['telegram']['bot']['password'])
    sys.exit(0 if all(isinstance(value, str) and value.strip() for value in values) else 1)
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(1)
PY
}

case "${1:-}" in
  start | restart)
    if ! is_configured; then
      printf 'Сначала настройте бот: pluniversal setup\n' >&2
      exit 1
    fi
    systemctl "$1" "$SERVICE"
    printf 'Команда выполнена. Логи: pluniversal log\n'
    ;;
  stop | enable | disable)
    systemctl "$1" "$SERVICE"
    ;;
  status)
    systemctl status "$SERVICE" --no-pager
    ;;
  log | logs)
    journalctl -u "$SERVICE" -f --output=cat
    ;;
  log100)
    journalctl -u "$SERVICE" -n 100 --no-pager --output=cat
    ;;
  setup)
    systemctl stop "$SERVICE"
    cd -- "$BOT_DIR"
    export TERM=xterm-256color
    "$PYTHON" -c '
import colorama.ansitowin32 as ansi
ansi.AnsiToWin32.convert_osc = lambda self, text: text
import colorama
colorama.init()
from utils import configure_config
configure_config()
'
    if is_configured; then
      systemctl start "$SERVICE"
      printf 'Настройка завершена. Логи: pluniversal log\n'
    else
      printf 'Настройка не заполнена. Повторите pluniversal setup\n' >&2
      exit 1
    fi
    ;;
  update)
    shift
    exec "$PYTHON" "$BOT_DIR/deploy.py" update "$@"
    ;;
  update-setup)
    shift
    exec "$PYTHON" "$BOT_DIR/deploy.py" setup "$@"
    ;;
  update-status)
    "$PYTHON" "$BOT_DIR/deploy.py" status
    systemctl status playerokuniversal-update.timer --no-pager
    ;;
  update-disable)
    systemctl disable --now playerokuniversal-update.timer
    ;;
  *)
    printf 'Playerok Universal — %s\n\n' "$BOT_DIR"
    printf 'pluniversal setup   — настройка бота\n'
    printf 'pluniversal start   — запуск\n'
    printf 'pluniversal stop    — остановка\n'
    printf 'pluniversal restart — перезапуск\n'
    printf 'pluniversal status  — состояние сервиса\n'
    printf 'pluniversal log     — журнал\n'
    printf 'pluniversal log100  — последние 100 строк\n'
    printf 'pluniversal enable  — включить автозапуск\n'
    printf 'pluniversal disable — выключить автозапуск\n'
    printf 'pluniversal update-setup --repo https://github.com/OWNER/REPO.git — включить автообновление\n'
    printf 'pluniversal update — установить обновление сейчас\n'
    printf 'pluniversal update-status — источник и состояние автообновления\n'
    printf 'pluniversal update-disable — выключить автообновление\n'
    ;;
esac
BOTCMD
  } > "$COMMAND_FILE"
  chmod 755 "$COMMAND_FILE"
}

main() {
  local service_only=false no_start=false
  while (($#)); do
    case "$1" in
      --service-only) service_only=true ;;
      --no-start) no_start=true ;;
      -h | --help)
        printf 'Установка из папки бота: sudo bash install.sh [--service-only] [--no-start]\n'
        printf -- '--service-only: использовать готовое venv без установки зависимостей.\n'
        printf -- '--no-start: создать сервис и автозапуск без запуска бота сейчас.\n'
        return ;;
      *) error "Неизвестный параметр: $1" ;;
    esac
    shift
  done
  [[ $EUID -eq 0 ]] || error "Запустите от root: sudo bash install.sh"
  command -v systemctl >/dev/null 2>&1 || error "Для установки нужен systemd."
  [[ -f "$BOT_DIR/bot.py" && -f "$BOT_DIR/requirements.txt" ]] || error "install.sh должен находиться в папке с bot.py и requirements.txt."
  info "Папка бота: $BOT_DIR"
  if [[ "$service_only" == false ]]; then
    install_dependencies
  else
    [[ -x "$VENV_PYTHON" ]] || error "Виртуальное окружение не найдено. Запустите sudo bash install.sh без --service-only."
  fi
  write_launcher
  write_service
  write_controller
  systemctl daemon-reload
  systemctl enable "$BOT_NAME"
  info "Сервис ${BOT_NAME}.service создан для папки $BOT_DIR."
  if [[ "$no_start" == true ]]; then
    info "Для запуска: pluniversal start"
  elif is_configured; then
    systemctl restart "$BOT_NAME"
    info "Бот запущен. Проверка: pluniversal status; журнал: pluniversal log"
  else
    info "Для первоначальной настройки: pluniversal setup"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
