from django import template

register = template.Library()


@register.filter(name="get_item")
def get_item(dictionary, key):
    """
    Get item from dictionary by key
    Usage: {{ dict|get_item:key }}
    """
    if dictionary is None:
        return None
    return dictionary.get(key, "未知项目")


@register.filter(name="mul")
def mul(value, arg):
    """
    Multiply value by arg
    Usage: {{ value|mul:5 }}
    """
    try:
        return int(value) * int(arg)
    except (ValueError, TypeError):
        return 0


@register.filter(name="add")
def add_filter(value, arg):
    """
    Add arg to value (override default add filter if needed)
    Usage: {{ value|add:5 }}
    """
    try:
        return int(value) + int(arg)
    except (ValueError, TypeError):
        try:
            return float(value) + float(arg)
        except (ValueError, TypeError):
            return value
