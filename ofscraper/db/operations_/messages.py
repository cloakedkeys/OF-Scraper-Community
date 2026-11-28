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

import ofscraper.db.operations_.media as media
import ofscraper.db.operations_.wrapper as wrapper
import ofscraper.db.utils.columns as db_columns
import ofscraper.db.utils.convert as convert
import ofscraper.db.utils.rebuild as rebuild
import ofscraper.utils.settings as settings

console = Console()
log = logging.getLogger("shared")

# user_id==sender
messagesCreate = """
CREATE TABLE IF NOT EXISTS messages (
id INTEGER NOT NULL,
post_id INTEGER NOT NULL,
text VARCHAR,
price INTEGER,
paid BOOLEAN,
archived BOOLEAN,
created_at TIMESTAMP,
user_id INTEGER,
model_id INTEGER,
is_deleted BOOLEAN DEFAULT 0,
group_id VARCHAR,
PRIMARY KEY (id),
UNIQUE (post_id,model_id)
)
"""
# a message another run saved since this one read the ids is already there (#657)
messagesInsert = """INSERT INTO messages(
post_id, text,price,paid,archived,
created_at,user_id,model_id,group_id)
            VALUES (?, ?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING;"""
messagesInsertTransition = """INSERT INTO messages(
post_id, text,price,paid,archived,
created_at,user_id,model_id,is_deleted,group_id)
            VALUES (?, ?,?,?,?,?,?,?,?,?);"""
messagesUpdate = """UPDATE messages
SET text = ?, price = ?, paid = ?, archived = ?, created_at = ?, user_id=?,model_id=?, is_deleted=FALSE
WHERE post_id = ? and model_id=(?);"""
# fills group_id of rows saved before it existed, on every scan; a message
# never changes conversation, so the metadata update leaves it alone
messagesFillMissing = """UPDATE messages
SET group_id = ?
WHERE post_id = ? AND model_id = (?) AND group_id IS NULL;"""
allMessagesCheck = """
SELECT post_id FROM messages
"""
# columns a later version added are (name, stand-in when missing): see
# db_columns.select_all
messagesSelectTransition = [
    "post_id",
    "text",
    "price",
    "paid",
    "archived",
    "created_at",
    "user_id",
    ("model_id", "NULL"),
    ("is_deleted", "0"),
    ("group_id", "NULL"),
]
messagesData = """
SELECT created_at,post_id FROM messages where model_id=(?)
"""
messageIgnoreGhost = """
UPDATE messages
SET is_deleted = TRUE
WHERE post_id = (?) AND model_id = (?);
"""

messageUnmarkGhost = """
UPDATE messages
SET is_deleted = FALSE
WHERE post_id = (?) AND model_id = (?) AND is_deleted = TRUE;
"""

deletedMessagesCheck = """
SELECT post_id FROM messages WHERE is_deleted = TRUE AND model_id = (?)
"""


@wrapper.operation_wrapper_async
def create_message_table(
    model_id=None, username=None, conn=None, db_path=None, **kwargs
):
    with contextlib.closing(conn.cursor()) as cur:
        cur.execute(messagesCreate)
        conn.commit()


@wrapper.operation_wrapper_async
def update_messages_table(messages: dict, model_id=None, conn=None, **kwargs):
    with contextlib.closing(conn.cursor()) as cur:
        updateData = list(
            map(
                lambda message: (
                    message.db_text,
                    message.price,
                    message.paid,
                    message.archived,
                    wrapper.date_value(conn, message.date),
                    message.fromuser,
                    model_id,
                    message.id,
                    model_id,
                ),
                messages,
            )
        )
        cur.executemany(messagesUpdate, updateData)
        conn.commit()


@wrapper.operation_wrapper_async
def fill_messages_missing_fields(messages: list, model_id=None, conn=None, **kwargs):
    fillData = [
        (message.group_id, message.id, model_id)
        for message in messages
        if message.group_id is not None
    ]
    if not fillData:
        return
    with contextlib.closing(conn.cursor()) as cur:
        cur.executemany(messagesFillMissing, fillData)
        conn.commit()


@wrapper.operation_wrapper_async
def write_messages_table(messages: dict, model_id=None, conn=None, **kwargs):
    with contextlib.closing(conn.cursor()) as cur:
        insertData = list(
            map(
                lambda message: (
                    message.id,
                    message.db_text,
                    message.price,
                    message.paid,
                    message.archived,
                    wrapper.date_value(conn, message.date),
                    message.fromuser,
                    model_id,
                    message.group_id,
                ),
                messages,
            )
        )
        cur.executemany(messagesInsert, insertData)
        conn.commit()


MESSAGES = rebuild.TableSpec(
    "messages",
    messagesCreate,
    messagesInsertTransition,
    messagesSelectTransition,
    keys=(
        "post_id",
        "text",
        "price",
        "paid",
        "archived",
        "created_at",
        "user_id",
        "model_id",
        "is_deleted",
    ),
    optional=("group_id",),
)


@wrapper.operation_wrapper_async
def write_messages_table_transition(
    inputData: list, model_id=None, conn=None, **kwargs
):
    with contextlib.closing(conn.cursor()) as cur:
        cur.executemany(
            messagesInsertTransition, rebuild.transition_rows(MESSAGES, inputData)
        )
        conn.commit()


@wrapper.operation_wrapper_async
def replace_messages_table(inputData: list, model_id=None, conn=None, **kwargs):
    rebuild.replace_spec_table(
        conn, MESSAGES, rebuild.transition_rows(MESSAGES, inputData)
    )


@wrapper.operation_wrapper_async
def get_all_messages_ids(model_id=None, username=None, conn=None, **kwargs) -> list:
    with contextlib.closing(conn.cursor()) as cur:
        creator, params = db_columns.creator_filter(
            cur, "messages", model_id, "WHERE"
        )
        cur.execute(allMessagesCheck + creator, params)
        return [dict(row)["post_id"] for row in cur.fetchall()]


@wrapper.operation_wrapper_async
def get_all_messages_transition(
    model_id=None, username=None, conn=None, database_model=None, **kwargs
) -> list:
    return rebuild.read_transition(conn, MESSAGES, database_model)


@wrapper.operation_wrapper_async
def get_messages_post_info(model_id=None, username=None, conn=None, **kwargs) -> list:
    return media.post_info(conn, messagesData, model_id)


@wrapper.operation_wrapper_async
def add_column_messages_ID(conn=None, **kwargs):
    db_columns.add_column(conn, "messages", "model_id", "INTEGER")


async def rebuild_messages_table(model_id=None, username=None, db_path=None, **kwargs):
    await rebuild.rebuild_table(
        get_all_messages_transition, replace_messages_table, model_id, username, db_path
    )


async def make_messages_table_changes(
    all_messages,
    model_id=None,
    username=None,
    from_api=False,
    skip_update=None,
    **kwargs,
):
    all_messages = list(all_messages)
    curr_id = set(await get_all_messages_ids(model_id=model_id, username=username))
    new_posts = list(filter(lambda x: x.id not in curr_id, all_messages))
    curr_posts = list(filter(lambda x: x.id in curr_id, all_messages))
    if len(new_posts) > 0:
        new_posts = convert.converthelper(new_posts)
        await write_messages_table(new_posts, model_id=model_id, username=username)
    # a message the API returned in this run isn't deleted; cached lists (the
    # check commands) can hold messages deleted since. Only --metadata updates the rest
    if from_api:
        await unmark_message_as_deleted(
            [ele.id for ele in curr_posts], model_id=model_id, username=username
        )
    # skip_update is True, or the ids of messages whose rows --metadata leaves
    # alone: the check commands' cached lists can be days older than the rows
    if settings.get_settings().metadata and skip_update is not True:
        updated = [ele for ele in curr_posts if ele.id not in (skip_update or ())]
        if len(updated) > 0:
            updated = convert.converthelper(updated)
            await update_messages_table(updated, model_id=model_id, username=username)
    if len(curr_posts) > 0:
        await fill_messages_missing_fields(
            curr_posts, model_id=model_id, username=username
        )


async def get_oldest_message_date(model_id=None, username=None, **kwargs):
    return await media.area_date(media.get_messages_media, model_id, username)


async def get_newest_message_date(model_id=None, username=None, **kwargs):
    read = media.get_messages_media
    return await media.area_date(read, model_id, username, newest=True)


@wrapper.operation_wrapper_async
def mark_message_as_deleted(
    post_ids, model_id=None, username=None, conn=None, **kwargs
):
    if not post_ids:
        return
    log.debug(
        f"Flagging the following message post_ids as deleted for {username}: {post_ids}"
    )
    with contextlib.closing(conn.cursor()) as curr:
        insertData = [[post_id, model_id] for post_id in post_ids]
        curr.executemany(messageIgnoreGhost, insertData)
        conn.commit()


@wrapper.operation_wrapper_async
def unmark_message_as_deleted(
    post_ids, model_id=None, username=None, conn=None, **kwargs
):
    if not post_ids:
        return
    with contextlib.closing(conn.cursor()) as curr:
        curr.executemany(
            messageUnmarkGhost, [[post_id, model_id] for post_id in post_ids]
        )
        conn.commit()


@wrapper.operation_wrapper_async
def get_deleted_messages_ids(model_id=None, username=None, conn=None, **kwargs) -> set:
    with contextlib.closing(conn.cursor()) as cur:
        cur.execute(deletedMessagesCheck, [model_id])
        return set([dict(row)["post_id"] for row in cur.fetchall()])


@wrapper.operation_wrapper_async
def add_column_message_is_deleted(conn=None, **kwargs):
    if db_columns.add_column(conn, "messages", "is_deleted", "BOOLEAN DEFAULT 0"):
        log.info("Added is_deleted column to messages table")


@wrapper.operation_wrapper_async
def add_column_message_group_id(conn=None, **kwargs):
    db_columns.add_column(conn, "messages", "group_id", "VARCHAR")
