# Native Stuff
import os,random,time
from collections import Counter
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
        self.database = self._Connect()
        self.cursor = self.database.cursor()
        self._side = None

    def _Connect(self):
        return psycopg2.connect(host=CFG.DB_HOST,port=CFG.DB_PORT,database=CFG.DB_NAME,user=CFG.DB_USR,password=CFG.DB_PASS)

    @property
    def side_cursor(self) -> psycopg2.extensions.cursor:
        """
        Cursor on a second, autocommit connection, for writes that must be visible to every
        other connection immediately without committing this connection's in-progress
        transaction -- see the new-user insert in Get_Messages (modules/Classes.py). Opened on
        first use, so connections that never need it don't hold an extra session.
        """
        if self._side is None:
            self._side = self._Connect()
            self._side.autocommit = True
            self._side_cursor = self._side.cursor()
        return self._side_cursor

    def Close(self):
        """
        LBA: Closes the database cursor and connection.
        """
        LOG.logger.debug('Closing SQL Database...')
        self.cursor.close()
        self.database.close()
        if self._side is not None:
            self._side.close()

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

def InsertEntriesRetryOnDeadlock(cursor:psycopg2.extensions.cursor,table:str,data_list:list[dict[str,Any]],conflict:str|None=None,max_attempts:int=5) -> None:
    """
    Same as InsertEntries, but retries on a deadlock instead of aborting the caller's whole
    transaction. Use this for tables where independent worker threads can race to insert
    overlapping keys within the same uncommitted transaction window -- e.g. emotes, which is
    shared across every video/worker of a channel (see the "deadlock detected ... emotes"
    incident this covers: two workers' batches each inserted the same handful of emotes for
    the channel in a different relative order, so their still-uncommitted transactions ended
    up wanting each other's row locks in opposite order). A savepoint scopes any rollback to
    just this insert instead of the whole transaction; retrying is safe since ON CONFLICT DO
    NOTHING makes the insert idempotent.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Target table
    :type table: String
    :param data_list: A list of entries to input into the target table.
    :type data_list: List of Dictionaries
    :param conflict: A column name that is either a Primary Key, or contains a Unique Constraint
    :type conflict: String
    :param max_attempts: Number of times to retry after a deadlock before giving up.
    :type max_attempts: Integer
    """
    if len(data_list) == 0:
        return

    for attempt in range(1,max_attempts + 1):
        cursor.execute("SAVEPOINT insert_entries_sp")
        try:
            InsertEntries(cursor,table,data_list,conflict)
            cursor.execute("RELEASE SAVEPOINT insert_entries_sp")
            return
        except psycopg2.errors.DeadlockDetected:
            cursor.execute("ROLLBACK TO SAVEPOINT insert_entries_sp")
            if attempt == max_attempts:
                raise
            LOG.logger.warning(f'InsertEntries deadlock on table {table} ({len(data_list)} row(s)), retrying (attempt {attempt}/{max_attempts})...')
            time.sleep(random.uniform(0.05,0.25) * attempt)

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

def RecordMessageStatsBatch(cursor:psycopg2.extensions.cursor,batch:list[tuple[str,str,str,float,bool,int|None,str,int,bool,bool,bool,float|None,str|None,int|None]]) -> list[tuple]:
    """
    Incrementally updates video_message_stats and user_channel_activity (and their
    dedup/first-message helper tables) for a batch of newly-inserted chat messages, in one round
    trip. Call this right before committing -- see _flush_pending_stats() in
    modules/Classes.py Get_Messages, which buffers one (channel_id, video_id, user_id, timestamp,
    is_member, member_status, message_id, emote_count, is_moderator, is_verified, is_owner,
    amount, currency, stream_week) tuple per newly-inserted message and flushes the whole buffer here.
    All of the counting logic lives server-side in public.record_message_stats_batch() /
    record_message_stats() (see sql/video_message_stats.sql and
    sql/user_channel_activity_stats.sql).

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param batch: List of (channel_id, video_id, user_id, timestamp, is_member, member_status,
        message_id, emote_count, is_moderator, is_verified, is_owner, amount, currency, stream_week)
        tuples, one per message, SORTED BY user_id by the caller -- see below for why.
    :type batch: list[tuple]

    record_message_stats() never touches a video_message_stats row for any video other than its
    own (see that function's docstring in sql/user_channel_activity_stats.sql, and
    RefreshFirstMessageCounts for where first_messages/first_channel_messages moved to), which
    closed one deadlock path. That alone wasn't enough, though: a worker's transaction spans up
    to 500 messages (one commit per batch), and each row here still takes a row lock on
    user_first_message/user_channel_activity for whichever user it's for, held until that
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

    :return: one row per message, (idx, *AudienceDeltas.RESULT_COLUMNS): each
        user_channel_activity row's old and new values, for modules/AudienceDeltas.batch_deltas().
    """
    if len(batch) == 0:
        return []

    (channel_ids,video_ids,user_ids,timestamps,is_members,member_statuses,
     message_ids,emote_counts,is_moderators,is_verifieds,is_owners,amounts,currencies,weeks) = zip(*batch)
    query = 'SELECT * FROM record_message_stats_batch(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::int[])'
    values = (
        list(channel_ids),list(video_ids),list(user_ids),list(timestamps),list(is_members),
        list(member_statuses),list(message_ids),list(emote_counts),list(is_moderators),
        list(is_verifieds),list(is_owners),list(amounts),list(currencies),list(weeks)
    )

    if CFG.DB_VERBOSE == True:
        LOG.logger.info(query)
        LOG.logger.info(values)

    max_attempts = 5
    for attempt in range(1,max_attempts + 1):
        cursor.execute("SAVEPOINT record_message_stats_sp")
        try:
            cursor.execute(query,values)
            rows = cursor.fetchall()
            cursor.execute("RELEASE SAVEPOINT record_message_stats_sp")
            return rows
        except psycopg2.errors.DeadlockDetected:
            cursor.execute("ROLLBACK TO SAVEPOINT record_message_stats_sp")
            if attempt == max_attempts:
                raise
            LOG.logger.warning(f'record_message_stats_batch deadlock for {len(batch)} message(s), retrying (attempt {attempt}/{max_attempts})...')
            time.sleep(random.uniform(0.05,0.25) * attempt)
    return []

def StageAudienceDeltas(cursor:psycopg2.extensions.cursor,d) -> None:
    """
    Writes one batch's audience deltas (an AudienceDeltas.BatchDeltas) to the insert-only
    staging tables. Call it in the SAME transaction as the RecordMessageStatsBatch() call they
    came from, so a rolled-back batch takes its deltas with it -- that's what keeps the rollups
    exact. Insert-only on purpose: no shared counter rows for the parallel workers to lock.
    FoldAudience() moves them into the rollups later.
    """
    from psycopg2.extras import execute_values
    if d.channel:
        execute_values(cursor,'INSERT INTO audience_delta (channel_id,dim,bucket,delta) VALUES %s',
                       [(c,dim,b,v) for (c,dim,b),v in d.channel.items()])
    if d.month or d.reg_month:
        cols = {"arrivals":0,"last_seen":1,"regulars":2,"active":3,"returning":4,"reg_returning":5}
        months:dict[tuple,list[int]] = {}
        for counter in (d.month,d.reg_month):
            for (c,m,kind),v in counter.items():
                months.setdefault((c,m),[0] * len(cols))[cols[kind]] += v
        execute_values(cursor,'INSERT INTO audience_month_delta (channel_id,"month",arrivals,last_seen,regulars,active,"returning",reg_returning) VALUES %s',
                       [(c,m,*v) for (c,m),v in months.items()])
    if d.retention:
        execute_values(cursor,'INSERT INTO audience_retention_delta (channel_id,kind,cohort,age,n) VALUES %s',
                       [(c,k,co,a,v) for (c,k,co,a),v in d.retention.items()])
    if d.activity:
        import modules.AudienceDeltas as AD
        execute_values(cursor,'INSERT INTO audience_activity_change (channel_id,user_id,added_active,added_regular) VALUES %s',
                       [(c,u,sorted(AD.month_index(m) for m in a),sorted(AD.month_index(m) for m in rg))
                        for (c,u),(a,rg) in d.activity.items()],template='(%s,%s,%s::smallint[],%s::smallint[])')
    if d.reg_year:
        years:dict[tuple,list[int]] = {}
        for (c,y,kind),v in d.reg_year.items():
            years.setdefault((c,y),[0,0])[0 if kind == "regulars" else 1] += v
        execute_values(cursor,'INSERT INTO audience_year_delta (channel_id,"year",regulars,active) VALUES %s',
                       [(c,y,r,a) for (c,y),(r,a) in years.items()])
    if d.reg_changes:
        execute_values(cursor,'INSERT INTO audience_regular_change (channel_id,user_id,old_years,new_years) VALUES %s',
                       [(c,u,sorted(o),sorted(n)) for (c,u),(o,n) in d.reg_changes.items()])
    if d.video:
        vids:dict[str,list[int]] = {}
        for (v,kind),n in d.video.items():
            vids.setdefault(v,[0,0])[0 if kind == "first_global" else 1] += n
        execute_values(cursor,'INSERT INTO audience_video_delta (video_id,first_global,first_channel) VALUES %s',
                       [(v,g,c) for v,(g,c) in vids.items()])
    if d.links:
        execute_values(cursor,'INSERT INTO audience_new_link (channel_id,user_id,is_new,o_first) VALUES %s',
                       [(c,u,n,o) for (c,u),(n,o) in d.links.items()])
    if d.top:
        execute_values(cursor,'INSERT INTO audience_top_candidate (channel_id,user_id,total_messages) VALUES %s',
                       [(c,u,t) for (c,u),t in d.top.items()])

TOP_CHATTERS = 100

# Distinct-per-scope retention needs each changed user's other user_channel_activity rows in the
# fold (about 5-6x the users the Community fold reads). False = per-channel retention only, and
# scope_retention goes stale (rebuild it with build_audience_baseline.py before re-enabling).
SCOPE_RETENTION = True

def GetTopThreshold(cursor:psycopg2.extensions.cursor,channel_id:str) -> int:
    """
    A channel's current #TOP_CHATTERS message total in channel_top_chatters (0 while it has fewer
    rows). Totals only grow, so a chatter can only enter the top list by reaching this -- the
    ingest path stages just those as candidates. Reads at most TOP_CHATTERS rows.
    """
    cursor.execute('SELECT CASE WHEN count(*) >= %s THEN min(total_messages) ELSE 0 END '
                   'FROM channel_top_chatters WHERE channel_id = %s',(TOP_CHATTERS,channel_id))
    return int(cursor.fetchone()[0])

def FoldAudience(connection) -> dict[str,int]:
    """
    Moves everything staged by StageAudienceDeltas() into the audience rollups, in one
    transaction (sql/audience_rollups.sql). Main.py calls it after each channel, at the start of
    a run (whatever a killed run left staged) and at the end.

    Must run while no ingestion workers are writing -- it reads the newly-linked users' current
    user_channel_activity rows and treats every row not in this fold's staging as already
    counted. Small reads only: the staged rows, plus those users' rows via
    idx_user_channel_activity_user_id.
    """
    import modules.AudienceDeltas as AD
    from psycopg2.extras import execute_values
    cur = connection.cursor()
    connection.commit()
    cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    counts = {}

    cur.execute('''
        WITH moved AS (DELETE FROM audience_delta RETURNING *)
        INSERT INTO channel_audience (channel_id,dim,bucket,n)
        SELECT channel_id,dim,bucket,sum(delta) FROM moved GROUP BY 1,2,3
        ON CONFLICT (channel_id,dim,bucket) DO UPDATE SET n = channel_audience.n + EXCLUDED.n''')
    counts["channel_audience"] = cur.rowcount
    cur.execute('''
        WITH moved AS (DELETE FROM audience_month_delta RETURNING *)
        INSERT INTO channel_audience_monthly (channel_id,"month",arrivals,last_seen,regulars,active,"returning",reg_returning)
        SELECT channel_id,"month",sum(arrivals),sum(last_seen),sum(regulars),sum(active),sum("returning"),sum(reg_returning)
        FROM moved GROUP BY 1,2
        ON CONFLICT (channel_id,"month") DO UPDATE SET arrivals = channel_audience_monthly.arrivals + EXCLUDED.arrivals,
                                                     last_seen = channel_audience_monthly.last_seen + EXCLUDED.last_seen,
                                                     regulars = channel_audience_monthly.regulars + EXCLUDED.regulars,
                                                     active = channel_audience_monthly.active + EXCLUDED.active,
                                                     "returning" = channel_audience_monthly."returning" + EXCLUDED."returning",
                                                     reg_returning = channel_audience_monthly.reg_returning + EXCLUDED.reg_returning''')
    counts["channel_audience_monthly"] = cur.rowcount
    cur.execute('''
        WITH moved AS (DELETE FROM audience_retention_delta RETURNING *)
        INSERT INTO channel_retention (channel_id,kind,cohort,age,n)
        SELECT channel_id,kind,cohort,age,sum(n) FROM moved GROUP BY 1,2,3,4 HAVING sum(n) <> 0
        ON CONFLICT (channel_id,kind,cohort,age) DO UPDATE SET n = channel_retention.n + EXCLUDED.n''')
    counts["channel_retention"] = cur.rowcount
    cur.execute('''
        WITH moved AS (DELETE FROM audience_year_delta RETURNING *)
        INSERT INTO channel_audience_yearly (channel_id,"year",regulars,active)
        SELECT channel_id,"year",sum(regulars),sum(active) FROM moved GROUP BY 1,2
        ON CONFLICT (channel_id,"year") DO UPDATE SET regulars = channel_audience_yearly.regulars + EXCLUDED.regulars,
                                                    active = channel_audience_yearly.active + EXCLUDED.active''')
    counts["channel_audience_yearly"] = cur.rowcount
    cur.execute('''
        WITH moved AS (DELETE FROM audience_video_delta RETURNING *),
             agg AS (SELECT video_id,sum(first_global) AS g,sum(first_channel) AS c FROM moved GROUP BY 1)
        UPDATE video_message_stats v SET first_messages = v.first_messages + agg.g,
                                         first_channel_messages = v.first_channel_messages + agg.c
        FROM agg WHERE v.video_id = agg.video_id AND (agg.g <> 0 OR agg.c <> 0)''')
    counts["video_firsts"] = cur.rowcount
    cur.execute('''
        WITH moved AS (DELETE FROM audience_top_candidate RETURNING *)
        INSERT INTO channel_top_chatters (channel_id,user_id,total_messages)
        SELECT channel_id,user_id,max(total_messages) FROM moved GROUP BY 1,2
        ON CONFLICT (channel_id,user_id) DO UPDATE
        SET total_messages = GREATEST(channel_top_chatters.total_messages,EXCLUDED.total_messages)
        RETURNING channel_id''')
    touched_channels = sorted({r[0] for r in cur.fetchall()})
    if touched_channels:
        cur.execute('''
            DELETE FROM channel_top_chatters t USING (
                SELECT channel_id,user_id,row_number() OVER (PARTITION BY channel_id ORDER BY total_messages DESC,user_id) AS rn
                FROM channel_top_chatters WHERE channel_id = ANY(%s)) r
            WHERE t.channel_id = r.channel_id AND t.user_id = r.user_id AND r.rn > %s''',(touched_channels,TOP_CHATTERS))

    # Community: users new to a channel (or whose first message there moved to an earlier month).
    cur.execute('DELETE FROM audience_new_link RETURNING channel_id,user_id,is_new,o_first')
    links:dict[str,dict[str,tuple[bool,float|None]]] = {}
    for channel_id,user_id,is_new,o_first in cur.fetchall():
        was_new,prev = links.setdefault(user_id,{}).get(channel_id,(False,None))
        if is_new or was_new:
            links[user_id][channel_id] = (True,None)
        else:
            links[user_id][channel_id] = (False,o_first if prev is None else max(prev,o_first))
    counts["links"] = sum(len(v) for v in links.values())

    # Regulars: users whose regular years on a channel grew. Bits only ever get set, so across
    # several staged events the smallest "old" set is the true before and the largest "new" the after.
    cur.execute('DELETE FROM audience_regular_change RETURNING channel_id,user_id,old_years,new_years')
    reg:dict[str,dict[str,set[int]]] = {}
    for channel_id,user_id,old_years,new_years in cur.fetchall():
        prev = reg.setdefault(user_id,{}).get(channel_id)
        old = set(old_years or [])
        reg[user_id][channel_id] = old if prev is None or len(old) < len(prev) else prev
    counts["regular_changes"] = sum(len(v) for v in reg.values())

    # Retention per scope: months newly active / regular on a channel. Months only get added, so a
    # channel's old sets = its current sets minus the union of everything staged for it.
    cur.execute('DELETE FROM audience_activity_change RETURNING channel_id,user_id,added_active,added_regular')
    act:dict[str,dict[str,tuple[set,set]]] = {}
    for channel_id,user_id,added_a,added_r in cur.fetchall():
        a,rg = act.setdefault(user_id,{}).setdefault(channel_id,(set(),set()))
        a.update(AD.month_at(i) for i in added_a or [])
        rg.update(AD.month_at(i) for i in added_r or [])
    counts["activity_changes"] = sum(len(v) for v in act.values())
    if not SCOPE_RETENTION:
        act = {}

    if links or reg or act:
        cur.execute('SELECT user_id,channel_type,branch,"group" FROM channel_directory')
        scopes = AD.channel_scopes(cur.fetchall())
        pairs,scope_k,scope_month = Counter(),Counter(),Counter()
        rpairs,rscopes = Counter(),Counter()
        sret,smix = Counter(),Counter()
        users = list(set(links) | set(reg) | set(act))
        for i in range(0,len(users),5000):
            chunk = users[i:i + 5000]
            cur.execute('SELECT user_id,channel_id,"timestamp",active_weeks::text FROM user_channel_activity WHERE user_id = ANY(%s)',(chunk,))
            after:dict[str,dict[str,float]] = {}
            reg_after:dict[str,dict[str,set[int]]] = {}
            months_after:dict[str,dict[str,tuple[set,set]]] = {}
            for user_id,channel_id,first,weeks in cur.fetchall():
                after.setdefault(user_id,{})[channel_id] = first
                reg_m = AD.regular_months(weeks)
                reg_after.setdefault(user_id,{})[channel_id] = AD.years_of(reg_m)
                if user_id in act:
                    months_after.setdefault(user_id,{})[channel_id] = (AD.active_months(weeks),reg_m)
            for user_id in chunk:
                if user_id in links:
                    now = after.get(user_id,{})
                    before = dict(now)
                    for channel_id,(is_new,o_first) in links[user_id].items():
                        if is_new:
                            before.pop(channel_id,None)
                        elif o_first is not None and channel_id in before:
                            before[channel_id] = o_first
                    p,k,m = AD.community_deltas(before,now,scopes)
                    pairs.update(p); scope_k.update(k); scope_month.update(m)
                if user_id in reg:
                    r_now = reg_after.get(user_id,{})
                    r_before = dict(r_now)
                    r_before.update(reg[user_id])
                    p,sc = AD.regular_community_deltas(r_before,r_now,scopes)
                    rpairs.update(p); rscopes.update(sc)
                if user_id in act:
                    m_now = months_after.get(user_id,{})
                    m_before = dict(m_now)
                    for channel_id,(added_a,added_r) in act[user_id].items():
                        a,rg = m_now.get(channel_id,(set(),set()))
                        m_before[channel_id] = (a - added_a,rg - added_r)
                    r_,m_ = AD.scope_retention_deltas(m_before,m_now,scopes)
                    sret.update(r_); smix.update(m_)
        if sret:
            execute_values(cur,'''INSERT INTO scope_retention ("scope",kind,cohort,age,n) VALUES %s
                ON CONFLICT ("scope",kind,cohort,age) DO UPDATE SET n = scope_retention.n + EXCLUDED.n''',
                [(s_,k,c,a,n) for (s_,k,c,a),n in sret.items() if n])
        if smix:
            mix:dict[tuple,list[int]] = {}
            for (s_,m,col),n in smix.items():
                mix.setdefault((s_,m),[0,0])[0 if col == "returning" else 1] += n
            execute_values(cur,'''INSERT INTO scope_audience_monthly ("scope","month","returning",reg_returning) VALUES %s
                ON CONFLICT ("scope","month") DO UPDATE SET "returning" = scope_audience_monthly."returning" + EXCLUDED."returning",
                                                          reg_returning = scope_audience_monthly.reg_returning + EXCLUDED.reg_returning''',
                [(s_,m,a,b) for (s_,m),(a,b) in mix.items() if a or b])
        counts["scope_retention"] = len(sret)
        if rpairs:
            execute_values(cur,'''INSERT INTO regular_overlap (channel_a,channel_b,"year",shared) VALUES %s
                ON CONFLICT (channel_a,channel_b,"year") DO UPDATE SET shared = regular_overlap.shared + EXCLUDED.shared''',
                [(a,b,y,n) for (a,b,y),n in rpairs.items() if n])
        if rscopes:
            execute_values(cur,'''INSERT INTO scope_regulars ("scope","year",n) VALUES %s
                ON CONFLICT ("scope","year") DO UPDATE SET n = scope_regulars.n + EXCLUDED.n''',
                [(s,y,n) for (s,y),n in rscopes.items() if n])
        counts["regular_pairs"] = len(rpairs)
        if pairs:
            execute_values(cur,'''INSERT INTO channel_overlap (channel_a,channel_b,shared) VALUES %s
                ON CONFLICT (channel_a,channel_b) DO UPDATE SET shared = channel_overlap.shared + EXCLUDED.shared''',
                [(a,b,n) for (a,b),n in pairs.items() if n])
        if scope_k:
            execute_values(cur,'''INSERT INTO scope_audience ("scope",dim,bucket,n) VALUES %s
                ON CONFLICT ("scope",dim,bucket) DO UPDATE SET n = scope_audience.n + EXCLUDED.n''',
                [(s,"k",str(k),n) for (s,k),n in scope_k.items() if n])
        if scope_month:
            execute_values(cur,'''INSERT INTO scope_audience_monthly ("scope","month",arrivals) VALUES %s
                ON CONFLICT ("scope","month") DO UPDATE SET arrivals = scope_audience_monthly.arrivals + EXCLUDED.arrivals''',
                [(s,m,n) for (s,m),n in scope_month.items() if n])
        counts["overlap_pairs"] = len(pairs)
    connection.commit()
    return counts

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

def ReplaceVideoTags(cursor:psycopg2.extensions.cursor,table:str,video_id:str,channel_id:str,tags:list[str]|None) -> None:
    """
    Replaces a video's rows in the tags table with the tags YouTube currently reports for it, so
    tags a creator removed don't linger. Call it in the same transaction as the video's own
    INSERT/UPDATE (tags FKs to videos), with tags from a fresh videos().list response -- there, a
    missing snippet.tags means the video has no tags, so None clears them.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: The tags table (CFG.DB_TABLES["tags"]).
    :type table: String
    :param video_id: The video whose tags to replace.
    :type video_id: String
    :param channel_id: The video's channel.
    :type channel_id: String
    :param tags: snippet.tags from the API, or None if it had none.
    :type tags: List of strings or None
    """
    cursor.execute(f'DELETE FROM {table} WHERE video_id = %s',(video_id,))
    # YouTube can return the same tag twice; the primary key would reject the duplicate.
    unique_tags = list(dict.fromkeys(tags or []))
    if unique_tags:
        InsertEntries(cursor,table,[{"video_id":video_id,"channel_id":channel_id,"tag_name":tag} for tag in unique_tags])

def RecordSubtitleStats(cursor:psycopg2.extensions.cursor,video_id:str,channel_id:str,language:str,total_subtitles:int,total_words:int,speech_duration_ms:int) -> None:
    """
    Upserts video_subtitle_stats for one video/language from precomputed totals. Call this once
    per video/language right after its cues are written -- see Get_Subtitles in modules/Classes.py,
    which already has the parsed cue list in memory and passes its totals straight through rather
    than reading them back from the subtitles table.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param video_id: The video these subtitles belong to.
    :type video_id: String
    :param channel_id: The channel the video belongs to (CFG.YT_USER_ID).
    :type channel_id: String
    :param language: Caption language code (yt-dlp subtitleslangs entry).
    :type language: String
    :param total_subtitles: Number of cues parsed for this video/language.
    :type total_subtitles: Integer
    :param total_words: Total word count across those cues.
    :type total_words: Integer
    :param speech_duration_ms: Summed (end_ms - start_ms) across those cues.
    :type speech_duration_ms: Integer
    """
    query = 'SELECT record_subtitle_stats(%s,%s,%s,%s,%s,%s)'
    values = (video_id,channel_id,language,total_subtitles,total_words,speech_duration_ms)

    if CFG.DB_VERBOSE == True:
        LOG.logger.info(query)
        LOG.logger.info(values)

    cursor.execute(query,values)

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

# Per-channel members-only chat tables, created on demand by EnsureMembersTables(). Same
# definitions as the existing hand-made *_<talent>_members tables in sql/*_members.sql (those
# files are now reference only). {s} is the channel's db_suffix; every name built from it is
# passed through sql.Identifier. Order matters: nickname_matches FKs to messages.
_MEMBERS_TABLE_DDL:list[tuple[str,str]] = [
    ("messages_{s}_members","""
        CREATE TABLE IF NOT EXISTS public.{t} (
            message_id text NOT NULL,
            message text NULL,
            "timestamp" int8 NULL,
            time_in_seconds float4 NULL,
            "type" text NULL,
            video_id text NULL,
            user_id text NULL,
            user_name text NULL,
            user_member_status int8 NULL,
            ismoderator bool DEFAULT false NOT NULL,
            isverified bool DEFAULT false NOT NULL,
            isowner bool DEFAULT false NOT NULL,
            amount float4 NULL,
            currency text NULL,
            symbol text NULL,
            color text NULL,
            datetime timestamp GENERATED ALWAYS AS (
                CASE
                    WHEN ("timestamp" > '1000000000000000'::bigint) THEN to_timestamp((("timestamp" / 1000000))::double precision)
                    ELSE to_timestamp(("timestamp")::double precision)
                END) STORED NULL,
            CONSTRAINT {pk} PRIMARY KEY (message_id),
            CONSTRAINT {fk_user} FOREIGN KEY (user_id) REFERENCES public.user_ids(id),
            CONSTRAINT {fk_video} FOREIGN KEY (video_id) REFERENCES public.videos(id)
        );
        CREATE INDEX IF NOT EXISTS {idx_user} ON public.{t} USING btree (user_id);
        CREATE INDEX IF NOT EXISTS {idx_video} ON public.{t} USING btree (video_id);
    """),
    ("emotes_{s}_members","""
        CREATE TABLE IF NOT EXISTS public.{t} (
            id text NOT NULL,
            "name" text NOT NULL,
            url text NULL,
            custom bool NOT NULL,
            CONSTRAINT {pk} UNIQUE (id)
        );
    """),
    ("nickname_matches_{s}_members","""
        CREATE TABLE IF NOT EXISTS public.{t} (
            matched_nickname text NULL,
            message_id text NULL,
            index_start int8 NULL,
            index_end int8 NULL,
            CONSTRAINT {pk} UNIQUE (message_id, index_start, index_end),
            CONSTRAINT {fk_msg} FOREIGN KEY (message_id) REFERENCES public.{messages}(message_id)
        );
        CREATE INDEX IF NOT EXISTS {idx_msg} ON public.{t} USING btree (message_id);
    """),
    ("subtitles_{s}_members","""
        CREATE TABLE IF NOT EXISTS public.{t} (
            video_id text NOT NULL,
            language text DEFAULT 'en'::text NOT NULL,
            seq int4 NOT NULL,
            start_ms int8 NOT NULL,
            end_ms int8 NOT NULL,
            text text NOT NULL,
            CONSTRAINT {pk} PRIMARY KEY (video_id, language, seq),
            CONSTRAINT {fk_video} FOREIGN KEY (video_id) REFERENCES public.videos(id)
        );
    """),
]

def EnsureMembersTables(cursor:psycopg2.extensions.cursor,db_suffix:str) -> None:
    """
    Creates a channel's members-only chat tables (messages_, emotes_, nickname_matches_ and
    subtitles_<db_suffix>_members) if they don't already exist -- what a members chat pass
    (CFG.GET_MEMBERS_CHAT) writes to. Called per channel before processing it, so flagging a new
    channel members_chat in channel_directory is all that's needed. Caller is responsible for committing.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param db_suffix: The channel's channel_directory.db_suffix.
    :type db_suffix: String
    """
    messages_table = f"messages_{db_suffix}_members"
    for name_template,ddl in _MEMBERS_TABLE_DDL:
        table = name_template.format(s=db_suffix)
        if TableExists(cursor,table):
            continue
        query = sql.SQL(ddl).format(
            t=sql.Identifier(table),
            messages=sql.Identifier(messages_table),
            pk=sql.Identifier(f"pk_{table}"),
            fk_user=sql.Identifier(f"fk_{table}_user"),
            fk_video=sql.Identifier(f"fk_{table}_video"),
            fk_msg=sql.Identifier(f"fk_{table}_msg"),
            idx_user=sql.Identifier(f"idx_{table}_user"),
            idx_video=sql.Identifier(f"idx_{table}_video"),
            idx_msg=sql.Identifier(f"idx_{table}_msg"),
        )
        if CFG.DB_VERBOSE == True:
            LOG.logger.info(query.as_string(cursor))
        try:
            cursor.execute(query)
            LOG.logger.info(f'Created missing members-only table {table}.')
        except Exception as e:
            LOG.logger.error(f'Failed to create members-only table {table}: {e}')
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
    ("video_subtitle_stats","video_subtitle_stats"),
    ("user_channel_activity_stats","user_channel_activity"),
    ("video_message_stats","video_message_stats"),
    ("user_channel_superchats","user_channel_superchats"),
    ("audience_rollups","channel_audience"),
    ("channel_video_summary","channel_video_summary"),
    ("processed_status","processed_status"),
    ("chat_archive","chat_archive"),
]

_SQL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"sql")

def EnsureSchema(cursor:psycopg2.extensions.cursor) -> None:
    """
    Verifies the database structure this bot needs exists, and builds whatever's missing from
    the DDL scripts in sql/ -- lets a fresh, empty database bootstrap itself on first run
    instead of requiring the scripts to be applied by hand first. Safe to call on every
    startup: each file is only applied if its anchor table/matview (see _CORE_SQL_FILES)
    doesn't already exist, so an already-provisioned database is a no-op.

    The `*_members` files aren't applied -- members-only chat tables are created per channel
    by EnsureMembersTables() when a members chat pass reaches it. Caller is responsible for
    committing.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    """
    for file_name,anchor_table in _CORE_SQL_FILES:
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

def AddChannel(cursor:psycopg2.extensions.cursor,name:str,user_id:str,db_suffix:str,group:str,branch:str,active:bool,debut:str,process:bool,channel_type:str='talent') -> None:
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
    :param branch: Talent's branch/sub-agency. Must already exist in the branches table.
    :type branch: String
    :param active: Whether the channel is currently active.
    :type active: Boolean
    :param debut: Channel's debut date, ISO format (YYYY-MM-DD).
    :type debut: String
    :param process: Whether this channel should be included when CFG.PROCESS_ALL is True.
    :type process: Boolean
    :param channel_type: 'talent' for a talent's own channel, 'official' for a branch or unit channel.
    :type channel_type: String
    """
    try:
        insert_query = 'INSERT INTO channel_directory (name, user_id, db_suffix, "group", branch, active, debut, process, channel_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)'
        insert_values = (name,user_id,db_suffix,group,branch,active,debut,process,channel_type)

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

def GetVideosNeedingSubtitles(cursor:psycopg2.extensions.cursor,videos_table:str,channel_id:str,members:bool) -> list[dict[str,Any]]:
    """
    Videos for a channel that don't have subtitles yet and aren't currently live. Custom SQL
    rather than GetEntries() -- GetEntries only ANDs plain equality filters, which can't
    express "livestream IS NOT TRUE" (must include NULL rows for older/edge-case videos where
    livestream was never set -- the column is nullable, and existing code always guards with
    != True, never == False, for this reason).

    Returns id + end_time (not just id) so the caller can tell a video that JUST stopped being
    live (auto-captions may not be finalized yet -- see CFG.SUBTITLE_GRACE_HOURS) apart from
    one that's been done processing for a while.

    :param cursor: Database cursor object to execute commands.
    :param videos_table: CFG.DB_TABLES["videos"].
    :param channel_id: CFG.YT_USER_ID for the channel being processed.
    :param members: CFG.GET_MEMBERS_CHAT -- videos.members distinguishes public vs
        members-only rows within the single unified videos table.
    :return: List of {"id":..., "end_time":...} dicts.
    """
    query = f'SELECT id,end_time FROM {videos_table} WHERE channel_id = %s AND members = %s AND subtitles_processed = false AND livestream IS NOT TRUE'
    if CFG.DB_VERBOSE == True:
        LOG.logger.info(query)
    cursor.execute(query,(channel_id,members))
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns,row)) for row in cursor.fetchall()]

def RecordSubtitleThrottle(cursor:psycopg2.extensions.cursor,videos_table:str,video_id:str,give_up_after:int) -> bool:
    """
    Increments videos.subtitles_fail_count for a video whose subtitle download exhausted every
    retry this run with throttling (see SubtitleThrottled in Classes.py). Once give_up_after
    separate runs have each ended in a throttle for this video, flips subtitles_processed to
    true so it stops being re-queued forever -- some videos get a real, consistent 429 from
    YouTube's caption endpoint no matter how the request is paced (seen on a YouTube Short),
    which no amount of retrying within a run will ever get past.

    :param cursor: Database cursor object to execute commands.
    :param videos_table: CFG.DB_TABLES["videos"].
    :param video_id: ID of the video that was just throttled.
    :param give_up_after: CFG.SUBTITLE_GIVEUP_AFTER_RUNS.
    :return: True if this call tipped the video over the threshold and gave up on it permanently.
    """
    query = f'UPDATE {videos_table} SET subtitles_fail_count = subtitles_fail_count + 1 WHERE id = %s RETURNING subtitles_fail_count'
    if CFG.DB_VERBOSE == True:
        LOG.logger.info(query)
    cursor.execute(query,(video_id,))
    fail_count = cursor.fetchone()[0]
    gave_up = fail_count >= give_up_after
    if gave_up:
        cursor.execute(f'UPDATE {videos_table} SET subtitles_processed = true WHERE id = %s',(video_id,))
    return gave_up