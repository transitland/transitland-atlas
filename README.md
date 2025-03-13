<!-- omit in toc -->
# Transitland Atlas

An open catalog of transit/mobility data feeds and operators.

This catalog is used to power the canonical [Transitland](https://transit.land) platform, is available for distributed used of the [transitland-lib](https://github.com/interline-io/transitland-lib) tooling, and is open to use as a "crosswalk" within other transportation data systems.

**Table of contents**:

<!-- TOC created and updated by VSCode Markdown All in One extension -->
- [Feeds](#feeds)
- [How to Add a New Feed](#how-to-add-a-new-feed)
- [How to Update an Existing Feed](#how-to-update-an-existing-feed)
- [Operators](#operators)
- [Onestop IDs](#onestop-ids)
- [License](#license)

## Feeds

Public mobility/transit data feeds cataloged in the [Distributed Mobility Feed Registry](https://github.com/transitland/distributed-mobility-feed-registry) format.

Includes feeds in the following data specifications (specs):

- [GTFS](https://gtfs.org/reference/static)
- [GTFS Realtime](https://gtfs.org/reference/realtime/v2/)
- [GBFS](https://github.com/MobilityData/gbfs) - automatically synchronized from https://github.com/MobilityData/gbfs/blob/master/systems.csv
- [MDS](https://github.com/openmobilityfoundation/mobility-data-specification) - automatically synchronized from https://github.com/openmobilityfoundation/mobility-data-specification/blob/main/providers.csv

## How to Add a New Feed

1. Check if a `./feeds` file exists with the domain name for the feed URL. (ex. `http://bart.gov` -> `bart.gov.dmfr.json`)
    * If a file exists, use that file, otherwise create a new empty DMFR file.
    * To create a new file, you can use `example.com.dmfr.json` as a starting point, which contains the basic schema and an example feed.
    * Feeds exist as an array in the `feeds` property of a DMFR file.
2. Propose a new Onestop ID for the feed (see [below](#onestop-ids))
    * Feed Onestop ID's begins with `f-` and continues with a unique string, like the transit operator's name
    * Use lowercase, alphanumeric unicode characters  in the name component
    * Use `~` instead of spaces or other punctuation
3. Add the appropriate URL to `static_current`
4. Add license and/or authorization metadata if you are aware of it.
5. Open a PR. Feel free to add any questions as a comment on the PR if you are uncertain about your DMFR file.
6. GitHub Actions (continuous integration service) will run a basic validation check on your PR and report any errors.
7. A moderator will review and comment on your PR. If you don't get a response shortly, feel free to ping us at [hello@transit.land](mailto:hello@transit.land)

If you are using the Github web interface, you can click "Add a file -> Create a new file" in the `./feeds` directory, or when viewing an individual existing file, the pencil icon in the upper right of the contents display. Make sure to select "Create a new branch for this commit" and begin creating a pull request to propose changes.

For more information on what can go into a DMFR file, see the [DMFR documentation](https://github.com/transitland/distributed-mobility-feed-registry).

## Opinionated DMFR file format

The Atlas repository enforces an opinionated DMFR format that extends the standard DMFR JSON schema. This format enforces:
- Consistent JSON indentation
- Consistent key ordering
- A trailing line break at the end of the file (this is a change as of March 2025)

This opinionated format is not part of the DMFR specification itself, but rather an additional layer of formatting rules to ensure that DMFR files in the Atlas repository only change to reflect meaningful changes in the data, not inconsequential formatting differences. This reduces the amount of lines that are likely to change in PRs in this repository. The opinionated format is applied using the `transitland dmfr format` command from the [transitland-lib](https://github.com/interline-io/transitland-lib) CLI tool and is checked by GitHub Actions on all PRs in this repo.

## How to Update an Existing Feed

1. Find the DMFR file containing the feed.
2. Update the URLs and other properties for that feed
    * For static feeds, use `static_current` for the present URL.
    * Add the previous URL value to the `static_historic` array.
3. Edit the file and open the PR as described above.

Onestop ID values for feeds and operators are used to synchronize with existing values in the Transitland database. Editing the Onestop ID value will cause a new feed or operator record to be created; values in the database that are no longer present in the Transitland Atlas will be marked as soft-deleted. Use caution and clear intent when changing a Onestop ID value.

## Operators

[Operators](https://transit.land/operators) describe, annotate, and group data from different feed data sources. For example, `o-9q9-actransit` describes a transit operator, Alameda-Contra Costa Transit District, which pulls from two different data sources (one GTFS-RT, one static GTFS) and adds additional metadata such as a US National Transit Database ID.

Operators can exist in the top-level `operators` property if a DMFR file, or nested within a feed. An operator defined in the top-level `operators` property requires an `associated_feeds` value to connect the operator with data sources. When an operator is nested within a feed, there is an implicit association that all GTFS agencies contained in that file are associated with that operator, which helps reduces complexity and maintenance.

The key properties for an operator are:
* `onestop_id`: A Onestop ID value for this operator, starting with `o-`
* `name`: A formal name for the operator, such as `Bay Area Rapid Transit`
* `short_name`: A simpler, colloqial name for an operator, such as `BART`
* `tags`: A set of key,value string pairs that provide additional metadata and references
* `website`: A URL to find more information about this operator
* `associated_feeds`: An array of feed association objects; for each entry, `feed_onestop_id` is required and `gtfs_agency_id` is optional

Values for `onestop_id` and `name` are required; `associated_feeds` (either explicit or through nesting the operator in a feed) are highly recommended.

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

## Enriching Transitland Atlas with external reference data

We welcome help from the community to expand Transitland Atlas by reviewing external reference datasets, including:

- [NTD GTFS Weblinks](./external-data-for-reference/ntd-gtfs-weblinks/readme.md)

## License

All data files in this repository are made available under the [Community Data License Agreement – Permissive, Version 1.0](LICENSE.txt). This license allows you to:

1. use this data for commercial, educational, or research purposes and be able to trust that it's cleanly licensed
2. duplicate data, as long as you mention (attribute) this source
3. use this data to create analyses and derived data (such as geocoding), without needing to provide attribution

We welcome you to contribute your edits and improvements directly to this repository. Please open a pull request!
