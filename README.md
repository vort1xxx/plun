# Playerok Universal
[![python](https://img.shields.io/badge/python-3.12.x-yellow?style=for-the-badge&logo=python&link=https%3A%2F%2Fimg.shields.io%2Fbadge%2Ftelegram-%25D0%25BA%25D0%25B0%25D0%25BD%25D0%25B0%25D0%25BB-blue%3Fstyle%3Dfor-the-badge%26logo%3Dtelegram)](https://www.python.org/downloads/release/python-3119/)

Многофункциональный бот-помощник для Playerok 🤖🟦

---

## 🗺️ Навигация
- **[Функционал](#-функционал)**
- **[Установка](#%EF%B8%8F-установка)**
  - **[Windows](#-windows)**
  - **[macOS](#-macos)**
  - **[Linux](#%EF%B8%8F-linux-ubuntu)**
  - **[🌐 Cloudflare Workers](#-блокировка-telegram-используйте-cloudflare-workers)**
- **[Для разработчиков](#-для-разработчиков)**

## ⚙️ Функционал

### 🤖 Полное управление в Telegram боте
- **🎪 Удобное и понятное меню**
- **💬 Просмотр чатов** (отправка, чтение сообщений...)
- **🖼️ Отправка изображений** в чаты
- **🌟 Просмотр отзывов** (с фильтрами)
- **📋 Управление сделками** (возвраты, проблемы, подтверждения...)
- **🛍️ Управление товарами** (публикация, поднятие...)
- **🛠️ Настройка конфигов**
- **📊 Просмотр статистики** (за 24 часа, неделю, месяц и всё время)
- **👤 Просмотр профиля** (вся подробнейшая информация)
- **🗒️ Просмотр логов** из консоли
- **🔔 Уведомления о событиях** (новые сообщения, продажи...)
- **⚡ Быстрые ответы** (сообщения по нажатию одной кнопки)
- и многое другое...

### ✨ Широкие возможности
- **🧩 Система модулей** (плагины, подключаемые к боту)
- **🟢 Вечный онлайн на сайте**
- **🎈 Сообщения на события** (приветственное, новая продажа...)
- **❗ Собственные команды**
- **🚀 Авто-выдача товаров** (поштучная и сообщением)
- **⬆️ Авто-поднятие товаров**
- **♻️ Восстановление товаров** (проданные и истёкшие)
- **☑️ Авто-подтверждение сделок**
- **🔗 Привязка товаров по ссылке** (вместо ключевых фраз)
- **💸 Авто-вывод средств** 
- **🌐 Подключение прокси** 
- и многое другое...

## ⬇️ Установка

### 🔷 Windows
1. Установите **Python версии 3.12.x** (на других версиях работа бота не гарантируется), перейдя по ссылке https://www.python.org/downloads/release/python-31210/ (при установке нажмите на пункт `Add to PATH`).
2. Скачайте [последнюю Release версию](https://github.com/alleexxeeyy/playerok-universal/releases/latest) и распакуйте в любое удобное для вас место.
3. Откройте `install.bat` и дождитесь установки всех необходимых зависимостей, а после закройте окно.
4. Чтобы запустить бота, откройте запускатор `start.bat`.
5. После первого запуска следуйте действиям в консоли.

[Возникли проблемы с установкой? Нажми на меня](https://telegra.ph/FunPay-Universal--chastye-oshibki-i-ih-resheniya-08-26)

### 🍎 macOS
1. Установите **Python версии 3.12.x** с [официального сайта](https://www.python.org/downloads/release/python-31210/) или через Homebrew:
   ```bash
   brew install python@3.12
   ```
2. Скачайте [последнюю Release версию](https://github.com/alleexxeeyy/funpay-universal/releases/latest) и распакуйте в любое удобное место.
3. Дважды кликните `install_mac.command` — откроется Terminal, дождитесь установки зависимостей.
4. Запустите бота двойным кликом по `start_mac.command`.
5. Следуйте инструкциям в консоли.

### ♨️ Linux (Ubuntu 24)
1. Выполните команду

```bash
sudo bash install.sh
```
2. Следуйте инструкциям установщика. Он автоматически установит все необходимые для работы компоненты.

**🕹️ Команды для управления**

| Команда               | Что делает       |
|-----------------------|-------------------------------------|
| `pluniversal`         | **🕹️ Меню с командами**             |
| `pluniversal setup`   | **⚙️ Первичная настройка**          |
| `pluniversal start`   | **🟢 Запуск бота**                  |
| `pluniversal stop`    | **⛔ Остановка бота**               |
| `pluniversal restart` | **🔄️ Перезапуск бота**              |
| `pluniversal status`  | **📈 Статус бота**                  |
| `pluniversal log`     | **🗒️ Логи бота**         |
| `pluniversal log100`  | **📃 Последние 100 логов**          |
| `pluniversal update`  | **🔵 Обновление бота**              | 
| `pluniversal enable`  | **☑️ Включить авто-запуск бота**  |
| `pluniversal disable` | **❌ Выключить авто-запуск бота** |

## Обновление из GitHub-репозитория

После настройки сервер проверяет выбранную ветку каждые 5 минут. Новые commits
устанавливаются автоматически. Остановленного вручную бота проверка не запускает.
Нужен Linux с systemd; в корне репозитория должны находиться файлы этого проекта,
включая `deploy.py`, `install.sh`, `bot.py` и `requirements.txt`.

Для **публичного репозитория** SSH-ключ и токен не нужны. Загрузите исходники
в корень репозитория, а на сервере один раз перенесите текущий код в папку бота
и выполните из этой папки:

```bash
sudo bash install.sh --service-only --no-start
sudo apt-get install -y git
sudo pluniversal update-setup --repo https://github.com/OWNER/REPO.git --branch main
sudo pluniversal update
```

Подставьте HTTPS-адрес из **Code → HTTPS** и название своей ветки.
Теперь достаточно отправлять обновлённый код в эту ветку на GitHub.
Настройки и данные бота, настройки и данные модулей, логи и секреты не заменяются.
Полная инструкция, команды управления и описание отката — в [DEPLOYMENT.txt](DEPLOYMENT.txt).

Далее приведена дополнительная настройка для **приватного репозитория через SSH**.

1. Создайте на GitHub репозиторий с видимостью **Private** и загрузите исходники.
   Используйте Git с приложенным `.gitignore`: он исключает настройки с токенами,
   данные бота и модулей, логи и виртуальное окружение. При загрузке через сайт
   эти папки исключите вручную: сайт не применяет `.gitignore`.
   SSH-адрес находится в **Code → SSH** и выглядит как
   `git@github.com:OWNER/REPO.git`. В примерах ветка называется `main`.

2. Один раз перенесите текущий код на сервер и обновите установленные команды.
   Выполняйте из папки работающего бота; `--no-start` не перезапускает его:

   ```bash
   sudo bash install.sh --service-only --no-start
   sudo apt-get install -y git openssh-client
   sudo install -d -m 700 /root/.ssh
   sudo ssh-keygen -t ed25519 -f /root/.ssh/playerok_deploy -N '' -C playerok-deploy
   sudo cat /root/.ssh/playerok_deploy.pub
   ```

   Создавайте ключ по этому пути только один раз. Скопируйте **публичный** ключ
   из последней команды в репозиторий: **Settings → Deploy keys → Add deploy key**.
   Параметр **Allow write access** оставьте выключенным.
   [Инструкция GitHub по deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys).

3. Проверьте SSH-доступ от root:

   ```bash
   sudo ssh -i /root/.ssh/playerok_deploy -o IdentitiesOnly=yes -T git@github.com
   ```

   При первом подключении проверьте отпечаток сервера по
   [списку GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
   и подтвердите добавление хоста. Сообщение об успешной авторизации и отсутствии
   shell-доступа означает, что ключ работает; SSH при этом может вернуть код 1.

4. Включите обновления, подставив свой адрес:

   ```bash
   sudo pluniversal update-setup --repo git@github.com:OWNER/REPO.git --branch main
   sudo pluniversal update
   ```

   Если ключ лежит в другом месте, добавьте `--key /абсолютный/путь/к/ключу`.
   Адрес, ветка и путь к ключу сохраняются локально в `.deploy/config.json`.

Теперь достаточно отправлять изменения в эту ветку на GitHub. При обновлении
сохраняются `bot_settings`, `bot_data`, `module_settings`, `module_data`, логи,
локальные секреты и настройки обновления. Неуправляемые локальные файлы и модули
не удаляются; удаляются только файлы, установленные предыдущим обновлением и
затем удалённые из репозитория. Файлы Python проверяются на синтаксис до остановки
бота. Изменённые зависимости готовятся в копии venv до остановки сервиса.

Перед заменой кода создаётся резервная копия в `.deploy/backups/`. После запуска
проверяется, что сервис остаётся активным с одним PID в течение 30 секунд.
Это проверка процесса, а не успешности подключения к Telegram или Playerok.
При ошибке возвращаются предыдущие код и venv; текущие данные не откатываются.
Прерванное обновление восстанавливается при следующем запуске обновляющего скрипта.
Версия, не прошедшая запуск, автоматически не повторяется до следующего commit;
команда `pluniversal update` позволяет повторить её вручную.
Резервные копии автоматически не удаляются.

```bash
sudo pluniversal update-status
sudo journalctl -u playerokuniversal-update.service -n 100 --no-pager
sudo pluniversal update-disable
```

Периодичность задаётся в `/etc/systemd/system/playerokuniversal-update.timer`:
`OnUnitInactiveSec=5min`. После изменения выполните `sudo systemctl daemon-reload`
и `sudo systemctl restart playerokuniversal-update.timer`.

## 🌐 Блокировка Telegram? Используйте Cloudflare Workers

Если Telegram заблокирован в вашем регионе (Россия, Иран и т.д.), бот не сможет подключиться к `api.telegram.org`. Решение — бесплатный прокси-воркер на Cloudflare. Всё настраивается за 2 минуты.

### 🚀 Быстрый старт

1. Зайдите в [Cloudflare Dashboard](https://dash.cloudflare.com/) → **Workers & Pages** → **Create application** → **Create Worker**
2. Назовите воркер, например `tg-proxy`
3. **Полностью удалите** шаблонный код из редактора и вставьте этот:

```javascript
export default {
  async fetch(request) {
    const url = new URL(request.url);
    const tgUrl = `https://api.telegram.org${url.pathname}${url.search}`;
    const headers = new Headers(request.headers);
    headers.set('Host', 'api.telegram.org');
    return fetch(new Request(tgUrl, {
      method: request.method, headers, body: request.body
    }));
  }
}
```

4. Нажмите **Save and Deploy** ✅
5. Скопируйте URL воркера — выглядит так: `https://tg-proxy.ваш-поддомен.workers.dev`

### 🔧 Как использовать в боте

При первом запуске бота, после ввода токена появится  пункт:

```
┌────┤ Введите Кастомный URL Telegram API (опционально) ├────┐
  Если Telegram заблокирован, можно указать URL
  Cloudflare Worker-прокси вместо api.telegram.org
  Или пропустите эту настройку, нажав Enter
  
  · Пример: https://tg-proxy.ваш-поддомен.workers.dev
```

Вставьте URL вашего воркера → **Enter**. Прокси для Telegram можно пропустить (тоже Enter). Готово! 🚀

> **Важно:** URL можно указать и без `https://` — бот сам допишет. Достаточно просто `tg-proxy.ваш-поддомен.workers.dev`.

## 📚 Для разработчиков

Модульная система помогает внедрять в бота дополнительный функционал, сделанный энтузиастами. По сути, это же, что и плагины, но в более удобном формате.
Вы можете создавать свой модуль, опираясь на [шаблонный](.templates/forms_module).

<details>
  <summary><strong>📌 Основные ивенты</strong></summary>

  ### Ивенты бота (BOT_EVENT_HANDLERS)

  Ивенты, которые выполняются при определённом действии бота.

  | Ивент | Когда вызывается | Передающиеся аргументы |
  |-------|------------------|------------------------|
  | `ON_MODULE_ENABLED` | При включении модуля | `Module` |
  | `ON_MODULE_DISABLED` | При выключении модуля | `Module` |
  | `ON_INIT` | При инициализации бота | `-` |
  | `ON_PLAYEROK_BOT_INIT` | При инициализации (запуске) Playerok бота | `PlayerokBot` |
  | `ON_TELEGRAM_BOT_INIT` | При инициализации (запуске) Telegram бота | `TelegramBot` |

  ### Ивенты Playerok (PLAYEROK_EVENT_HANDLERS)

  Ивенты, получаемые в слушателе событий в Playerok боте.

  | Ивент | Когда вызывается | Передающиеся аргументы |
  |-------|------------------|------------------------|
  | `EventTypes.CHAT_INITIALIZED` | Чат инициализирован | `PlayerokBot`, `ChatInitializedEvent` |
  | `EventTypes.NEW_MESSAGE` | Новое сообщение в чате | `PlayerokBot`, `NewMessageEvent` |
  | `EventTypes.NEW_DEAL` | Создана новая сделка (когда покупатель оплатил товар) | `PlayerokBot`, `NewDealEvent` |
  | `EventTypes.NEW_REVIEW` | Новый отзыв по сделке | `PlayerokBot`, `NewReviewEvent` |
  | `EventTypes.DEAL_CONFIRMED` | Сделка подтверждена | `PlayerokBot`, `DealConfirmedEvent` |
  | `EventTypes.DEAL_ROLLED_BACK` | Продавец оформил возврат сделки | `PlayerokBot`, `DealRolledBackEvent` |
  | `EventTypes.DEAL_HAS_PROBLEM` | Пользователь сообщил о проблеме в сделке | `PlayerokBot`, `DealHasProblemEvent` |
  | `EventTypes.DEAL_PROBLEM_RESOLVED` | Проблема в сделке решена | `PlayerokBot`, `DealProblemResolvedEvent` |
  | `EventTypes.DEAL_STATUS_CHANGED` | Статус сделки изменён | `PlayerokBot`, `DealStatusChangedEvent` |
  | `EventTypes.ITEM_PAID` | Пользователь оплатил предмет | `PlayerokBot`, `ItemPaidEvent` |
  | `EventTypes.ITEM_SENT` | Предмет отправлен (продавец подтвердил выполнение сделки) | `PlayerokBot`, `ItemSentEvent` |

</details>

<details>
  <summary><strong>📁 Строение модуля</strong></summary>  
  
  </br>Модуль - это папка, внутри которой находятся важные компоненты. Вы можете изучить строение модуля, опираясь на [шаблонный модуль](.templates/forms_module), но стоит понимать, что это лишь пример, сделанный нами.

  Обязательные константы хендлеров:
  | Константа | Тип | Описание |
  |-----------|-----|----------|
  | `BOT_EVENT_HANDLERS` | `dict[str, list[Any]]` | В этом словаре задаются хендлеры ивентов бота |
  | `PLAYEROK_EVENT_HANDLERS` | `dict[EventTypes, list[Any]` | В этом словаре задаются хендлеры ивентов Playerok |
  | `TELEGRAM_BOT_ROUTERS` | `list[Router]` | В этом массиве задаются роутеры модульного Telegram бота  |

  Обязательные константы метаданных:
  | Константа | Тип | Описание |
  |-----------|-----|----------|
  | `PREFIX` | `str` | Префикс |
  | `VERSION` | `str` | Версия |
  | `NAME` | `str` | Название |
  | `DESCRIPTION` | `str` | Описание |
  | `AUTHORS` | `str` | Авторы |
  | `LINKS` | `str` | Ссылки на авторов |

  Также, если модуль требует дополнительных зависимостей, в нём должен быть файл зависимостей **requirements.txt**, которые будут сами скачиваться при загрузке всех модулей бота.

  #### 🔧 Пример содержимого:
  Обратите внимание, что метаданные были вынесены в отдельный файл `meta.py`, но импортируются в `__init__.py`.
  Это сделано для избежания конфликтов импорта в дальнейшей части кода модуля.

  **`meta.py`**:
  ```python
  from colorama import Fore, Style

  PREFIX = f"{Fore.LIGHTCYAN_EX}[test module]{Fore.WHITE}"
  VERSION = "0.1"
  NAME = "test_module"
  DESCRIPTION = "Тестовый модуль. /test_module в Telegram боте для управления"
  AUTHORS = ""
  LINKS = ""
  ```

  **`__init__.py`**:
  ```python
  from playerokapi.listener.events import EventTypes
  from core.modules_manager import Module, disable_module

  from .plbot.handlers import on_playerok_bot_init, on_new_message, on_new_deal
  from .tgbot import router
  from .tgbot._handlers import on_telegram_bot_init
  from .meta import *
  

  _module: Module = None


  def set_module(module: Module):
      global _module
      _module = module

  def get_module():
      return _module
  
  async def on_module_enabled(module: Module):
      try:
          set_module(module)
          print(f"{PREFIX} Модуль подключен и активен")
      except:
          await disable_module(_module.uuid)
  

  BOT_EVENT_HANDLERS = {
      "ON_MODULE_ENABLED": [on_module_enabled],
      "ON_PLAYEROK_BOT_INIT": [on_playerok_bot_init],
      "ON_TELEGRAM_BOT_INIT": [on_telegram_bot_init]
  }
  PLAYEROK_EVENT_HANDLERS = {
      EventTypes.NEW_MESSAGE: [on_new_message],
      EventTypes.NEW_DEAL: [on_new_deal],
      # ...
  }
  TELEGRAM_BOT_ROUTERS = [router]
  ```

</details>

<details>
  <summary><strong>🛠️ Полезные инструменты</strong></summary>  
  
  ### 📝 Настроенные врапперы файлов конфигурации и файлов данных
  Вместо того, чтобы лишний раз мучаться с файлами конфигурациями, написанием кода для управлениями ими, мы подготовили для вас готовое решение.
  У бота есть уже настроенные классы в файлах [`settings.py`](settings.py) и [`data.py`](data.py)

  #### Как это работает?
  Допустим, вы хотите создать файл конфигурации в своём модуле, для этого вам нужно будет создать файл `settings.py` в корне папки модуля.
  Содержимое `settings.py` должно быть примерно следующим:
  ```python
  import os
  from settings import (
      Settings as sett,
      SettingsFile
  )


  CONFIG = SettingsFile(
      name="config", #  название файла конфигурации
      path=os.path.join(os.path.dirname(__file__), "module_settings", "config.json"), #  путь к файлу конфигурации (в данном случае относительно папки модуля)
      need_restore=True, #  нужно ли восстанавливать конфиг
      default={
          "bool_param": True,
          "str_param": "qwerty",
          "int_param": 123
      } #  стандартное содержимое файла
  )

  DATA = [CONFIG]


  class Settings:
    
      @staticmethod
      def get(name: str) -> dict:
          return sett.get(name, DATA)

      @staticmethod
      def set(name: str, new: list | dict) -> dict:
          return sett.set(name, new, DATA)
  ```

  Файл конфигурации задаётся с помощью датакласса `SettingsFile`, который в свою очередь, передаётся в массив `DATA`.
  
  Далее, получить данные из конфига или сохранить данные в конфиг можно вот так:
  ```python
  from . import settings as sett

  config = sett.get("config") #  получаем конфиг
  print(config["bool_param"]) # -> True
  print(config["str_param"]) #  -> qwerty
  print(config["int_param"]) #  -> 123
  config["bool_param"] = False
  config["str_param"] = "uiop"
  config["int_param"] = 456
  sett.set("config", config) #  задаём конфигу новое значение
  ```

  Задавая конфигу новое значение, оно сразу записывается в его файл. Также и при получении, берутся актуальные данные из файла.

  Описание аргументов датакласса `SettingsFile`:
  | Аргумент | Описание |
  |----------|----------|
  | `name` | Название файла конфигурации, которое будем использовать при получении и записи |
  | `path` | Путь к файлу конфигурации |
  | `need_restore` | Нужно ли восстанавливать конфиг? Допустим, в стандартное значение конфига у вас добавились новые данные, а в уже созданном **ранее** файле конфигурации они отсутствуют. Если параметр включен, скрипт будет сверять текущие данные конфига со стандартными указанными, и если в текущих данных не будет того или иного ключа, который есть в стандартном значении, он автоматически добавится в конфиг. Так же, если тип значения ключа стандартного конфига не соответствует существующему (например, в файле **строковый** тип, а в стандартном значении **числовой**), также этот ключ в текущем конфиге будет заменён на стандартное значение |
  | `default` | Стандартное значение файла конфигурации |


  </br>Точно также устроен и файл данных, но он нужен для хранения информации, собранной самим скриптом, а не указанной пользователей.
  Например, вы хотите создать файл данных в своём модуле, для этого вам нужно будет создать файл `data.py` в корне папки модуля.
  
  Содержимое `data.py` должно быть примерно следующим:
  ```python
  import os
  from data import (
      Data as data,
      DataFile
  )


  LATEST_EVENTS_TIMES = DataFile(
      name="new_forms", #  название файла данных
      path=os.path.join(os.path.dirname(__file__), "module_data", "new_forms.json"), #  путь к файлу данных (в данном случае относительно папки модуля)
      default={} #  стандартное содержимое файла
  )

  DATA = [LATEST_EVENTS_TIMES]


  class Data:

      @staticmethod
      def get(name: str) -> dict:
          return data.get(name, DATA)

      @staticmethod
      def set(name: str, new: list | dict) -> dict:
          return data.set(name, new, DATA)
  ```

  Здесь всё аналогично файлу конфигурации, только служит для другой задачи.


  ### 🔌 Удобное управление состояниями модуля
  Используя методы из `core/modules.py`, можно удобно включать/выключать/перезагружать текущий модуль.
  Для того, чтобы это сделать, нужно прежде всего получить UUID текущего запущенного модуля, который генерируется при его инициализации.
  
  Например, в файле `__init__.py` можно делать так:
  ```python
  # import ...


  _module: Module = None


  async def set_module(module: Module):
      global _module
      _module = module

  def get_module():
      return _module
  

  BOT_EVENT_HANDLERS = {
      "ON_MODULE_ENABLED": [set_module],
      # ...
  }
  # ...
  ```

  А потом в любом удобном месте управлять модулем:
  ```python
  from core.modules import enable_module, disable_module, reload_module

  from . import get_module


  await disable_module(get_module().uuid) #  выключает модуль
  await enable_module(get_module().uuid) #  включает модуль
  await reload_module(get_module().uuid) #  перезагружает модуль
  ```

</details>

<details>
  <summary><strong>❗ Примечания</strong></summary>

  </br>Функционал Telegram бота написан на библиотеке aiogram 3, система внедрения пользовательского функционала Telegram бота работает на основе роутеров, которые сливаются с основным, главным роутером бота.
  И так, как они сливаются воедино, могут возникнуть осложнения, если, например Callback данные имеют идентичное название. Поэтому, после написания функционала Telegram бота для модуля, лучше переименуйте
  эти данные уникальным образом, чтобы они не совпадали с названиями основного бота или дополнительных подключаемых модулей.

</details>


