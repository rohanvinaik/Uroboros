def read_pair(line):
    key, sign, value = line.partition("=")
    key = key.strip()
    if not sign or not key:
        raise ValueError(line)
    return key, value.strip()


def read_settings(text):
    settings = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, value = read_pair(line)
        if key in settings:
            raise ValueError(key)
        settings[key] = value
    return settings
