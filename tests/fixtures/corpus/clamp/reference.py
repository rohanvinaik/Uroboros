def clamp(value, low, high):
    if low > high:
        raise ValueError((low, high))
    if value < low:
        return low
    if value > high:
        return high
    return value
