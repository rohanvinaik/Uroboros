Disc title durations: a title classed by the length of its printed duration.

- `title_kind(text)`: the kind of a disc title by its printed duration: 'extra' under two minutes, 'episode' under eighty minutes, 'film' from eighty minutes on. A printed duration is 'H:MM:SS' or 'MM:SS', read as a whole number of seconds: each field is a whole number of digits, and minutes and seconds are each below 60. A text that is not such a duration is refused with ValueError.
