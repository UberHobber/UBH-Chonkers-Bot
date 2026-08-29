# Native Stuff
import os,random,time
from typing import Any,LiteralString

# Installed Stuff
import psycopg2
from psycopg2 import sql

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG

class PostgresClass:
    """
    Initializes a PostgreSQL database connection. See Settings.py for database configuration.
    """
    def __init__(self):
        LOG.logger.debug('Connecting to database...')
        self.database = psycopg2.connect(host=CFG.DB_HOST,port=CFG.DB_PORT,database=CFG.DB_NAME,user=CFG.DB_USR,password=CFG.DB_PASS)
        self.cursor = self.database.cursor()

    def Close(self):
        """
        LBA: Closes the database cursor and connection.
        """
        LOG.logger.debug('Closing SQL Database...')
        self.cursor.close()
        self.database.close()

    def ClearDB(self,tables:list[str]):
        """
        LBA: Clears database tables.

        :param tables: List of database tables to delete all entries from
        :type tables: List of strings
        """
        for table in tables:
            self.cursor.execute(f'DELETE FROM {table}')
            self.database.commit()
            LOG.logger.info(f'Data from {table} deleted.')

def InsertEntries(cursor:psycopg2.extensions.cursor,table:str,data_list:list[dict[str,Any]],conflict:str|None=None) -> None:
    """
    Inserts a given list of dictionaries into a target table.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Target table
    :type table: String
    :param data_list: A list of entries to input into the target table.
    :type data_list: List of Dictionaries
    :param conflict: A column name that is either a Primary Key, or contains a Unique Constraint
    :type conflict: String
    """
    try:
        LOG.logger.debug(f'{len(data_list)} item(s) to add to table {table} in database.')
        for item in data_list:
            columns: str = ', '.join(item.keys())
            placeholders: LiteralString = ', '.join(['%s' for _ in item])
            values = list(item.values())
            if conflict:
                conflict_text = f" ON CONFLICT ({conflict}) DO NOTHING"
                query: str = f'INSERT INTO {table} ({columns}) VALUES ({placeholders}){conflict_text}'
            else:
                query: str = f'INSERT INTO {table} ({columns}) VALUES ({placeholders})'



            if CFG.DB_VERBOSE == True:
                LOG.logger.info(query)
                LOG.logger.info(values)

            cursor.execute(query,values)
    except Exception as e:
        LOG.logger.error(f'Query: {query} ({type(query)})\nValues: {values} ({type(values)})\n')
        raise e

def UpdateEntry(cursor:psycopg2.extensions.cursor,table:str,data_column:str,data_value:Any,filter_column:str,filter_value:Any):
    """
    Updates an entry with a new value for a single column.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Target table
    :type table: String
    :param data_column: The column that will be targeted
    :type data_column: String
    :param data_value: The value to update into that row's column
    :type data_value: Acceptable datatype for your column
    :param filter_column: Column to target for filter
    :type filter_column: String
    :param filter_value: Value of column to filter by
    :type filter_value: Acceptable datatype for your column
    """
    try:
        LOG.logger.debug(f'Updating {data_column} in table {table} in database.')

        values = (data_value, filter_value)

        query: str = f'UPDATE {table} SET {data_column} = %s WHERE {filter_column} = %s'

        if CFG.DB_VERBOSE == True:
            LOG.logger.info(query)
            LOG.logger.info(values)

        cursor.execute(query,values)
    except Exception as e:
        LOG.logger.error(f'Query: {query} ({type(query)})\nValues: {values} ({type(values)})\n')
        raise e

def UpdateEntries(cursor:psycopg2.extensions.cursor,table:str,data_dict:dict[str,Any],filter_column:str,filter_value:Any):
    """
    Updates multiple columns in a single query.

    :param cursor: Database cursor object to execute commands.
    :param table: Target table
    :param data_dict: Dict of {column: value} pairs to update
    :param filter_column: Column to filter on
    :param filter_value: Value of column to filter by
    """
    try:
        LOG.logger.debug(f'Updating {len(data_dict)} column(s) in table {table} in database.')
        set_clause = ", ".join([f"{col} = %s" for col in data_dict.keys()])
        values = list(data_dict.values()) + [filter_value]
        query: str = f'UPDATE {table} SET {set_clause} WHERE {filter_column} = %s'
        if CFG.DB_VERBOSE == True:
            LOG.logger.info(query)
            LOG.logger.info(values)
        cursor.execute(query,values)
    except Exception as e:
        LOG.logger.error(f'Query: {query}\nValues: {values} ({type(values)})\n')
        raise e

def RecordMessageStatsBatch(cursor:psycopg2.extensions.cursor,batch:list[tuple[str,str,str,float,bool,int|None]]) -> None:
    """
    Incrementally updates video_message_stats and user_first_channel_message (and their
    dedup/first-message helper tables) for a batch of newly-inserted chat messages, in one round
    trip. Call this right before committing -- see _flush_pending_stats() in
    modules/Classes.py Get_Messages, which buffers one (channel_id, video_id, user_id, timestamp,
    is_member, member_status) tuple per newly-inserted message and flushes the whole buffer here.
    All of the counting logic lives server-side in public.record_message_stats_batch() /
    record_message_stats() (see sql/video_message_stats.sql and
    sql/user_first_channel_message_stats.sql).

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param batch: List of (channel_id, video_id, user_id, timestamp, is_member, member_status)
        tuples, one per message, SORTED BY user_id by the caller -- see below for why.
    :type batch: list[tuple]

    record_message_stats() never touches a video_message_stats row for any video other than its
    own (see that function's docstring in sql/user_first_channel_message_stats.sql, and
    RefreshFirstMessageCounts for where first_messages/first_channel_messages moved to), which
    closed one deadlock path. That alone wasn't enough, though: a worker's transaction spans up
    to 500 messages (one commit per batch), and each row here still takes a row lock on
    user_first_message/user_first_channel_message for whichever user it's for, held until that
    commit. Two workers processing two different videos that share chatters could each
    accumulate locks on overlapping users across their batch and lock them in opposite order --
    see the "deadlock detected ... user_first_message" incident between two DIFFERENT users.
    That's why the caller sorts by user_id before flushing: every transaction then acquires these
    row locks in the same ascending order no matter which video it's processing, so two
    transactions can never hold them in opposite order.

    Batching into one call (instead of one call per row) isn't just for round-trip efficiency --
    it's what makes the sort above actually pay off. Sorted-but-sequential single-row calls still
    left a worker blocked on a contended row waiting through up to 500 sequential network round
    trips to a remote DB host (multiple seconds of real stall, observed directly) before the
    lock-holder's batch finished committing. One call carrying all the rows removes essentially
    all of that round-trip latency from the wait, without changing the per-row order or any
    locking/upsert semantics -- record_message_stats_batch() just loops server-side in the order
    the arrays are given.

    The retry below is a defensive backstop on top of the ordering guarantee above -- e.g.
    Postgres lock queueing edge cases under extreme load -- not the primary defense. A savepoint
    scopes any rollback to just this batch instead of the whole transaction.
    """
    if len(batch) == 0:
        return

    channel_ids,video_ids,user_ids,timestamps,is_members,member_statuses = zip(*batch)
    query = 'SELECT record_message_stats_batch(%s,%s,%s,%s,%s,%s)'
    values = (list(channel_ids),list(video_ids),list(user_ids),list(timestamps),list(is_members),list(member_statuses))

    if CFG.DB_VERBOSE == True:
        LOG.logger.info(query)
        LOG.logger.info(values)

    max_attempts = 5
    for attempt in range(1,max_attempts + 1):
        cursor.execute("SAVEPOINT record_message_stats_sp")
        try:
            cursor.execute(query,values)
            cursor.execute("RELEASE SAVEPOINT record_message_stats_sp")
            return
        except psycopg2.errors.DeadlockDetected:
            cursor.execute("ROLLBACK TO SAVEPOINT record_message_stats_sp")
            if attempt == max_attempts:
                raise
            LOG.logger.warning(f'record_message_stats_batch deadlock for {len(batch)} message(s), retrying (attempt {attempt}/{max_attempts})...')
            time.sleep(random.uniform(0.05,0.25) * attempt)

def FinalizeVideoMessageRate(cursor:psycopg2.extensions.cursor,video_id:str) -> None:
    """
    Fills in video_message_stats.messages_per_min for a video once its duration is known.
    Call this once a video is confirmed finished (duration is NULL while a video is still live).

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param video_id: The video to finalize the message rate for.
    :type video_id: String
    """
    query = 'SELECT finalize_video_message_rate(%s)'
    values = (video_id,)

    if CFG.DB_VERBOSE == True:
        LOG.logger.info(query)
        LOG.logger.info(values)

    cursor.execute(query,values)

def RefreshFirstMessageCounts(cursor:psycopg2.extensions.cursor) -> None:
    """
    Recomputes video_message_stats.first_messages/first_channel_messages from
    user_first_message/user_first_channel_message. Call this once per full run -- see the bottom
    of Main.py -- not per message: record_message_stats() no longer patches these columns
    incrementally, since doing so required reaching into a SECOND, unrelated video's
    video_message_stats row whenever a user's first-message pointer moved, which could deadlock
    against another worker thread's video (see record_message_stats()'s docstring in
    sql/user_first_channel_message_stats.sql for the incident this replaced).

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    """
    query = 'SELECT refresh_first_message_counts()'

    if CFG.DB_VERBOSE == True:
        LOG.logger.info(query)

    cursor.execute(query)

def RefreshUserMessageRankings(cursor:psycopg2.extensions.cursor) -> None:
    """
    Refreshes the user_message_rankings materialized view (per-channel and global message-count
    leaderboard ranks). Call this once per full run -- see the bottom of Main.py -- not per
    message or per video; a rank isn't incrementally maintainable the way totals/counts are, and
    this is a full recomputation over the user_first_channel_message rollup. Uses CONCURRENTLY so
    readers (e.g. Grafana) keep seeing the previous ranks instead of being locked out mid-refresh.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    """
    query = 'SELECT refresh_user_message_rankings()'

    if CFG.DB_VERBOSE == True:
        LOG.logger.info(query)

    cursor.execute(query)

def DeleteEntries(cursor:psycopg2.extensions.cursor,table:str,filter:dict[str,Any]|None=None) -> None:
    """
    Deletes an entry in the target table matching a given filter. Deletes ALL entries if no filter given.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Target table
    :type table: String
    :param filter: Deletes entries that contain specific values in specific columns. If non specified, delete EVERYTHING in table.
    :type filter: Dictionary of {[Column] , [Value]}
    """

    base_query = f'DELETE FROM {table}'

    try:
        if filter != None:
            column_list:str = " AND ".join([f'{col} = %s' for col in filter.keys()])
            values = list(filter.values())

            query: str = f'{base_query} WHERE {column_list}'

            if CFG.DB_VERBOSE == True:
                LOG.logger.info(query)
                LOG.logger.info(values)

            cursor.execute(query,values)
        else:
            LOG.logger.info(base_query)
            cursor.execute(base_query)
    except Exception as e:
        LOG.logger.error(f'Query: {query}\nValues: {values}\n')
        raise e

def CreateMessagesPartition(cursor:psycopg2.extensions.cursor,table:str,channel_id:str) -> None:
    """
    Creates a partition of public.messages for the given channel_id.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Partition table name (e.g. "messages_<db_suffix>").
    :type table: String
    :param channel_id: The channel's user_id -- the partition's list value.
    :type channel_id: String
    """
    query = sql.SQL('CREATE TABLE public.{partition} PARTITION OF public.messages FOR VALUES IN (%s)').format(partition=sql.Identifier(table))

    if CFG.DB_VERBOSE == True:
        LOG.logger.info(query.as_string(cursor))
        LOG.logger.info(channel_id)

    cursor.execute(query,(channel_id,))

def TableExists(cursor:psycopg2.extensions.cursor,table:str) -> bool:
    """
    Checks whether a table exists in the public schema.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Table name to check for.
    :type table: String
    :return: Whether the table exists.
    :rtype: Boolean
    """
    cursor.execute('SELECT to_regclass(%s) IS NOT NULL',(f'public.{table}',))
    row = cursor.fetchone()
    return row is not None and bool(row[0])

def EnsureMessagesPartition(cursor:psycopg2.extensions.cursor,table:str,channel_id:str) -> None:
    """
    Creates the given channel's messages partition table if it doesn't already exist.
    Covers channels added directly to channel_directory (e.g. via manual SQL) that don't
    yet have a matching messages_<db_suffix> partition. Caller is responsible for committing.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Partition table name (e.g. "messages_<db_suffix>").
    :type table: String
    :param channel_id: The channel's user_id -- the partition's list value.
    :type channel_id: String
    """
    if TableExists(cursor,table):
        return

    try:
        CreateMessagesPartition(cursor,table,channel_id)
        LOG.logger.info(f'Created missing partition table {table} for channel_id {channel_id}.')
    except Exception as e:
        LOG.logger.error(f'Failed to create missing partition table {table} for channel_id {channel_id}: {e}')
        raise e

# sql/<name>.sql -> the table (or matview) whose presence means that file has already been
# applied. Order matters -- see sql/README.md for the FK reasons (e.g. messages before
# nicknames, messages_members before nicknames_members). Kept in sync by hand with
# sql/README.md and .claude/skills/chat-database/update_schema.py's FILE_GROUPS -- these are
# small, stable clusters that don't change often.
_CORE_SQL_FILES = [
    ("channel_directory","channel_directory"),
    ("user_ids","user_ids"),
    ("videos","videos"),
    ("tags","tags"),
    ("emotes","emotes"),
    ("messages","messages"),
    ("nicknames","nicknames"),
    ("subtitles","subtitles"),
    ("user_first_channel_message_stats","user_first_channel_message"),
    ("video_message_stats","video_message_stats"),
    ("user_global_message_rankings","user_message_rankings"),
]
_MEMBERS_SQL_FILES = [
    ("emotes_members","emotes_calli_members"),
    ("messages_members","messages_calli_members"),
    ("nicknames_members","nickname_matches_kiara_members"),
    ("subtitles_members","subtitles_ame_members"),
]

_SQL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"sql")

def EnsureSchema(cursor:psycopg2.extensions.cursor,members_only:bool) -> None:
    """
    Verifies the database structure this bot needs exists, and builds whatever's missing from
    the DDL scripts in sql/ -- lets a fresh, empty database bootstrap itself on first run
    instead of requiring the scripts to be applied by hand first. Safe to call on every
    startup: each file is only applied if its anchor table/matview (see _CORE_SQL_FILES/
    _MEMBERS_SQL_FILES) doesn't already exist, so an already-provisioned database is a no-op.

    The `*_members` files are only applied when members_only is True, so a public-only
    deployment never gets members-only tables it doesn't need. Caller is responsible for
    committing.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param members_only: Whether to also ensure the members-only tables exist (CFG.GET_MEMBERS_ONLY).
    :type members_only: Boolean
    """
    files = list(_CORE_SQL_FILES)
    if members_only:
        files += _MEMBERS_SQL_FILES

    for file_name,anchor_table in files:
        if TableExists(cursor,anchor_table):
            continue
        path = os.path.join(_SQL_DIR,f"{file_name}.sql")
        with open(path,"r",encoding="utf-8") as f:
            ddl = f.read()
        try:
            cursor.execute(ddl)
            LOG.logger.info(f'Database structure missing -- created it from sql/{file_name}.sql.')
        except Exception as e:
            LOG.logger.error(f'Failed to build database structure from sql/{file_name}.sql: {e}')
            raise e

def AddChannel(cursor:psycopg2.extensions.cursor,name:str,user_id:str,db_suffix:str,group:str) -> None:
    """
    Registers a new channel: adds its row to channel_directory and creates its
    messages_<db_suffix> partition of the partitioned messages table. Caller is
    responsible for committing (or rolling back) both statements as one transaction,
    since the channel_directory row and its messages partition must exist together.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param name: Channel's display name, used as its key in CFG.CHANNEL_DIRECTORY.
    :type name: String
    :param user_id: YouTube user ID (channel_directory.user_id / channel_id used elsewhere).
    :type user_id: String
    :param db_suffix: Short identifier used to name the channel's messages partition table.
    :type db_suffix: String
    :param group: Talent group/agency the channel belongs to.
    :type group: String
    """
    try:
        insert_query = 'INSERT INTO channel_directory (name, user_id, db_suffix, "group") VALUES (%s, %s, %s, %s)'
        insert_values = (name,user_id,db_suffix,group)

        if CFG.DB_VERBOSE == True:
            LOG.logger.info(insert_query)
            LOG.logger.info(insert_values)

        cursor.execute(insert_query,insert_values)

        CreateMessagesPartition(cursor,f'messages_{db_suffix}',user_id)

        LOG.logger.info(f'Added channel {name} (messages_{db_suffix}) to channel_directory.')
    except Exception as e:
        LOG.logger.error(f'Failed to add channel {name} (user_id={user_id}, db_suffix={db_suffix}): {e}')
        raise e

def GetEntries(cursor:psycopg2.extensions.cursor,table:str,columns:str='*',filter:dict[str,Any]|None=None) -> list[dict[str, Any]]:
    """
    Retrieves entries from a given table. Can specify columns and various filters.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Table name
    :type table: String
    :param columns: Retrieves values from specified columns. If none are specified, get all values from entries.
    :type columns: String formatted as '[ColName], [Colname], etc.'
    :param filter: Get results that contain specific values in specific columns. If non specified, don't filter anything
    :type filter: Dictionary of {[Column] , [Value]}
    :return: List of entries
    :rtype: Format matching Entry Objects (List of Dictionaries {[Colname] , [Value]})
    """
    base_query: str = f'SELECT {columns} FROM {table}'

    if filter != None:

        column_list:str = " AND ".join([f'{col} = %s' for col in filter.keys()])

        values = list(filter.values())

        query = f'{base_query} WHERE {column_list}'
        cursor.execute(query,values)
    else:
        cursor.execute(base_query)

    if cursor.description is not None:
        entry_columns:list[str] = [description[0] for description in cursor.description]
    entries = cursor.fetchall()
    results:list[dict[str,Any]] = []

    for row in entries:
        row_dict = dict(zip(entry_columns,row))
        results.append(row_dict)

    return results