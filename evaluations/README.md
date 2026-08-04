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

One file per subject, named for its Onestop ID: `evaluations/o-dh-lakeland.json`. Validated by [`schema.json`](schema.json).

Key on the **operator** where one exists. Where the feed has no operator record, key on the **feed** instead: `evaluations/f-move~stanislaus~vetsvan~flex.json`. That is not an edge case, because operator records are only created when there is something to put on them, so most feeds rely on generated operators and a finding about one would otherwise have nowhere to live. The validator rejects a feed-keyed file whose feed does have operator records, and names them.

A file holds one kind of record: **`candidates`**, URLs that have been evaluated and not registered. Each carries `url`, `decision`, `decided_on` and `rationale`, optionally `relates_to` and `references`. That is the whole format.

`decision` is `not_used`, `deferred` or `unavailable`. All three suppress a discovery source identically — the distinction is for a human skimming the file. There is no `used`: the registry is what says a URL is in use, and a file claiming otherwise would just be a second source of truth to keep in sync.

## Record the decision, not the investigation

The temptation is to write down everything that was measured on the way to a decision: file dates, byte counts, route and stop totals, every URL probed. Resist it.

Most of those figures are cheaper to re-measure than to trust, and they decay silently. Worse, the next person to look at the same question will usually look **wider** than we did, because they will have a reason to, and their fuller picture may support a different answer. A rationale packed with our partial evidence invites them to argue with our working rather than form their own view.

So: a few sentences on why the decision holds, and stop. Quote a figure only where it *is* the reason, such as a calendar that had already run out. `relates_to` is worth filling in only when a comparison was actually made, not as a guess to complete the record. The schema caps rationale length to keep this honest.

What genuinely belongs here is the part that is expensive to rediscover: that a URL was looked at and rejected, and roughly why. Not the audit trail.

**Record the constraint, not only the verdict.** This is the one place to err towards saying more. A rationale that gives the conclusion without the thing that forced it reads as settled, and invites someone to reverse it on evidence that was never in dispute. Victor Valley is the worked example: an entry said its realtime was keyed to a superseded static, all true, and omitted that the agency had asked for the feed we register. Read back a day later that looked like an open question, and nearly produced a switch against the agency's stated wish. Where a decision rests on something not examined, say that too.

## Current state, not a log

Each file holds what we currently believe. Re-checking a candidate overwrites its entry rather than appending.

Git is the log. `git log --follow evaluations/o-dh-lakeland.json` recovers every revision with author and date, and GitHub resolves each commit to its pull request, so PR numbers are not recorded here — that would duplicate a derivable link and require editing a file after opening the PR that contains it. Use `references` for links known in advance: agency pages, vendor directories, issues.

## Keyed by operator where possible

Operator rather than DMFR filename, because:

- A candidate URL frequently spans several feeds but rarely several operators. A whole-system feed may correspond to two registered feeds for one agency.
- Operator Onestop IDs are more stable than feed IDs, which get superseded.
- One file per operator keeps concurrent edits from colliding.

## Decisions decay

`decided_on` is required. Every finding here rests on something that can change — a calendar that expires, a vendor contract, a URL that moves.

There is deliberately no scheduled recheck. An earlier version carried `recheck_after`, and in practice it was attached almost entirely to findings of the form "the endpoint was quiet when probed", which is cheaper to re-measure than to trust and produced a nag list nobody acted on. If a decision needs revisiting on a date, that belongs to whatever re-probes; a file in git is the wrong place to schedule work.

## Validation

```sh
cd scripts && uv run validate-evaluations.py
```

Errors exit non-zero; warnings and notes are advisory. It checks schema conformance, that the filename matches its subject, that the operator and every `relates_to` feed exists in `feeds/`, that dates are sane, and that no URL is duplicated within a file. It also flags the one contradiction worth catching: a candidate recorded here that this operator is actually using.

That check is scoped **per operator**, because a decision here is about one agency and the same URL may legitimately be another agency's registered feed. When that happens it is reported as a note, not an error.

The script is deliberately offline. It never contacts the Transitland API or fetches a candidate URL, so it stays deterministic and a feed that happens to be erroring cannot fail it. Re-checking whether a recorded finding still holds is a different job, and one that would want `$TRANSITLAND_API_KEY`.

## Does an entry earn its place?

These files are only worth keeping if they stay dense. Three questions, and an entry failing all three should be deleted:

1. **Would a contributor plausibly propose this URL again?**
2. **Did it take real work to establish**, such that re-deriving it would cost?
3. **Does it suppress a finding a discovery source will keep re-reporting?**

Judge the third by checking, not by intuition. Reviewing the Caltrans entries, two looked like obvious deletions: obscure dial-a-ride files, cheap to re-check, nobody would propose them. But Atlas records those feeds against their **old** `gtfs.calitp.org` URLs, while Cal-ITP now publishes them on `gtfs.dds.dot.ca.gov`. A cross-reference against Cal-ITP compares URLs, so it would flag all four as unknown candidates on every single run. They earn their place on question 3 alone.

The reverse case is worth watching for too: an entry can be interesting and still not worth keeping, if nothing will ever surface the URL again.

## First: can the URL go in the feed record instead?

**If a candidate URL is a genuine alternate for a feed we already hold, it belongs in `feeds/`, not here.** Put it in that feed's `static_historic` and the job is done: the registry then answers the question directly, no sidecar entry is needed, and any discovery source comparing URLs stops reporting it. Atlas already uses `static_historic` this way for URLs that are still live but are not the one we fetch.

That is the cheaper half of the problem, and it covers more cases than it first appears. When the NTD weblinks source was first run, 12 Minnesota agencies were reported because NTD declares the state DOT's mirror while Atlas points at the producer. Recording those 12 URLs as `static_historic` removed all 12 findings, with no evaluation files at all.

**These files are for the other half: alternates we considered and rejected.** A URL that is not a valid alternate for any feed has nowhere to live in `feeds/` — a landing page, a feed for a different agency, an empty vendor tenant, an agency that turned out to have no GTFS at all. That reasoning is what a contributor or an automated research pass would otherwise re-derive from scratch when the same URL is proposed again.

So the test is:

> Does this URL serve a feed we hold? Then `static_historic`. Did we look at it and decide against it? Then an evaluation.

## Subjects with no Atlas record

A discovery source proposes candidates for agencies we do not hold, and a rejection then has no operator or feed to key on. For those, key on an external identifier instead — currently `us_ntd_id`, in a file named `us-ntd-<id>.json`, with a `name` so the file is readable without a lookup.

The validator rejects an externally-keyed file once an Atlas operator carries that id, because the finding then belongs on the operator and the file should be renamed. That makes the external key a staging area rather than a parallel namespace.

## Two kinds of source, and what suppression means for each

`scripts/scan-feed-sources.py` reports findings from several sources, and they do not behave the same way.

**State monitors** — `unstable_url` feeds, stale feeds, failing fetches. These describe the current condition of feeds already registered. A flagged feed stays flagged until someone fixes it, and the count goes down by fixing things, not by recording them. Nothing belongs here for those: the Transitland API reports current state on every run, fresher than a file in git could be. Pages worth re-checking because they republish a moving URL are configured in [`scripts/watch-pages.json`](../scripts/watch-pages.json) instead, since `last_checked` has to be maintained on a cadence and that is exactly what disqualifies a field from this directory.

**Discovery sources** — NTD weblinks, vendor directories, other registries. These propose candidates we may or may not want. The same candidate reappears on every run indefinitely unless a decision is recorded against it, so suppression is the point: the report converges toward zero as evaluations accumulate, even if no feed ever changes. This is where these files pay for themselves.

So suppression is the default for discovery and the exception for monitoring. The runner should always print how many findings were suppressed and why, rather than dropping them silently — a tool that quietly stops reporting real problems is worse than a noisy one.

### What the NTD pass measured about suppression

Suppression is worth less than it first looks, and worth it for a different reason than expected.

Comparing the 2023 reference release against the live one: **98% of NTD ids persist (226 of 230) but only 43% of URLs do (99 of 230)**. An agency stays an agency; its declared URL changes constantly. So a decision recorded against a URL alone expires more often than it holds, and the finding returns anyway. Suppression therefore keys on the **subject** where a source shares one with the registry, and treats the URL as the thing whose change re-opens the question.

The corollary is that the value here is not really deduplication. It is that when a URL *does* come back, the recorded reason is still there.

Two things reduce noise more cheaply than any evaluation file, and should be tried first:

- **Normalising URLs before comparing.** Scheme, host case, trailing slash and path case are all inconsistent between what agencies declare and what Atlas records. Folding them moved 77 agencies out of the unmatched bucket in one step.
- **Following redirects.** Agencies sometimes declare a click-tracking link from a vendor's notification email rather than the feed URL. Two such links resolved to perfectly good feeds, and 4 more resolved onto URLs Atlas already had.

## Public repository

Rationale text is public. Phrase it as measurement rather than judgment — "trip IDs resolved 0 of 48 against the registered realtime", not an opinion about a vendor or an agency's data quality.
