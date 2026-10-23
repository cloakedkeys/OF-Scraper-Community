# Organize downloads in Stash

[Stash](https://stashapp.cc) is a self-hosted media library. It indexes your
download folders, but on its own it only knows file names. This command copies
what OF-Scraper recorded about each file into Stash:

- **Performer:** the creator, created if needed, with a link to their OnlyFans page.
- **Studio:** `OnlyFans` (change it with `--studio`).
- **Date:** when the post or message was posted.
- **Title and details:** the caption's first line, and the full caption.
- **Code:** the post ID.
- **URL:** the post's page, for timeline posts. Messages have no public page.
- **Tags:** where the file came from, e.g. `OnlyFans Timeline`, `OnlyFans Messages` or
  `OnlyFans Paid` (change the prefix with `--tag-prefix`, or turn them off with `--no-tags`).

It only fills in empty fields and adds the performer, studio and tags. Anything
you've set in Stash is kept, so running it again changes nothing that's already
there.

It reads OF-Scraper's databases offline and never contacts OnlyFans.

## Run it

Stash must be running, and must already have scanned your download folders, or
use `--scan` to have it scan them first.

Preview what would change:

```sh
python -m ofscraper.commands.stash_sync "<profile>/.data"
```

Then apply it:

```sh
python -m ofscraper.commands.stash_sync "<profile>/.data" --apply
```

`<profile>/.data` holds one folder per creator, each with a `user_data.db`.
To do a single creator, pass that creator's `user_data.db` instead.

The report is JSON. For each creator it counts:

| Count | Meaning |
|---|---|
| `to_update` / `updated` | files that were, or would be, changed in Stash |
| `up_to_date` | files that already had everything |
| `not_in_stash` | downloaded files Stash hasn't indexed; scan, or check `--path-map` |
| `unsupported_type` | audio, which Stash doesn't index |

A few of the missing paths are listed under `not_in_stash_sample`.

## Options

| Option | Use |
|---|---|
| `--apply` | write to Stash; without it, nothing is created or changed |
| `--scan` | with `--apply`, have Stash scan the download folders first and wait for it |
| `--url` | Stash's GraphQL address (default `http://localhost:9999/graphql`, or `$STASH_URL`) |
| `--api-key` | needed if Stash has a password: Settings → Security → API Key (or `$STASH_API_KEY`) |
| `--path-map LOCAL=STASH` | when Stash sees the files under a different path, e.g. in Docker; repeatable |
| `--organized` | also mark the items as organized in Stash |
| `--studio`, `--tag-prefix`, `--no-tags` | change the studio and tags |

### Stash in Docker

If OF-Scraper saved files under `D:\OnlyFans` and Stash's container mounts that
folder as `/data`:

```sh
python -m ofscraper.commands.stash_sync "<profile>/.data" --apply --path-map "D:\OnlyFans=/data"
```

The mapping also applies to `--scan`. Without it, Stash can't find any of the
files: the report then says so in a `hint`, and nothing is created in Stash.

## Notes

- Only files OF-Scraper marked as downloaded are matched, by their exact path.
  Files you've moved or renamed since won't be found.
- A file posted more than once (for example in a message and again as a
  purchase) gets one item in Stash with the earliest date and all of its tags.
- The creator's performer is matched by name, which is their username. A
  performer you created yourself under another name gets a second one; merge them
  in Stash if so.
