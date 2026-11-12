A number held to a range.

- `clamp(value, low, high)`: the value held between the low and the high bound: the value itself when it lies within them, the low bound when it is below, the high bound when it is above. A low bound above the high one is refused with ValueError.
