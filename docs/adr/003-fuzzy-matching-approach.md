# ADR 003 — Levenshtein + token similarity for fuzzy column matching

## Context

Real-world files use inconsistent column naming conventions. Exact-match
comparison misses the most common real-world schema drift (`CustomerID` vs
`customer_id` vs `cust_id`).

## Decision

Fuzzy matching combines a Levenshtein ratio (on normalized, lowercased,
non-alphanumeric-stripped names) with a token-level similarity score (names
split on underscores/camelCase boundaries). The combined score must be
≥ 0.80 (configurable via `--fuzzy-threshold`) to be considered a candidate
match. Candidates are resolved via greedy bipartite matching (highest score
first; a column claimed on either side can't be reused).

Implementation note: the token-similarity term uses Levenshtein-tolerant
token matching (not strict token equality), since strict Jaccard similarity
alone scores `CustomerID` vs `cust_id` below threshold (`cust` ≠ `customer`
as literal tokens). This is what makes the semantic layer satisfy this ADR's
own example.

## Tradeoff

Fuzzy matching can produce false positives for short column names. Mitigated
by always presenting matches as `suggestion` severity — never auto-mapped
without human confirmation.

## Status

Implemented. See `adv_data_comp/layers/semantic_layer.py`.
