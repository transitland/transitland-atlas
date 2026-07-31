# Feed source evaluations (experimental)

**This directory is an experiment and is not part of the DMFR specification.** Nothing here affects how feeds are fetched or imported. It is a sidecar to `feeds/`, not a replacement for anything in it.

## What this is for

Deciding which URL to register for an agency often takes real work — comparing a candidate against what we already have, checking whether realtime trip IDs resolve, reading an agency's own route list, working out that two vendors each serve half a system. Today that reasoning survives only in commit messages and pull request descriptions, so the next person handed the same URL starts from nothing.

These files record the conclusion and the reason, so a URL that was evaluated once does not have to be evaluated again from scratch.

Both outcomes matter. A finding that a URL **should not** be used is often more valuable than one that should, because nothing else in the registry can express it.

## What this is not for

**Anything derived from feed contents or fetches belongs in the Transitland API, not here.** Route counts, trip counts, calendar spans, fetch history, validation reports and realtime status all change daily or hourly and are already served by the platform, far fresher than a file in git could be.

The test:

> If a field would need updating on a fetch cadence, it belongs in the Transitland API, not here.

Figures do appear in `rationale` text, as frozen evidence for a decision. They are not maintained and should not be read as current.

## Shape

One file per operator, named for its Onestop ID: `evaluations/o-dh-lakeland.json`. Validated by [`schema.json`](schema.json).

Two kinds of record:

- **`candidates`** — URLs that have been evaluated, and what was decided. `decision` is one of `used`, `not_used`, `deferred` or `unavailable`.
- **`watch`** — pages worth re-checking, usually because they publish a URL that moves. This is the counterpart to `tags.unstable_url` in a DMFR file: the tag marks a URL as volatile, and a `watch` entry records where its replacement will appear.

## Current state, not a log

Each file holds what we currently believe. Re-checking a candidate overwrites its entry rather than appending.

Git is the log. `git log --follow evaluations/o-dh-lakeland.json` recovers every revision with author and date, and GitHub resolves each commit to its pull request, so PR numbers are not recorded here — that would duplicate a derivable link and require editing a file after opening the PR that contains it. Use `references` for links known in advance: agency pages, vendor directories, issues.

## Keyed by operator

Operator rather than feed or DMFR filename, because:

- A candidate URL frequently spans several feeds but rarely several operators. A whole-system feed may correspond to two registered feeds for one agency.
- Operator Onestop IDs are more stable than feed IDs, which get superseded.
- One file per operator keeps concurrent edits from colliding.

## Decisions decay

`decided_on` is required. Every finding here rests on something that can change — a calendar that expires, a vendor contract, a URL that moves. `recheck_after` marks the date beyond which a decision's basis may no longer hold, which is what makes `deferred` useful: a rejection with an expiry is far more actionable than a permanent no.

## Validation

```sh
cd scripts && uv run validate-evaluations.py
```

Errors exit non-zero; warnings and notes are advisory. It checks schema conformance, that the filename matches `operator_onestop_id`, that the operator and every `relates_to` / `publishes` feed exists in `feeds/`, that dates are sane and `recheck_after` follows `decided_on`, and that no URL is duplicated within a file. It also flags contradictions in both directions: a candidate marked `not_used` that this operator actually uses, or one marked `used` that no feed of this operator uses.

That contradiction check is scoped **per operator**, because a decision here is about one agency and the same URL may legitimately be another agency's registered feed. When that happens it is reported as a note, not an error.

Two advisory outputs are the point of running it regularly:

- **candidates due for recheck** — anything whose `recheck_after` has passed
- **operators with `unstable_url` feeds but no `watch` entry** — a work list for recording where a moving URL gets republished

The script is deliberately offline. It never contacts the Transitland API or fetches a candidate URL, so it stays deterministic and a feed that happens to be erroring cannot fail it. Re-checking whether a recorded finding still holds is a different job, and one that would want `$TRANSITLAND_API_KEY`.

## Public repository

Rationale text is public. Phrase it as measurement rather than judgment — "trip IDs resolved 0 of 48 against the registered realtime", not an opinion about a vendor or an agency's data quality.
