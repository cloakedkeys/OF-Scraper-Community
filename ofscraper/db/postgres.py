"""The optional PostgreSQL engine (#608).

SQLite keeps one file per creator. PostgreSQL keeps one set of tables per
site (a schema named after the site), shared by every creator, with each row
carrying its creator's model_id. The table modules run the same SQL on both;
this module is everything that differs:

- the tables, with PostgreSQL's own types, made at the current layout
- the connection pool, and a cursor that takes the `?` placeholders
- values going in and coming out, so the callers see what SQLite gave them
- which errors a retry can fix

The driver (psycopg) is an optional install, imported only when a database
URL is set.
"""

import atexit
import functools
import logging
import re
import sys
import threading
import urllib.parse

import ofscraper.utils.of_env.of_env as of_env

log = logging.getLogger("shared")

URL_KEY = "OFSC_DATABASE_URL"
# the oldest release still supported by PostgreSQL when this was written
MIN_SERVER = 15
INSTALL_HINT = 'pip install "{dist}[postgres]"'
# async operations run one at a time; the rest are reads from other threads
POOL_SIZE = 4
CONNECT_TIMEOUT = 15

# Made at the current layout: the steps that upgrade an old SQLite file never
# run here. test_db_postgres compares these with SQLite's tables, column by
# column. Ids are 64-bit, yes/no columns are booleans and dates are
# timestamps, so that other tools can query the database sensibly.
TABLES = {
    "medias": """
CREATE TABLE IF NOT EXISTS medias (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    media_id BIGINT,
    post_id BIGINT NOT NULL,
    link TEXT,
    directory TEXT,
    filename TEXT,
    size BIGINT,
    api_type TEXT,
    media_type TEXT,
    preview BOOLEAN,
    linked BOOLEAN,
    downloaded BOOLEAN,
    created_at TIMESTAMPTZ,
    posted_at TIMESTAMPTZ,
    duration TEXT,
    unlocked BOOLEAN,
    hash TEXT,
    model_id BIGINT NOT NULL,
    skip_permanent BOOLEAN,
    UNIQUE (media_id, model_id, post_id)
)""",
    "posts": """
CREATE TABLE IF NOT EXISTS posts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id BIGINT NOT NULL,
    text TEXT,
    price NUMERIC,
    paid BOOLEAN,
    archived BOOLEAN,
    pinned BOOLEAN,
    stream BOOLEAN,
    opened BOOLEAN,
    created_at TIMESTAMPTZ,
    model_id BIGINT NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE,
    purchase_type TEXT,
    mentions TEXT,
    UNIQUE (post_id, model_id)
)""",
    "messages": """
CREATE TABLE IF NOT EXISTS messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id BIGINT NOT NULL,
    text TEXT,
    price NUMERIC,
    paid BOOLEAN,
    archived BOOLEAN,
    created_at TIMESTAMPTZ,
    user_id BIGINT,
    model_id BIGINT NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE,
    group_id TEXT,
    UNIQUE (post_id, model_id)
)""",
    "stories": """
CREATE TABLE IF NOT EXISTS stories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id BIGINT NOT NULL,
    text TEXT,
    price NUMERIC,
    paid BOOLEAN,
    archived BOOLEAN,
    created_at TIMESTAMPTZ,
    model_id BIGINT NOT NULL,
    UNIQUE (post_id, model_id)
)""",
    "others": """
CREATE TABLE IF NOT EXISTS others (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id BIGINT NOT NULL,
    text TEXT,
    price NUMERIC,
    paid BOOLEAN,
    archived BOOLEAN,
    created_at TIMESTAMPTZ,
    model_id BIGINT NOT NULL,
    UNIQUE (post_id, model_id)
)""",
    "products": """
CREATE TABLE IF NOT EXISTS products (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id BIGINT NOT NULL,
    text TEXT,
    price NUMERIC,
    paid BOOLEAN,
    archived BOOLEAN,
    created_at TIMESTAMPTZ,
    title TEXT,
    model_id BIGINT NOT NULL,
    UNIQUE (post_id, model_id)
)""",
    "labels": """
CREATE TABLE IF NOT EXISTS labels (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label_id BIGINT,
    name TEXT,
    type TEXT,
    post_id BIGINT,
    model_id BIGINT NOT NULL,
    UNIQUE (post_id, label_id, model_id)
)""",
    "profiles": """
CREATE TABLE IF NOT EXISTS profiles (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    UNIQUE (user_id, username)
)""",
    "models": """
CREATE TABLE IF NOT EXISTS models (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_id BIGINT NOT NULL,
    UNIQUE (model_id)
)""",
    "schema_flags": """
CREATE TABLE IF NOT EXISTS schema_flags (
    flag_name TEXT PRIMARY KEY,
    flag_value TEXT
)""",
}
TABLE_COLUMNS = {
    table: tuple(
        line.split()[0]
        for line in create.splitlines()[2:]
        if line.startswith("    ") and not line.strip().startswith("UNIQUE")
    )
    for table, create in TABLES.items()
}
# The unique keys start with the row's own id; nearly every read asks for one
# creator's rows
CREATOR_INDEXES = ("medias", "posts", "messages", "stories", "others", "products", "labels")


class PostgresUnavailable(RuntimeError):
    """PostgreSQL is configured but can't be used: one plain sentence, for
    the user. Not retried."""


def url():
    """The configured connection URL, or "" for SQLite."""
    return str(of_env.getattr(URL_KEY) or "").strip()


def enabled():
    return bool(url())


def masked(text, database_url=None):
    """`text` with the URL's password hidden."""
    password = _password(database_url if database_url is not None else url())
    text = str(text)
    if password:
        text = text.replace(password, "{hidden}")
        text = text.replace(urllib.parse.quote(password, safe=""), "{hidden}")
    return text


def _password(database_url):
    try:
        password = urllib.parse.urlsplit(database_url).password
    except ValueError:
        return None
    if password is None:
        # the other spelling: host=... password=...
        found = re.search(r"\bpassword\s*=\s*('(?:[^'\\]|\\.)*'|\S+)", database_url)
        return found.group(1).strip("'") if found else None
    return urllib.parse.unquote(password)


def describe(database_url=None):
    """The server and database a URL names, without the login: for messages."""
    database_url = database_url if database_url is not None else url()
    try:
        parts = urllib.parse.urlsplit(database_url)
        if parts.scheme and parts.hostname:
            port = f":{parts.port}" if parts.port else ""
            return f"{parts.hostname}{port}{parts.path}"
    except ValueError:
        pass
    return "the configured database"


def schema_name(site):
    """The schema that holds a site's tables: the site's name."""
    name = str(site or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", name):
        raise PostgresUnavailable(f"{site!r} can't be used as a schema name")
    return name


def translate(query):
    """The table modules' SQL as the driver takes it: `?` becomes %s, and a
    literal % is doubled. Text inside quotes is left alone."""
    return _translate(query)


@functools.lru_cache(maxsize=512)
def _translate(query):
    out = []
    quoted = False
    for char in query:
        if char == "'":
            quoted = not quoted
        if char == "%":
            out.append("%%")
        elif char == "?" and not quoted:
            out.append("%s")
        else:
            out.append(char)
    return "".join(out)


def iso(text):
    """PostgreSQL's text for a timestamp (2024-01-02 03:04:05+00) as the ISO
    text the app writes and reads (2024-01-02T03:04:05+00:00)."""
    text = text.replace(" ", "T", 1)
    if re.search(r"[+-]\d\d$", text):
        text += ":00"
    return text


def number(text):
    """A NUMERIC as SQLite gave it: a whole number as an int."""
    value = float(text)
    return int(value) if value.is_integer() else value


def date_value(value):
    """A date as it's saved: ISO text with its offset, read the way the app
    reads dates back (arrow; a number is seconds since 1970, and text
    without an offset is UTC). Nothing for no date."""
    import arrow

    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            pass
    return arrow.get(value).isoformat()


class Row(tuple):
    """A result row read as sqlite3.Row is: row[0], row["name"], dict(row)."""

    __slots__ = ()
    _names = ()
    _index = {}

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                key = self._index[key.lower()]
            except KeyError:
                raise IndexError("No item with that key") from None
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._names)


@functools.lru_cache(maxsize=512)
def _row_class(names):
    return type(
        "Row",
        (Row,),
        {
            "__slots__": (),
            "_names": names,
            "_index": {name.lower(): place for place, name in enumerate(names)},
        },
    )


def row_factory(cursor):
    description = cursor.description or ()
    return _row_class(tuple(column.name for column in description))


def _driver():
    """psycopg and its pool, or a sentence saying how to install them."""
    try:
        import psycopg
        import psycopg_pool
    except ImportError as E:
        if getattr(sys, "frozen", False):
            fix = (
                "The standalone download doesn't include it: use the pip or "
                "Docker install, or unset the variable to use SQLite."
            )
        else:
            from ofscraper.__version__ import __dist_name__

            fix = f"Install it with: {INSTALL_HINT.format(dist=__dist_name__)}"
        raise PostgresUnavailable(
            f"{URL_KEY} is set, but the PostgreSQL driver isn't installed. {fix}"
        ) from E
    return psycopg, psycopg_pool


@functools.lru_cache(maxsize=1)
def _adapters():
    """How values go in and come out, so that the table modules and their
    callers see what SQLite gave them.

    In: numbers and booleans are sent as untyped text, as text already is,
    and the server reads each one as its column's type. SQLite does the
    same with a column's affinity: 1 saves as TRUE in a yes/no column and
    as 1 in a number column.
    Out: a boolean is 1 or 0, a timestamp is ISO text and a NUMERIC is an
    int or a float.
    """
    psycopg, _ = _driver()
    from psycopg.adapt import AdaptersMap, Dumper, Loader

    class AsText(Dumper):
        def dump(self, obj):
            return str(obj).encode()

    class BoolAsText(Dumper):
        def dump(self, obj):
            return b"1" if obj else b"0"

    class BoolAsInt(Loader):
        def load(self, data):
            return 1 if bytes(data) == b"t" else 0

    class TimestampAsIso(Loader):
        def load(self, data):
            return iso(bytes(data).decode())

    class NumericAsNumber(Loader):
        def load(self, data):
            return number(bytes(data).decode())

    adapters = AdaptersMap(psycopg.adapters)
    adapters.register_dumper(int, AsText)
    adapters.register_dumper(float, AsText)
    adapters.register_dumper(bool, BoolAsText)
    adapters.register_loader("bool", BoolAsInt)
    adapters.register_loader("timestamptz", TimestampAsIso)
    adapters.register_loader("numeric", NumericAsNumber)
    return adapters


@functools.lru_cache(maxsize=1)
def _cursor_class():
    psycopg, _ = _driver()

    class Cursor(psycopg.Cursor):
        """Takes the table modules' SQL (see translate)."""

        def execute(self, query, params=None, **kwargs):
            return super().execute(
                translate(query), () if params is None else params, **kwargs
            )

        def executemany(self, query, params_seq, **kwargs):
            return super().executemany(translate(query), params_seq, **kwargs)

    return Cursor


class PostgresBackend:
    """The methods the database wrapper asks of an engine (see
    wrapper.SQLiteBackend), for PostgreSQL."""

    name = "postgresql"
    # the tables are made at the current layout, so no upgrade steps and no
    # file to back up first
    born_current = True

    def __init__(self):
        self._pools = {}
        self._ready = set()
        self._lock = threading.RLock()

    @property
    def errors(self):
        return (_driver()[0].Error,)

    @property
    def operational_errors(self):
        psycopg = _driver()[0]
        return (psycopg.OperationalError, psycopg.errors.UndefinedTable)

    # --- connections ---

    def where(self, kwargs, read_only):
        """The operation's database: the current site's schema."""
        import ofscraper.sites as sites

        return schema_name(sites.current_site())

    def _pool(self, schema, timeout):
        with self._lock:
            pool = self._pools.get((url(), schema))
            if pool is None:
                pool = self._open_pool(schema, timeout)
                self._pools[(url(), schema)] = pool
            return pool

    def _open_pool(self, schema, timeout):
        psycopg, psycopg_pool = _driver()
        database_url = url()
        self._hide_password(database_url)
        adapters = _adapters()
        # a first connection of our own: the pool only says it timed out,
        # this says why (no server, a refused login, no such database)
        try:
            psycopg.connect(database_url, connect_timeout=CONNECT_TIMEOUT).close()
        except psycopg.Error as E:
            raise self._unavailable(E, database_url) from E
        lock_timeout = int(max(float(timeout or 0), 1) * 1000)

        def configure(conn):
            conn.execute("SET TIME ZONE 'UTC'")
            conn.execute(f"SET search_path TO {schema}")
            # a row another program has locked stops the wait in the end, as
            # a locked SQLite file does
            conn.execute(f"SET lock_timeout = {lock_timeout}")
            conn.commit()

        pool = psycopg_pool.ConnectionPool(
            database_url,
            min_size=1,
            max_size=POOL_SIZE,
            open=False,
            configure=configure,
            kwargs={
                "context": adapters,
                "cursor_factory": _cursor_class(),
                "row_factory": row_factory,
                "application_name": "ofscraper",
            },
            name=f"ofscraper-{schema}",
        )
        try:
            pool.open(wait=True, timeout=CONNECT_TIMEOUT * 2)
        except Exception as E:
            pool.close()
            raise self._unavailable(E, database_url) from E
        if not self._pools:
            atexit.register(self.shutdown)
        return pool

    def _hide_password(self, database_url):
        password = _password(database_url)
        if password:
            import ofscraper.utils.logs.utils.sensitive as sensitive

            for text in {password, urllib.parse.quote(password, safe="")}:
                sensitive.add_sensitive_pattern(re.escape(text))

    def _unavailable(self, error, database_url):
        """A connection that can't be made, as one sentence for the user.
        The server's own first line follows in brackets."""
        psycopg = _driver()[0]
        text = str(error)
        state = getattr(error, "sqlstate", None) or ""
        if state.startswith("28") or "authentication failed" in text:
            reason = "the user name or password was refused"
        elif state == "3D000" or ("database" in text and "does not exist" in text):
            reason = "there is no database with that name on the server"
        elif state == "42501" or isinstance(error, psycopg.errors.InsufficientPrivilege):
            reason = "the user isn't allowed to make or use its tables"
        else:
            reason = "the server can't be reached"
        detail = masked(" ".join(text.split()), database_url)[:300]
        return PostgresUnavailable(
            f"Can't use the PostgreSQL database at {describe(database_url)}: "
            f"{reason}. ({detail})"
        )

    def connect(self, where, read_only, *, timeout, check_same_thread=None):
        """A pooled connection with its transaction ready to begin: the
        driver begins one with the first statement, read-only for a read."""
        psycopg = _driver()[0]
        pool = self._pool(where, timeout)
        try:
            conn = pool.getconn(timeout=float(timeout or 30))
        except psycopg.Error as E:
            raise self._unavailable(E, url()) from E
        try:
            conn.read_only = bool(read_only)
        except BaseException:
            pool.putconn(conn)
            raise
        conn._ofscraper_pool = pool
        return conn

    def close(self, conn):
        """Done with the connection: what wasn't committed is rolled back,
        and the pool has it back (it drops a broken one)."""
        try:
            conn.rollback()
        except _driver()[0].Error as E:
            log.debug(f"Nothing to roll back on a lost connection ({masked(E)})")
        conn._ofscraper_pool.putconn(conn)

    def shutdown(self):
        with self._lock:
            pools, self._pools = self._pools, {}
            self._ready.clear()
        for pool in pools.values():
            pool.close()

    # --- errors ---

    def final_error(self, error, read_only, where):
        """The error to raise for `error`. A read-only read of tables that
        aren't there yet is DatabaseMissing, as it is for a missing file."""
        import ofscraper.db.operations_.wrapper as wrapper

        psycopg = _driver()[0]
        if read_only and isinstance(error, psycopg.errors.UndefinedTable):
            return wrapper.DatabaseMissing(str(error).strip())
        if isinstance(error, psycopg.Error):
            error.database_path = f"PostgreSQL ({where})"
        return error

    def retryable(self, error):
        """Whether a retry can fix `error`: two writers that got in each
        other's way, a lock that wasn't freed in time, or a lost connection
        (the pool hands out a new one)."""
        if isinstance(error, PostgresUnavailable):
            return False
        try:
            psycopg = _driver()[0]
        except PostgresUnavailable:
            return False
        return isinstance(
            error,
            (
                psycopg.errors.SerializationFailure,
                psycopg.errors.DeadlockDetected,
                psycopg.errors.LockNotAvailable,
                psycopg.OperationalError,
            ),
        ) and not isinstance(error, psycopg.errors.QueryCanceled)

    def busy(self, error):
        try:
            psycopg = _driver()[0]
        except PostgresUnavailable:
            return False
        return isinstance(
            error, (psycopg.errors.LockNotAvailable, psycopg.errors.DeadlockDetected)
        )

    # --- what is there ---

    def owns(self, conn_or_cur):
        try:
            psycopg = _driver()[0]
        except PostgresUnavailable:
            return False
        return isinstance(conn_or_cur, (psycopg.Connection, psycopg.Cursor))

    def table_exists(self, cur, table):
        return bool(
            cur.execute(
                "SELECT 1 FROM information_schema.tables"
                " WHERE table_schema = current_schema() AND table_name = ?",
                [table],
            ).fetchall()
        )

    def table_columns(self, cur, table):
        """The names of the columns `table` has (none if there's no table).
        The app's own tables are made complete, so they aren't asked for."""
        if table in TABLE_COLUMNS and self._tables_made(cur):
            return set(TABLE_COLUMNS[table])
        return {
            row[0]
            for row in cur.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema() AND table_name = ?",
                [table],
            ).fetchall()
        }

    def _tables_made(self, cur):
        pool = getattr(cur.connection, "_ofscraper_pool", None)
        return pool is not None and getattr(pool, "_ofscraper_tables_made", False)

    def database_exists(self, model_id=None, username=None, db_path=None):
        """Every creator's rows are in the one database, which is there."""
        return True

    def date_value(self, value):
        return date_value(value)

    # --- the tables ---

    def create_tables(self, flags):
        """Make the site's schema and tables if they aren't there. `flags`
        (the upgrade steps this build knows) are recorded as done when the
        tables are first made, since they're made at the current layout."""
        schema = self.where({}, False)
        with self._lock:
            if (url(), schema) in self._ready:
                return
            timeout = of_env.getattr("DATABASE_TIMEOUT")
            conn = self.connect(schema, False, timeout=timeout)
            try:
                with conn.cursor() as cur:
                    # one at a time: two programs starting together would
                    # both try to make the same tables
                    cur.execute("SELECT pg_advisory_xact_lock(?)", [0x6F667363])
                    # asked first: making a schema needs a permission that
                    # a login given a ready-made one may not have
                    if not cur.execute(
                        "SELECT 1 FROM pg_namespace WHERE nspname = ?", [schema]
                    ).fetchall():
                        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                    first = not self.table_exists(cur, "schema_flags")
                    for create in TABLES.values():
                        cur.execute(create)
                    for table in CREATOR_INDEXES:
                        cur.execute(
                            f"CREATE INDEX IF NOT EXISTS {table}_model_id"
                            f" ON {table} (model_id)"
                        )
                    if first:
                        cur.executemany(
                            "INSERT INTO schema_flags (flag_name, flag_value)"
                            " VALUES (?, '1') ON CONFLICT (flag_name) DO NOTHING",
                            [(flag,) for flag in sorted(flags)],
                        )
                conn.commit()
            except self.errors as E:
                raise self._unavailable(E, url()) from E
            finally:
                self.close(conn)
            self._ready.add((url(), schema))
            self._pool(schema, timeout)._ofscraper_tables_made = True

    def check(self):
        """The startup check: connect, and stop with one sentence if the
        server can't be used. Returns the server's version text."""
        schema = self.where({}, True)
        conn = self.connect(schema, True, timeout=of_env.getattr("DATABASE_TIMEOUT"))
        try:
            version = conn.info.server_version
            if version < MIN_SERVER * 10000:
                raise PostgresUnavailable(
                    f"The PostgreSQL server at {describe()} is version "
                    f"{version // 10000}; {MIN_SERVER} or newer is needed."
                )
            return f"{version // 10000}.{version % 10000}"
        finally:
            self.close(conn)


POSTGRES = PostgresBackend()
