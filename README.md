<!-- omit in toc -->
# Transitland Atlas

An open catalog of transit/mobility data feeds and operators.

This catalog is used to power the canonical [Transitland](https://transit.land) platform, is available for distributed used of the [Gotransit](https://github.com/interline-io/gotransit) tooling, and is open to use as a "crosswalk" within other transportation data systems.

**Table of contents**:

<!-- TOC created and updated by VSCode Markdown All in One extension -->
- [Feeds](#feeds)
- [Operators](#operators)
- [How to Add a New Feed](#how-to-add-a-new-feed)
- [Onestop IDs](#onestop-ids)
- [License](#license)

## Feeds

Public mobility/transit data feeds cataloged in the [Distributed Mobility Feed Registry](https://github.com/transitland/distributed-mobility-feed-registry) format.

Currently includes:

- [GTFS](https://gtfs.org/reference/static)
- [GTFS Realtime](https://gtfs.org/reference/realtime/v2/)

For future addition:

- [GBFS](https://github.com/NABSA/gbfs) - see [pull request #41](https://github.com/transitland/transitland-atlas/pull/41)
- [MDS](https://github.com/openmobilityfoundation/mobility-data-specification)

## Operators

TODO: describe the new operator records (as JSON files)

TODO: give a checklist for creating a new operator record

TODO: link to operator listings on Transitland v2 website

## How to Add a New Feed

1. Duplicate an existing DMFR file under the `./feeds` directory. Title your new file with the hostname of the GTFS feed you are adding.
2. Add the appropriate URL to `current_static`
3. Propose a new Onestop ID for the feed; this can now be a "two-part" Onestop ID, which begins with `f-` and continues with a unique string, like the transit operator's name; use `~` instead of spaces or other punctuation in the name component.
4. Add license and/or authorization metadata if you are aware of it.
5. Open a PR. Feel free to add any questions as a comment on the PR if you are uncertain about your DMFR file.
6. GitHub Actions (continuous integration service) will run a basic validation check on your PR and report any errors.
7. A moderator will review and comment on your PR. If you don't get a response shortly, feel free to ping us at [hello@transit.land](mailto:hello@transit.land)

For more information on what can go into a DMFR file, see the [DMFR documentation](For more information on the available fields, see the [DMFR documentation](https://github.com/transitland/distributed-mobility-feed-registry).

## Onestop IDs

Every feed and operator record in the Atlas repository is identified by a unique [Onestop ID](https://transit.land/documentation/onestop-id-scheme/). Onestop IDs are meant to be globally unique (no duplicates in the world) and to be stable (no change over time).

To simplify the process of creating Onestop IDs, we now allow two different variants:

- a three-part Onestop ID includes an entity prefix, a geohash, and a name. For example: `f-9q9-bart`
- a two-part Onestop ID includes just the entity prefix and a name. For example: `f-banning~pass~transit`

The two-part Onestop ID is simpler to create if you are manually adding records to the Transitland Atlas repository.

Rules for Onestop IDs in this repository:

- Feeds start with `f-` and operators start with `o-`
- Geohash part is optional
- Name can include any alphanumeric characters in UTF-8
- The only separation or punctuation character allowed in the name component is a tilde (`~`)

## License

All data files in this repository are made available under the [Community Data License Agreement – Permissive, Version 1.0](LICENSE.txt). This license allows you to:

1. use this data for commercial, educational, or research purposes and be able to trust that it's cleanly licensed
2. duplicate data, as long as you mention (attribute) this source
3. use this data to create analyses and derived data (such as geocoding), without needing to provide attribution

We welcome you to contribute your edits and improvements directly to this repository. Please open a pull request!
