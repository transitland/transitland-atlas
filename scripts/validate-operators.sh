#!/usr/bin/env bash

# will fail if there are duplicate Onestop IDs, or if a Onestop ID or name is not present
jq -s . ../operators/*.json | pipenv run sqlite-utils insert operators.db operators - \
  --pk=onestop_id \
  --not-null=onestop_id \
  --not-null=name \
  --truncate