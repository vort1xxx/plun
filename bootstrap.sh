#!/usr/bin/env bash
# Install from GitHub on a server without a local copy of the project.
set -euo pipefail

BOOTSTRAP_REPO="https://github.com/vort1xxx/plun.git"
BOOTSTRAP_BRANCH="main"
BOOTSTRAP_DIR="/opt/playerokuniversal"
BOOTSTRAP_TMP=""
BOOTSTRAP_TEMP_ROOT=""
BOOTSTRAP_SETUP=true
BOOTSTRAP_CONTROLLER="/usr/local/bin/pluniversal"

bootstrap_info() { printf '\n%s\n' "$*"; }
bootstrap_error() { printf '\nОшибка: %s\n' "$*" >&2; exit 1; }

bootstrap_cleanup() {
  if [[ -n "$BOOTSTRAP_TMP" && -d "$BOOTSTRAP_TMP" ]]; then
    local resolved
    resolved="$(cd -- "$BOOTSTRAP_TMP" && pwd -P)"
    # Only remove the private staging directory created by this script.
    if [[ "$resolved" == "$BOOTSTRAP_TEMP_ROOT"/plun-bootstrap.* ]]; then
      rm -rf -- "$resolved"
    fi
  fi
}

bootstrap_require_root() {
  [[ $EUID -eq 0 ]] || bootstrap_error "Запустите установщик через sudo bash."
}

bootstrap_dependencies() {
  command -v apt-get >/dev/null 2>&1 || bootstrap_error "Установщик рассчитан на Ubuntu с apt-get."
  command -v systemctl >/dev/null 2>&1 || bootstrap_error "Для установки нужен systemd."
  if ! command -v git >/dev/null 2>&1 || ! command -v flock >/dev/null 2>&1; then
    apt-get update
    apt-get install -y git ca-certificates util-linux
  fi
}

bootstrap_lock() {
  exec 9>/run/playerokuniversal-bootstrap.lock
  flock -n 9 || bootstrap_error "Установка уже выполняется."
}

bootstrap_download() {
  [[ "$BOOTSTRAP_REPO" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] ||
    bootstrap_error "Нужен HTTPS-адрес репозитория GitHub."
  git check-ref-format --branch "$BOOTSTRAP_BRANCH" >/dev/null
  [[ "$BOOTSTRAP_DIR" == /* && "$BOOTSTRAP_DIR" != / && "$BOOTSTRAP_DIR" != *$'\n'* && "$BOOTSTRAP_DIR" != *$'\r'* ]] ||
    bootstrap_error "Папка установки должна быть абсолютным путём."

  local existing
  existing="$(systemctl show playerokuniversal --property=WorkingDirectory --value 2>/dev/null || true)"
  [[ -z "$existing" ]] || bootstrap_error "Уже существует установка в $existing. Для обновления используйте sudo pluniversal update."
  [[ ! -e "$BOOTSTRAP_DIR" && ! -L "$BOOTSTRAP_DIR" ]] ||
    bootstrap_error "Папка $BOOTSTRAP_DIR уже существует. Выберите новую через --dir; существующие данные сохранены."

  BOOTSTRAP_TEMP_ROOT="$(cd -- "${TMPDIR:-/tmp}" && pwd -P)"
  BOOTSTRAP_TMP="$(mktemp -d "$BOOTSTRAP_TEMP_ROOT/plun-bootstrap.XXXXXXXX")"
  trap bootstrap_cleanup EXIT
  bootstrap_info "Скачиваю $BOOTSTRAP_REPO, ветка $BOOTSTRAP_BRANCH..."
  git clone --depth 1 --branch "$BOOTSTRAP_BRANCH" -- "$BOOTSTRAP_REPO" "$BOOTSTRAP_TMP/source"
  local required
  for required in bot.py install.sh deploy.py requirements.txt __init__.py utils.py settings.py data.py; do
    [[ -f "$BOOTSTRAP_TMP/source/$required" ]] ||
      bootstrap_error "В корне репозитория отсутствует $required. Загрузите полный проект на GitHub."
  done
  for required in core playerokapi plbot tgbot; do
    [[ -d "$BOOTSTRAP_TMP/source/$required" ]] ||
      bootstrap_error "В репозитории отсутствует папка $required. Загрузите полный проект на GitHub."
  done
  mkdir -p -- "$(dirname -- "$BOOTSTRAP_DIR")"
  mv -- "$BOOTSTRAP_TMP/source" "$BOOTSTRAP_DIR"
}

bootstrap_configure() {
  if [[ "$BOOTSTRAP_SETUP" == false ]]; then
    bootstrap_info "Установка завершена. Для настройки и запуска: sudo pluniversal setup"
    return
  fi
  # A piped installer uses stdin for its code. Read credentials from the terminal.
  if { true < /dev/tty; } 2>/dev/null; then
    "$BOOTSTRAP_CONTROLLER" setup < /dev/tty
  else
    bootstrap_info "Проект установлен. Откройте SSH-терминал и выполните sudo pluniversal setup."
  fi
}

bootstrap_main() {
  while (($#)); do
    case "$1" in
      --dir | --repo | --branch)
        [[ $# -ge 2 ]] || bootstrap_error "После $1 требуется значение."
        case "$1" in
          --dir) BOOTSTRAP_DIR="$2" ;;
          --repo) BOOTSTRAP_REPO="$2" ;;
          --branch) BOOTSTRAP_BRANCH="$2" ;;
        esac
        shift 2 ;;
      --no-setup) BOOTSTRAP_SETUP=false; shift ;;
      -h | --help)
        printf 'Установка из GitHub: bootstrap.sh [--dir /opt/playerokuniversal] [--branch main] [--repo URL] [--no-setup]\n'
        return ;;
      *) bootstrap_error "Неизвестный параметр: $1" ;;
    esac
  done
  bootstrap_require_root
  bootstrap_dependencies
  # Serialize bootstrap runs, including ones started for different target folders.
  bootstrap_lock
  bootstrap_download
  bash "$BOOTSTRAP_DIR/install.sh" --no-start
  "$BOOTSTRAP_CONTROLLER" update-setup --repo "$BOOTSTRAP_REPO" --branch "$BOOTSTRAP_BRANCH" --no-enable
  # Record the installed version while the bot and update timer are still stopped.
  "$BOOTSTRAP_CONTROLLER" update
  systemctl enable --now playerokuniversal-update.timer
  bootstrap_configure
  bootstrap_info "Файлы проекта: $BOOTSTRAP_DIR. Автообновление проверяет GitHub каждые 5 минут."
}

# Also run when this file is read from stdin (curl ... | sudo bash).
if [[ "${BASH_SOURCE[0]:-}" == "$0" || -z "${BASH_SOURCE[0]:-}" ]]; then
  bootstrap_main "$@"
fi
