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

Afterwards the `user_data.db` files are no longer used. Keep them: there is no command yet for going back.

## Syncing to Stash

`stash_sync` (see `STASH.md`) reads PostgreSQL when it is given `--postgres` in place of the paths to `user_data.db` files:

```
python -m ofscraper.commands.stash_sync --postgres
```

- **It reads the database that `OFSC_DATABASE_URL` names in the environment the command is run in.** It is a separate tool and doesn't read the scraper's settings, so an address that is only in a `.env` file that `config.json` names is not seen: set the variable for the command. In Docker, an address given under `environment:` is already set for a command run in the container.
- **It needs the PostgreSQL driver**, as the scraper does.
- **Every creator of both sites is synced** unless you narrow it: `--site onlyfans` or `--site fansly` keeps one site, and `--creator <username or id>` keeps the creators it matches and can be given more than once. It matches an id on either site, and any username a creator has had, in any case, so it can match more than one creator.
- **PostgreSQL is only read.** The command opens one read-only connection, sees the database as it was when it started reading, and closes the connection before it sends anything to Stash. Its login only needs `USAGE` on each site's schema and `SELECT` on its tables.
- **A read doesn't wait for ever.** One that waits more than 30 seconds for a table another program has locked, or that takes more than 5 minutes, is ended by the server and the command stops.
- **A few results differ from the files'.** `STASH.md` lists them.
- **Given `user_data.db` files, it still reads those files.** With `OFSC_DATABASE_URL` set it then says that they hold nothing scraped since PostgreSQL was turned on. Files and `--postgres` together are refused.
- **A server it can't use** (not running, a refused login, no such database, no driver, a login that may not read a site's tables) stops it with one sentence and exit code 1, before Stash is contacted. The sentence names the host, the port and the database, never the password.

## Recovering filename collisions

`recover_collisions` (see `COLLISION_RECOVERY.md`) works on PostgreSQL when it is given `--postgres` and a creator in place of the path to a `user_data.db`:

```
python -m ofscraper.commands.recover_collisions --postgres --creator <username or id>
python -m ofscraper.commands.recover_collisions --postgres --creator <username or id> --apply --undo-dir <folder>
python -m ofscraper.commands.recover_collisions --postgres --creator <username or id> --undo <undo file> --apply
```

- **It uses the database that `OFSC_DATABASE_URL` names in the environment the command is run in**, and needs the PostgreSQL driver, as `stash_sync --postgres` does (see above).
- **One creator at a time.** `--creator` names the creator, by username or id. It must fit exactly one creator. If more than one goes by it (an id both sites have, an id that is another creator's username, a username two creators have had), the command changes nothing and lists them: give `--site onlyfans` or `--site fansly`, or a username or id that only one of them has. Only that creator's rows in that site's tables are read and changed. As with files, media of two different creators that share a path are not looked for.
- **The report says which creator it worked on**: the site, the id and the username saved last (`creator`). Look at it before `--apply`: a number can be one creator's id and another's username.
- **It finds and changes what it would in a `user_data.db` with the same rows.** Without `--apply` it only reads, over a read-only connection, and reports. With `--apply` it resets the same download flags, and `--undo <file>` sets them back.
- **Run it where the scraper runs** (with Docker: in the scraper's container), so that the recorded paths are the files' own. Paths recorded on another system (Windows paths read on Linux, or the reverse) are refused. `--path-root` is for relative recorded paths only, as before.
- **Close the scraper first**, as with files. A file locks the scraper out while the command works; PostgreSQL does not. If the scraper changes one of the rows while the command runs, the command changes nothing and says so. Run it again once the scraper is closed. What the scraper saves for the first time while the command runs is not seen by it: a post saved meanwhile for a media that is being reset keeps its download flag.
- **`--apply` needs `--undo-dir <folder>`**, the folder the undo file is written to. The undo file is the only way back, so the folder must be one that is kept. **In Docker, give a mounted folder**: a folder inside the container is gone when the container is. A preview given `--undo-dir` checks that the folder is there and can be written to. The report gives the undo file's full path.
- **All or nothing, until the changes are saved.** The undo file is written first. If it can't be written, or anything fails or the command is stopped before the changes are saved, no row is changed and the undo file is removed.
- **Once the changes are being saved, the undo file is kept whatever happens.** If the connection is lost or the command is stopped (Ctrl-C) at that moment, it can't know whether the server kept the changes. It says so and where the undo file is: run it with `--undo <that file>`, without `--apply`, to see whether there is anything to restore.
- **The undo file** names each row by its media and post, and records what the row held, and the server and database it was made on (not the login).
- **`--undo` restores every row in the undo file, or none.** With a `user_data.db` it skips the rows that no longer fit; here it stops. It is refused for another creator or site, and when a row is no longer as the recovery left it (for example, its media was downloaded again under another name); the message then says if the undo file was made on another database than the one in use. An undo file written for a `user_data.db` only fits that file, also after the file was brought into PostgreSQL; one written from PostgreSQL only fits PostgreSQL.
- **An undo file is trusted as far as the rows agree with it.** As with files, one written by hand can mark as downloaded a row the recovery never touched. Only that creator's rows, only from not downloaded to downloaded, and only a row whose folder, file name, size and hash are the ones the undo file gives.
- **Given a `user_data.db`, it still works on that file.** With `OFSC_DATABASE_URL` set it then says that the file holds nothing scraped since PostgreSQL was turned on. A file and `--postgres` together are refused.
- **Behind a connection pooler** that hands each transaction to another server connection (PgBouncer in transaction mode), the time zone and the timeouts the command sets may not hold. Give it the server's own address.

## What is different

- **One database for every creator.** Each site gets its own schema (`onlyfans`, `fansly`), holding the same tables a `user_data.db` has. Every row carries its creator's `model_id`.
- **Real column types.** Ids are 64-bit numbers (media, post and label ids are text: a few of them are words or hashes), yes/no columns are booleans and dates are timestamps, so other tools can query the database directly.
- **Already-downloaded media is still judged per creator.** A run skips media the database says is downloaded. A SQLite file is one creator's, so media was never skipped because another creator had it, and it isn't with PostgreSQL either: only the creator's own rows count. (`--force-model-unique` therefore changes nothing here.)
- **No automatic backup copies.** The scraper copies a SQLite file before it upgrades it. For PostgreSQL, back up with `pg_dump`.
- **Your SQLite files are left alone.** They are not read, changed or deleted while PostgreSQL is on.

## What doesn't work with it yet

- **Existing libraries start empty** until you bring your `user_data.db` files in (see above). Until then the scraper treats everything as new.
- **Tools that read `user_data.db` files** keep reading those files and don't see PostgreSQL: Merge Databases, `scripts/creator_names.py`, and the GUI patch. Nothing scraped since you turned PostgreSQL on is in those files, and what a tool changes in them does not reach PostgreSQL.
  - `scripts/creator_names.py` says so when it starts, if `OFSC_DATABASE_URL` is set in the environment it is run in. It doesn't read the scraper's settings, so it says nothing when the address is only in an env file that `config.json` names.
  - Merge Databases says so on its screen.
  - The GUI patch doesn't say so.
  - `stash_sync` and `recover_collisions` work on PostgreSQL when you tell them to: see "Syncing to Stash" and "Recovering filename collisions" above.
- **Going back** to SQLite files with what PostgreSQL holds isn't built. Unsetting `OFSC_DATABASE_URL` returns to your SQLite files as they were.

## Developing

`test/test_db_postgres_live.py` runs the engine against a real server. It is skipped unless `OFSC_TEST_POSTGRES_URL` names one the tests may use; each test works in a schema of its own and drops it. CI runs it against the oldest and newest supported PostgreSQL. `test/test_stash_sync_postgres_live.py` does the same for `stash_sync --postgres`, and `test/test_collision_recovery_postgres_live.py` for `recover_collisions --postgres`.

A column added to a SQLite table also needs adding to `TABLES` in `ofscraper/db/postgres.py`, with the step that adds it to PostgreSQL databases made before. `test/test_db_postgres.py` fails until the two match.
