import string


class SafeFormatter(string.Formatter):
    """
    We skip missing {args} instead of raising KeyValue and return placeholder.
    """
    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            if key in kwargs:
                return kwargs[key]
            return "{" + key + "}"
        return super().get_value(key, args, kwargs)


def stringify(template: str, /, **kwargs) -> str:
    """
    Used to safely format string, skip missing {args} instead of raising KeyValue.
    Returns placeholders inside {} if arg is missing.
    Example: stringify("Hello, {name} {second_name}!", name=Alice, age=18) -> "Hello, Alice {second_name}!"
    """
    return SafeFormatter().vformat(template, args=(), kwargs=kwargs)
