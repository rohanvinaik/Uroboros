You choose test INPUTS for one Python function by SELECTING values. You never write
code, and you never invent structure.

Signature:
    {signature}

The function's source (read its `if`, comparison and `raise` guards — the values it
tests against are your best choices):
{source}

Requirement to satisfy (at least one call must reach it; reason backward from it to
the inputs):
{focus}

PREFER the offered values for each parameter; they are the boundary values the
branches test. Only use a value outside them when no offered value can satisfy the
requirement.
