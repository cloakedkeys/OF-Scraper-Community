# Recover downloads lost to filename collisions

Install the filename-collision prevention change (#116) before re-downloading.
Stop the scraper while running this offline command. It does not contact OnlyFans
or change media files.

Preview one model's database:

```sh
python -m ofscraper.commands.recover_collisions "<profile>/.data/<model_id>/user_data.db"
```

The JSON report lists paths shared by distinct media and `media_to_mark`.
These are **suspected** losses: old records cannot reliably identify which media
owns the surviving file. All media in an ambiguous group are included, even if
some legitimately contain identical content. Multiple posts referencing the same
media are not, by themselves, a collision. Unshared missing files are outside this
command's scope.

Apply the report by running the command again with `--apply`:

```sh
python -m ofscraper.commands.recover_collisions "<profile>/.data/<model_id>/user_data.db" --apply
```

Apply rechecks the database, creates a SQLite backup beside it, then sets only
`downloaded = 0` for affected media, including their other post records. It leaves
filenames, hashes, sizes and media files intact. The backup path is in the report.
Running it again before re-downloading makes no further changes or backup.

Then run a normal download for the affected model and content areas, with
`--after 2000` to include old messages. Do not use `--force-all`: the cleared flags
make affected media eligible while previously downloaded unaffected media stay
skipped. Normal access and content filters still apply; media no longer available
from the service cannot be restored. Do not run metadata update between recovery
and the download, since it can mark existing paths downloaded again.

The prevention change reuses matching content and gives different content a free
name, updating each database row to the final path. This command does not download
anything itself or prove that recovery succeeded.

Existing files are grouped by filesystem identity, including case variants on
case-insensitive storage. Missing files are grouped only by exact normalized
absolute path; missing case aliases cannot reliably be identified. If recorded
paths are relative, supply `--path-root` with the working directory used during
the original downloads. Only the explicitly selected database is scanned; sharing
between separate model databases is not detected. Run this targeted repair on old
affected databases, not as periodic maintenance: legitimate identical downloads
may also share a path.
