# integrations

Export to external data standards. Integrations sit at the outer edge of the toolkit —
one may use anything in `core`, `episodes`, or `analytics`, and nothing in those tiers
depends on an integration, so adding or changing an export format cannot affect the
clinical logic beneath it. Each integration brings its own heavyweight dependencies and
is gated behind an optional extra, so installing omop-alchemy does not pull in formats
you are not exporting to.

## meds_standard

Export to the [Medical Event Data Standard](https://github.com/Medical-Event-Data-Standard/meds).

Not yet populated.
