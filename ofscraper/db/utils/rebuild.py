import contextlib


def replace_table(conn, table, create_sql, insert_sql, rows):
    """Rebuild a table from rows in one transaction.

    The old table is renamed aside and only dropped once every row is in the
    new one, so a failure rolls back and leaves the old table as it was.
    """
    old = f"{table}_rebuild_old"
    with contextlib.closing(conn.cursor()) as cur:
        try:
            cur.execute(f"ALTER TABLE {table} RENAME TO {old};")
            cur.execute(create_sql)
            cur.executemany(insert_sql, rows)
            cur.execute(f"DROP TABLE {old};")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
