r"""

 _______  _______         _______  _______  _______  _______  _______  _______  _______
(  ___  )(  ____ \       (  ____ \(  ____ \(  ____ )(  ___  )(  ____ )(  ____ \(  ____ )
| (   ) || (    \/       | (    \/| (    \/| (    )|| (   ) || (    )|| (    \/| (    )|
| |   | || (__     _____ | (_____ | |      | (____)|| (___) || (____)|| (__    | (____)|
| |   | ||  __)   (_____)(_____  )| |      |     __)|  ___  ||  _____)|  __)   |     __)
| |   | || (                   ) || |      | (\ (   | (   ) || (      | (      | (\ (
| (___) || )             /\____) || (____/\| ) \ \__| )   ( || )      | (____/\| ) \ \__
(_______)|/              \_______)(_______/|/   \__/|/     \||/       (_______/|/   \__/

"""

import contextlib
import logging

from rich.console import Console

import ofscraper.db.operations_.wrapper as wrapper
import ofscraper.db.utils.columns as db_columns
import ofscraper.db.utils.rebuild as rebuild

console = Console()
log = logging.getLogger("shared")

otherCreate = """
CREATE TABLE IF NOT EXISTS others (
	id INTEGER NOT NULL,
	post_id INTEGER NOT NULL,
	text VARCHAR,
	price INTEGER,
	paid INTEGER,
	archived BOOLEAN,
	created_at TIMESTAMP,
	model_id INTEGER,
	PRIMARY KEY (id),
	UNIQUE (post_id,model_id)
)
"""
productCreate = """
CREATE TABLE IF NOT EXISTS products (
	id INTEGER NOT NULL,
	post_id INTEGER NOT NULL,
	text VARCHAR,
	price INTEGER,
	paid INTEGER,
	archived BOOLEAN,
	created_at TIMESTAMP,
    title VARCHAR,
    model_id INTEGER,
	PRIMARY KEY (id),
	UNIQUE (post_id,model_id)
)
"""
schemaCreate = """
CREATE TABLE if not exists schema_flags (flag_name TEXT PRIMARY KEY, flag_value TEXT);
"""

schemaAll = """
SELECT flag_name FROM schema_flags WHERE flag_value = '1';
"""
# sets a flag whether or not it's there (INSERT OR REPLACE is SQLite's own
# spelling of this)
schemaInsert = """
INSERT INTO schema_flags (flag_name, flag_value)
VALUES (?, ?)
ON CONFLICT (flag_name) DO UPDATE SET flag_value = excluded.flag_value;
"""
# columns a later version added are (name, stand-in when missing): see
# db_columns.select_all
othersSelectTransition = [
    "post_id",
    "text",
    "price",
    "paid",
    "archived",
    "created_at",
    ("model_id", "NULL"),
]
othersInsert = """INSERT INTO others(
post_id, text,price,paid,archived,
created_at,model_id)
VALUES (?, ?,?,?,?,?,?);"""
productsSelectTransition = [
    "post_id",
    "text",
    "price",
    "paid",
    "archived",
    "created_at",
    "title",
    ("model_id", "NULL"),
]
productsInsert = """INSERT INTO products(
post_id, text,price,paid,archived,
created_at,title,model_id)
VALUES (?, ?,?,?,?,?,?,?);"""


@wrapper.operation_wrapper_async
def create_products_table(
    model_id=None, username=None, conn=None, db_path=None, **kwargs
):
    with contextlib.closing(conn.cursor()) as cur:
        cur.execute(productCreate)
        conn.commit()


@wrapper.operation_wrapper_async
def create_others_table(
    model_id=None, username=None, conn=None, db_path=None, **kwargs
):
    with contextlib.closing(conn.cursor()) as cur:
        cur.execute(otherCreate)
        conn.commit()


@wrapper.operation_wrapper_async
def add_column_other_ID(conn=None, **kwargs):
    db_columns.add_column(conn, "others", "model_id", "INTEGER")


@wrapper.operation_wrapper_async
def add_column_products_ID(conn=None, **kwargs):
    db_columns.add_column(conn, "products", "model_id", "INTEGER")


@wrapper.operation_wrapper_async
def create_schema_table(
    model_id=None, username=None, conn=None, db_path=None, **kwargs
):
    with contextlib.closing(conn.cursor()) as cur:
        cur.execute(schemaCreate)
        conn.commit()


@wrapper.operation_wrapper
def get_schema_changes(model_id=None, username=None, conn=None, db_path=None, **kwargs):
    with contextlib.closing(conn.cursor()) as cur:
        data = cur.execute(schemaAll).fetchall()
        return set(list(map(lambda x: x[0], data)))


@wrapper.operation_wrapper_async
def add_flag_schema(flag, model_id=None, username=None, conn=None, **kwargs):
    with contextlib.closing(conn.cursor()) as cur:
        cur.execute(schemaInsert, [flag, 1])
        conn.commit()


siteInsert = """
INSERT INTO schema_flags (flag_name, flag_value)
VALUES ('site', ?)
ON CONFLICT (flag_name) DO UPDATE SET flag_value = excluded.flag_value;
"""
siteSelect = """
SELECT flag_value FROM schema_flags WHERE flag_name = 'site';
"""


@wrapper.operation_wrapper_async
def write_site_flag(site, model_id=None, username=None, conn=None, **kwargs):
    """Record which site the database belongs to (only written for non-OnlyFans sites)."""
    with contextlib.closing(conn.cursor()) as cur:
        cur.execute(siteInsert, [site])
        conn.commit()


@wrapper.operation_wrapper
def get_site_flag(model_id=None, username=None, conn=None, db_path=None, **kwargs):
    with contextlib.closing(conn.cursor()) as cur:
        row = cur.execute(siteSelect).fetchone()
        return row[0] if row else None


OTHERS = rebuild.TableSpec(
    "others",
    otherCreate,
    othersInsert,
    othersSelectTransition,
    keys=("post_id", "text", "price", "paid", "archived", "created_at", "model_id"),
)
PRODUCTS = rebuild.TableSpec(
    "products",
    productCreate,
    productsInsert,
    productsSelectTransition,
    keys=(
        "post_id",
        "text",
        "price",
        "paid",
        "archived",
        "created_at",
        "title",
        "model_id",
    ),
)


@wrapper.operation_wrapper_async
def get_all_others_transition(
    model_id=None, username=None, conn=None, database_model=None, **kwargs
):
    return rebuild.read_transition(conn, OTHERS, database_model)


@wrapper.operation_wrapper_async
def write_others_table_transition(
    inputData, model_id=None, conn=None, **kwargs
) -> list:
    with contextlib.closing(conn.cursor()) as cur:
        cur.executemany(othersInsert, rebuild.transition_rows(OTHERS, inputData))
        conn.commit()


@wrapper.operation_wrapper_async
def replace_others_table(inputData, model_id=None, conn=None, **kwargs):
    rebuild.replace_spec_table(
        conn, OTHERS, rebuild.transition_rows(OTHERS, inputData)
    )


@wrapper.operation_wrapper_async
def get_all_products_transition(
    model_id=None, username=None, conn=None, database_model=None, **kwargs
):
    return rebuild.read_transition(conn, PRODUCTS, database_model)


@wrapper.operation_wrapper_async
def write_products_table_transition(
    inputData, model_id=None, conn=None, **kwargs
) -> list:
    with contextlib.closing(conn.cursor()) as cur:
        cur.executemany(productsInsert, rebuild.transition_rows(PRODUCTS, inputData))
        conn.commit()


@wrapper.operation_wrapper_async
def replace_products_table(inputData, model_id=None, conn=None, **kwargs):
    rebuild.replace_spec_table(
        conn, PRODUCTS, rebuild.transition_rows(PRODUCTS, inputData)
    )


async def rebuild_others_table(model_id=None, username=None, db_path=None, **kwargs):
    await rebuild.rebuild_table(
        get_all_others_transition, replace_others_table, model_id, username, db_path
    )


async def rebuild_products_table(model_id=None, username=None, db_path=None, **kwargs):
    await rebuild.rebuild_table(
        get_all_products_transition, replace_products_table, model_id, username, db_path
    )
