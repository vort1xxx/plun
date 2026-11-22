"""Compatibility for old modules; deploy.py and systemd manage updates."""
latest_release = None


def check_for_updates():
    return None


async def check_new_releases_task(interval=180):
    return None


def download_update(release_info):
    raise RuntimeError('Настройте GitHub-репозиторий и выполните sudo pluniversal update на сервере.')


def install_update(release_info, content):
    raise RuntimeError('Настройте GitHub-репозиторий и выполните sudo pluniversal update на сервере.')
