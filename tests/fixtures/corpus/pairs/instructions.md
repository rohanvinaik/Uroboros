Settings written as key = value lines.

- `read_settings(text)`: a text of such lines read as a mapping from key to value. Each line is split at its first '=', each side stripped of surrounding spaces; a line without '=', or with nothing before it, is refused with ValueError. Blank lines and lines whose first character other than a space is '#' are skipped. A key given twice is refused with ValueError.
