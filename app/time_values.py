from datetime import timezone


def utc_millisecond(value):
    value = value.astimezone(timezone.utc)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def iso_deadline(value):
    from datetime import datetime
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('时间必须是带时区的ISO字符串')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('时间格式错误')
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('时间必须带时区')
    try:
        return utc_millisecond(result)
    except (ValueError, OverflowError):
        raise ValueError('时间超出支持范围')
