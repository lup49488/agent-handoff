## Source progress

- Added standard-library Unicode normalization and separator collapse.
- `many---spaces` now passes.

## Decision

Punctuation cannot simply be discarded: `&` and `#` carry required words in
the output. Apply their semantic substitutions before ASCII word extraction.

## Next step

Add the substitutions while preserving the public signature and explicit type
validation.
