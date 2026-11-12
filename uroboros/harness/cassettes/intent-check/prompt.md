You hold admission for one Python function. Detective, which measures what code does and never what it is for, pinned
what the function does: each record below is one call and what it gave. What the function is for is what its contract
states: its docstring, and its intent tests with their coverage lines. You check each record against that, and only
against that.

The function: {qualname}

Its docstring: {doc}

Its intent tests:

{tests}

Their coverage lines:
{covers}

The records Detective pinned:
{records}

For each record, give one verdict, by the record's number:
- "agrees": what the call gave is what the intent tests or the docstring state, or follows from them;
- "contradicts:<name>": what the call gave contradicts the intent test or coverage line named;
- "cannot_tell": neither the intent tests nor the docstring settle what the call should give.
