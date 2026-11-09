def select_all(cur, table, columns):
    """A SELECT of `columns` from `table`, for a table that may be older than
    this version.

    Each item of `columns` is a column name, or a (name, stand_in) pair for a
    column a later version added: when the table doesn't have it, stand_in (an
    SQL expression, e.g. "NULL") is selected under its name instead. SQLite
    checks every column name before it runs a query, so this is decided here,
    from the table's columns, and not inside the query. Nothing is changed in
    the table.
    """
    present = {row[1] for row in cur.execute(f"PRAGMA table_info('{table}')")}
    parts = []
    for column in columns:
        if isinstance(column, str):
            parts.append(column)
            continue
        name, stand_in = column
        parts.append(name if name in present else f"{stand_in} AS {name}")
    return f"SELECT {', '.join(parts)} FROM {table};"
