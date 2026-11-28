import contextlib
import dataclasses
import re

import ofscraper.db.utils.columns as db_columns


@dataclasses.dataclass(frozen=True)
class TableSpec:
    """A creator table, as the upgrade's transition read and rebuild and the
    merge's copy see it."""

    name: str
    create: str
    # the insert of a whole row: rebuilds and merges
    insert: str
    # what the transition read selects (see db_columns.select_all); stored
    # as a tuple so the frozen spec can't be changed through it
    select: tuple
    # the insert's columns, in its order
    keys: tuple
    # the insert's last columns, which a row read before a later version
    # added them may lack
    optional: tuple = ()

    def __post_init__(self):
        # the specs are built from the modules' select lists
        object.__setattr__(self, "select", tuple(self.select))


def transition_rows(spec, rows):
    """The insert parameters for `rows` (dicts) of spec's table."""
    return [
        tuple([data[key] for key in spec.keys] + [data.get(key) for key in spec.optional])
        for data in rows
    ]


def read_transition(conn, spec, database_model=None):
    """Every row of spec's table, as dicts with each of spec.select's
    columns. A row with no model_id gets database_model."""
    with contextlib.closing(conn.cursor()) as cur:
        cur.execute(db_columns.select_all(cur, spec.name, spec.select))
        return [
            dict(row, model_id=row.get("model_id") or database_model)
            for row in map(dict, cur.fetchall())
        ]


def replace_spec_table(conn, spec, rows):
    """replace_table for spec's table, with these insert parameters."""
    replace_table(conn, spec.name, spec.create, spec.insert, rows)


async def rebuild_table(read, replace, model_id=None, username=None, db_path=None):
    """Rebuild a table from its rows: `read` is the table's wrapped
    transition read, `replace` its wrapped replace. A row with no model_id
    gets the database's one creator, if it has exactly one."""
    # imported here: profile.py imports this module
    from ofscraper.db.operations_.profile import get_single_model_via_profile

    database_model = get_single_model_via_profile(
        model_id=model_id, username=username, db_path=db_path
    )
    data = await read(
        model_id=model_id,
        username=username,
        database_model=database_model,
        db_path=db_path,
    )
    await replace(data, model_id=model_id, username=username, db_path=db_path)


def replace_table(conn, table, create_sql, insert_sql, rows):
    """Rebuild a table from rows in one transaction.

    Follows SQLite's documented order for a schema change: create the new
    table under a temporary name, copy the rows in, drop the old table, then
    rename the new one into place. Views that name the table keep pointing at
    it.

    If conn is not already in a transaction, this opens one (BEGIN IMMEDIATE),
    so it is safe on a plain sqlite3 connection too. Any failure rolls back
    and leaves the old table and its rows as they were.
    """
    new = f"{table}_rebuild_new"
    new_create_sql, count = re.subn(
        rf"(CREATE TABLE IF NOT EXISTS\s+){table}\b",
        rf"\g<1>{new}",
        create_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise ValueError(f"create statement does not create table {table}")
    new_insert_sql, count = re.subn(
        rf"(INSERT INTO\s+)'?{table}'?(?=\s*\()",
        rf"\g<1>{new}",
        insert_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise ValueError(f"insert statement does not insert into {table}")
    with contextlib.closing(conn.cursor()) as cur:
        try:
            if not conn.in_transaction:
                cur.execute("BEGIN IMMEDIATE;")
            cur.execute(new_create_sql)
            cur.executemany(new_insert_sql, rows)
            cur.execute(f"DROP TABLE {table};")
            # legacy mode renames only the table itself: views that name the
            # table are not checked while it is briefly missing, and keep
            # pointing at it once the new one has its name
            legacy = cur.execute("PRAGMA legacy_alter_table;").fetchone()[0]
            cur.execute("PRAGMA legacy_alter_table=ON;")
            try:
                cur.execute(f"ALTER TABLE {new} RENAME TO {table};")
            finally:
                cur.execute(f"PRAGMA legacy_alter_table={int(legacy)};")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
