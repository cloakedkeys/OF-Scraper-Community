# Using PostgreSQL

By default the scraper keeps one SQLite file (`user_data.db`) per creator. Nothing needs installing, and that stays the default.

As an option, it can keep every creator's rows in one PostgreSQL database instead. That is useful if you want one place to query and back up, or you run the scraper from more than one machine against the same library.

## What you need

- A PostgreSQL server, version 15 or newer, and a database on it that the scraper's login may create tables in.
- The PostgreSQL driver:
  - **pip:** `pip install "ofscraper-community[postgres]"`
  - **Docker:** the image already includes it.
  - **Standalone downloads** (the `.exe` and the other single files) don't include it. Use the pip or Docker install.

## Turning it on

Set `OFSC_DATABASE_URL` to the database's address:

```
OFSC_DATABASE_URL=postgresql://user:password@host:5432/database
```

Any of these works:

- An environment variable. In `docker-compose.yml`, add it under `environment:`.
- A line in a `.env` file that `advanced_options.env_files` in `config.json` names.

Leave it unset, or empty, to keep using SQLite files.

It is not a `config.json` setting, because it holds a password. `config.json` has a line, `advanced_options.database_url`, that only says so and points here; anything written there is ignored and replaced, with a warning that says so.

The password is never written to the log. If a special character in it breaks the address, percent-encode it (`@` is `%40`).

At startup the scraper connects once and says which server it is using. If it can't (no server, a refused login, no such database, a server older than 15), it stops there with one sentence saying which.

## Bringing your existing databases in

Turning PostgreSQL on doesn't move what your `user_data.db` files hold. A separate command does:

```
python -m ofscraper.commands.postgres_import <folder>
```

`<folder>` is searched for `user_data.db` files; your profile's `.data` folder is the usual one. `OFSC_DATABASE_URL` says where they go, as above. Don't run the scraper while this runs: a file that is written to while it is being copied is refused.

Run like that, it only reports: how many files, creators and rows it found for each site, and anything that stands in the way. Nothing is written to PostgreSQL. Add `--apply` to bring the files in.

- **Your files are never changed.** Each one is copied to a working folder, and the copy is brought up to date the way a scan would, so files last used by an old version come in too. The working copies need as much room as the files; `--staging <folder>` chooses where they go.
- **All or nothing.** Every file goes in together. If anything fails part way, PostgreSQL keeps none of it, and you can run the command again.
- **Each file goes to its site's tables**, OnlyFans or Fansly.
- **A creator comes from one file.** If two files hold rows for the same creator (after a rename, or a changed `metadata` setting), put them together with Merge Databases first.
- **A creator PostgreSQL already has rows for is refused**, so a file can't be brought in twice. Creators it doesn't have yet can be brought in later.
- **A value PostgreSQL's column can't hold stops the file**, and is listed with its table, row and column: an id that isn't a number, a yes/no column holding something else, a date that can't be read. Empty dates become empty. Two rows that would be the same row once a missing creator is filled in stop it too.
- **`--skip-bad-files`** brings in the files that can be, and leaves out (and lists) the ones that can't.

Afterwards the `user_data.db` files are no longer used. Keep them anyway, as your copy from before the switch.

## Going back to SQLite files

Unsetting `OFSC_DATABASE_URL` returns to your `user_data.db` files as they were, without anything scraped since. A second command writes what PostgreSQL holds back to files first:

```
python -m ofscraper.commands.postgres_export <folder>
```

Every creator PostgreSQL has rows for gets a file of their own, `<folder>/<the creator's id>/user_data.db`. A profile's `.data` folder is laid out like that, unless you changed the `metadata` setting: then move each file to where your setting puts it. `OFSC_DATABASE_URL` says which database is read, as above. Don't run the scraper while this runs: what it saves meanwhile isn't in the files.

Run like that, it only reports: how many creators and rows it found, and which creators already have a file. Nothing is written. Add `--apply` to write the files.

- **PostgreSQL is only read.** It holds everything afterwards as it did before.
- **A file that is already there is never changed.** Its creator is left out and listed, and the others are written. To get that creator's rows, run the command again with another folder, an empty one. You then have both files. If you brought the old file into PostgreSQL before, the new one holds everything and can take its place. If you didn't, put the two together with Merge Databases. A `user_data.db-wal` or `user_data.db-journal` file left where a database used to be is in the way too.
- **A file is whole or isn't there.** It is made under another name beside its place (`user_data.db.<letters>.partial`) and gets its name once it is complete. A run that is stopped leaves no half-written `user_data.db`, and can be run again: the files already written are left out. A `.partial` file left by a run that was killed can be deleted.
- **A file is one the scraper would have made today.** The code that makes a new creator's database makes it, so it has the same tables and has had every upgrade step.
- **A file is checked before it gets its name.** It has to hold the rows exactly as PostgreSQL gave them, and the import has to be able to read it. A creator whose file fails that is listed with the reason, and gets no file.
- **One site at a time.** If PostgreSQL has rows for both OnlyFans and Fansly, add `--site onlyfans` or `--site fansly` and give each site its own folder: an id is a creator of its own on each site.

What comes back is what the scraper reads from PostgreSQL. For files you brought in, those are the same rows, with these differences:

- **A date** is the same moment, written in UTC (`2024-03-01T09:30:00+00:00`), whatever form the old file had.
- **What the import tidied stays tidied:** an empty date is empty, a yes or no is 1 or 0, a number saved as text is a number.
- **A row's own `id`** is its place among the creator's rows, oldest first. In a file that held one creator that is usually the id it had. Where it isn't, an undo file that `recover_collisions` wrote for the old file no longer finds its rows.
- **A file that held several creators** comes back as one file for each. A file that held no posts, messages or media was never brought in, so it doesn't come back.

## What is different

- **One database for every creator.** Each site gets its own schema (`onlyfans`, `fansly`), holding the same tables a `user_data.db` has. Every row carries its creator's `model_id`.
- **Real column types.** Ids are 64-bit numbers (media, post and label ids are text: a few of them are words or hashes), yes/no columns are booleans and dates are timestamps, so other tools can query the database directly.
- **Already-downloaded media is still judged per creator.** A run skips media the database says is downloaded. A SQLite file is one creator's, so media was never skipped because another creator had it, and it isn't with PostgreSQL either: only the creator's own rows count. (`--force-model-unique` therefore changes nothing here.)
- **No automatic backup copies.** The scraper copies a SQLite file before it upgrades it. For PostgreSQL, back up with `pg_dump`.
- **Your SQLite files are left alone.** They are not read, changed or deleted while PostgreSQL is on.

## What doesn't work with it yet

- **Existing libraries start empty** until you bring your `user_data.db` files in (see above). Until then the scraper treats everything as new.
- **Tools that read `user_data.db` files** keep reading those files and don't see PostgreSQL: Merge Databases, `stash_sync`, `recover_collisions`, `scripts/creator_names.py`, and the GUI patch.

## Developing

`test/test_db_postgres_live.py` runs the engine against a real server. It is skipped unless `OFSC_TEST_POSTGRES_URL` names one the tests may use; each test works in a schema of its own and drops it. CI runs it against the oldest and newest supported PostgreSQL.

A column added to a SQLite table also needs adding to `TABLES` in `ofscraper/db/postgres.py`, with the step that adds it to PostgreSQL databases made before. `test/test_db_postgres.py` fails until the two match.
