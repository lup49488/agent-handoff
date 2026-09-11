## Source progress

- Reproduced the failures with `python acceptance.py`.
- Confirmed the function is public API; retain the `slugify(title)` signature.

## Next step

Normalize accented Latin characters before extracting words. Do not solve this
by adding a third-party dependency.
