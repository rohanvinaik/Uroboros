# Contract regression corpus

These modules came from the SquishOS harness qualification corpus. The Uroboros tests reuse their contracts and reference implementations.

| module | functions | what it holds |
|---|---|---|
| `clamp` | `clamp` | one function: comparisons, and a refusal |
| `orders` | `order_total`, combining `line_total` | arithmetic over a list of pairs |
| `durations` | `title_kind`, combining `seconds_of` | text read as numbers; a refusal passed up |
| `pairs` | `read_settings`, combining `read_pair` | text split and read into a mapping; refusals |
| `grades` | `pass_rate`, combining `passes`; the global `PASS_MARK` | a global object read below |

Each folder contains a seated `contract.json`, a Python reference implementation, and the prose `instructions.md` that describes the public behavior.
