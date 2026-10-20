# vendor/commit.sh

/dev/null's commit script, as served at <https://dev-null.sh/commit> and reviewed on 2026-10-19.

- Source: [nunomaduro/dev-null.sh](https://github.com/nunomaduro/dev-null.sh), MIT licence
  (`composer.json`).
- SHA-256: `e016dd6ac2227060deec72ea3fca1fc98e88a82ac0b21719b342b72e1aedbbb0`. The test suite
  checks it, so a change to this file is never accidental.

Updating it: download the live script, read the diff, replace the file, and update the hash here and in
`tests/test_script.py`.

# vendor/emoji.json

GitHub's emoji shortcodes (`"star": "⭐"`), for the composer's emoji list. Made from
[github/gemoji](https://github.com/github/gemoji) v4.1.0 (commit `5476a66`), MIT licence:
`db/emoji.json`, keeping only each alias and its emoji, in gemoji's order.
