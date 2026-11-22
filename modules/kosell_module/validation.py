from string import Formatter


def valid_template(template: str) -> bool:
    if not isinstance(template, str) or not template:
        return False
    try:
        fields = list(Formatter().parse(template))
        names = {field for _, field, _, _ in fields if field is not None}
        if not {"login", "password"}.issubset(names):
            return False
        if not names.issubset({"game", "login", "password", "hours", "duration"}):
            return False
        if any(spec or conversion for _, field, spec, conversion in fields if field is not None):
            return False
        template.format(game="game", login="login", password="password", hours=1, duration="1 ч.")
        return True
    except (ValueError, KeyError, IndexError, TypeError):
        return False
