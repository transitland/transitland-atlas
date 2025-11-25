# French Transport Portal Feed Review

This script reviews French operators and feeds from Transitland, matching them with datasets from transport.data.gouv.fr

## Usage

```bash
# From the workspace root
TRANSITLAND_API_KEY=xxx uv run external-data-for-reference/transport-data-gouv-fr/review-french-feeds.py
```

## Options

- `--match-threshold N`: Fuzzy matching threshold 0-100 (default: 60)
- `--min-age-days N`: Minimum feed age in days to flag as outdated (default: 270)

## Cache

API responses are cached locally in `.cache/` directory (gitignored) to speed up repeated runs

## Output

- Log files: `french-feeds-review_YYYYMMDD_HHMMSS.log`
- DMFR files: Updated in `../../feeds/` directory (workspace root)

