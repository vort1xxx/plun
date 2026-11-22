"""Read-only discovery of settings entry points registered by modules."""
import sys

from aiogram.filters import Command

SETTINGS_COMMANDS = {'playerok_deals_report_module': 'deals_data'}


def routers_for(module):
    seen = set()
    def walk(router):
        if id(router) in seen:
            return
        seen.add(id(router))
        yield router
        for child in getattr(router, 'sub_routers', ()):
            yield from walk(child)
    for router in module.telegram_bot_routers:
        yield from walk(router)


def settings_command(module):
    entries = []
    for router in routers_for(module):
        for handler in router.message.handlers:
            for filter_object in handler.filters or ():
                command_filter = filter_object.callback
                if not isinstance(command_filter, Command):
                    continue
                for command in command_filter.commands:
                    if isinstance(command, str) and command and command_filter.prefix == '/':
                        entries.append((command, handler.callback.__name__))
    commands = {command for command, _ in entries}
    imported = sys.modules.get('modules.' + module._dir_name)
    explicit = getattr(imported, 'MODULE_SETTINGS_COMMAND', None)
    preferred = explicit or SETTINGS_COMMANDS.get(module._dir_name) or module.meta.name
    if preferred in commands:
        return preferred
    # Avoid invoking arbitrary operational commands (imports, purchases, etc.).
    for command, handler in entries:
        if handler in ('menu', 'command_menu', 'settings_command', 'data_menu', 'settings_menu'):
            return command
    return None


def module_page(modules, page, per_page=12):
    pages = max(1, (len(modules) + per_page - 1) // per_page)
    page = min(max(0, page), pages - 1)
    return modules[page * per_page:(page + 1) * per_page], page, pages
