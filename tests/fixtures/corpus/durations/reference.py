def seconds_of(text):
    fields = text.split(":")
    if len(fields) not in (2, 3) or not all(f.isdigit() for f in fields):
        raise ValueError(text)
    numbers = [int(f) for f in fields]
    hours, minutes, seconds = [0, *numbers] if len(numbers) == 2 else numbers
    if minutes >= 60 or seconds >= 60:
        raise ValueError(text)
    return hours * 3600 + minutes * 60 + seconds


def title_kind(text):
    seconds = seconds_of(text)
    if seconds < 120:
        return "extra"
    if seconds < 4800:
        return "episode"
    return "film"
