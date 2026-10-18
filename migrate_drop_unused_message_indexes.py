# Other Project Files
import modules.logconfig as LOG
import modules.Database as DB

"""
One-time migration: drops indexes on public.messages that nothing in the bot, its server-side
functions, or its views reads -- ~186GB of the database, and 5-6 extra index updates on every
chat message inserted (the inserts were bottlenecked on exactly that index maintenance I/O).

What stays, and why:
    pk_messages (channel_id, message_id) -- uniqueness on insert, FK target for
        user_first_message/user_channel_activity, join key for refresh_first_message_counts().
    idx_messages_video_id -- Get_Messages' known-message-ids lookup by video_id.
    idx_messages_user_id -- backs the messages.user_id -> user_ids FK (without it, deleting a
        user_ids row seq-scans every partition), and is the obvious index for per-user queries.

What goes:
    uq_messages_<talent>_channel_message -- standalone UNIQUE (channel_id, message_id)
        constraints on 19 partitions, exact duplicates of each partition's pk_messages index.
    idx_messages_user_timestamp / idx_messages_video_user_status -- added only to make
        video_message_stats' one-time backfill feasible. Recreate them temporarily if a
        backfill/reconciliation over messages is ever re-run.
    idx_messages_null / idx_messages_message_text -- btrees over raw message text; nothing
        does exact-match lookups on message text.
    idx_messages_datetime -- nothing internal filters messages by datetime.
    idx_messages_{null,message_text,datetime}_<talent>_members -- the same three indexes'
        copies on the legacy members-only message tables, dropped too so those tables keep the
        same index set as messages (pk + user_id + video_id).

Run with the bot stopped: DROP INDEX on a partitioned index can't be CONCURRENTLY, and it takes
an ACCESS EXCLUSIVE lock on every partition (briefly -- the drop itself just unlinks files).
Idempotent -- safe to re-run.

Once this has run, regenerate sql/ and SKILL.MD:
    python .claude/skills/chat-database/update_schema.py
"""

_PARENT_INDEXES = [
    "idx_messages_user_timestamp",
    "idx_messages_video_user_status",
    "idx_messages_null",
    "idx_messages_datetime",
    "idx_messages_message_text",
]

# Every UNIQUE constraint on a messages partition whose columns are exactly the partition's
# primary key columns -- i.e. a redundant duplicate of pk_messages on that partition.
_DUPLICATE_PK_CONSTRAINTS = """
    SELECT con.conrelid::regclass::text, con.conname
    FROM pg_inherits inh
    JOIN pg_constraint con ON con.conrelid = inh.inhrelid AND con.contype = 'u'
    JOIN pg_constraint pk ON pk.conrelid = inh.inhrelid AND pk.contype = 'p'
    WHERE inh.inhparent = 'public.messages'::regclass
      AND con.conkey = pk.conkey
    ORDER BY 1
"""

# The members-only tables' copies of the dropped text/datetime indexes.
_MEMBERS_INDEXES = r"""
    SELECT indexname FROM pg_indexes
    WHERE schemaname = 'public' AND tablename LIKE 'messages\_%\_members'
      AND indexname ~ '^idx_messages_(null|message_text|datetime)_.+_members$'
    ORDER BY 1
"""

def _messages_index_bytes(cursor) -> int:
    cursor.execute(r"""
        SELECT COALESCE(sum(pg_indexes_size(oid)),0) FROM pg_class
        WHERE oid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = 'public.messages'::regclass)
           OR (relkind = 'r' AND relname LIKE 'messages\_%\_members')
    """)
    return cursor.fetchone()[0]

def main() -> None:
    db = DB.PostgresClass()
    cur = db.cursor

    # Fail fast instead of queueing behind (and blocking everything behind us) some other
    # session's open transaction on a partition.
    cur.execute("SET lock_timeout = '15s'")

    before = _messages_index_bytes(cur)

    cur.execute(_DUPLICATE_PK_CONSTRAINTS)
    duplicates = cur.fetchall()
    for table, conname in duplicates:
        LOG.logger.info(f"Dropping duplicate PK constraint {conname} on {table}")
        cur.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{conname}"')

    for index in _PARENT_INDEXES:
        LOG.logger.info(f"Dropping public.{index} (and its partition indexes)")
        cur.execute(f"DROP INDEX IF EXISTS public.{index}")

    cur.execute(_MEMBERS_INDEXES)
    members_indexes = [r[0] for r in cur.fetchall()]
    for index in members_indexes:
        LOG.logger.info(f"Dropping public.{index}")
        cur.execute(f"DROP INDEX IF EXISTS public.{index}")

    db.database.commit()

    after = _messages_index_bytes(cur)
    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
    db_size = cur.fetchone()[0]
    db.Close()

    gb = 1024 ** 3
    LOG.logger.info(
        f"Migration complete. Dropped {len(duplicates)} duplicate constraint(s) and "
        f"{len(_PARENT_INDEXES)} partitioned index(es) and {len(members_indexes)} members-table "
        f"index(es). messages (+ *_members) index size: "
        f"{before / gb:,.1f} GB -> {after / gb:,.1f} GB (freed {(before - after) / gb:,.1f} GB). "
        f"Database is now {db_size}."
    )

if __name__ == "__main__":
    main()
