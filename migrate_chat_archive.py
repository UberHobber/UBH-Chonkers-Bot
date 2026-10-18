# Native Stuff
import time

# Other Project Files
import modules.logconfig as LOG
import modules.Database as DB

"""
One-time migration: prepares the schema for moving processed videos' chat out of Postgres and
into Parquet on S3 (archive_chat.py), and for compressing the raw chat JSON on S3 while
correcting historical badge flags from it (compress_raw_chat.py).

1. videos.raw_chat_compressed / videos.chat_archived -- per-video progress markers for the two
   scripts above (archive_chat.py only archives videos whose raw chat pass is done).
2. badge_fix_pending -- (channel_id, user_id) pairs whose messages had moderator/verified/owner
   flags corrected, and whose user_channel_activity ever_*/last_is_* still need recomputing.
3. chat_archive -- manifest of which video's chat lives in which Parquet file.
4. Pointer tables learn their video: user_first_message.video_id and
   user_channel_activity.first_video_id, backfilled from messages, then the composite FKs into
   messages(channel_id, message_id) are dropped. Those FKs (NO ACTION) made any user's first
   message undeletable, and refresh_first_message_counts() only joined messages to recover
   video_id -- it now groups by the stored column instead, so it keeps working once the
   pointed-to messages live in Parquet.
5. record_message_stats() writes the new pointer columns.

Run with the bot stopped (the FK drop and VACUUM FULL lock the pointer tables). Idempotent --
safe to re-run; the backfill resumes per channel.

Once this has run, regenerate sql/ and SKILL.MD:
    python .claude/skills/chat-database/update_schema.py
"""

_ADD_COLUMNS_AND_TABLES = """
    ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS raw_chat_compressed bool DEFAULT false NOT NULL;
    ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS chat_archived bool DEFAULT false NOT NULL;

    ALTER TABLE public.user_first_message ADD COLUMN IF NOT EXISTS video_id text NULL;
    ALTER TABLE public.user_channel_activity ADD COLUMN IF NOT EXISTS first_video_id text NULL;

    CREATE TABLE IF NOT EXISTS public.badge_fix_pending (
        channel_id text NOT NULL,
        user_id text NOT NULL,
        CONSTRAINT badge_fix_pending_pkey PRIMARY KEY (channel_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS public.chat_archive (
        video_id text NOT NULL,
        channel_id text NOT NULL,
        file_key text NOT NULL,
        message_count int8 NOT NULL,
        min_ts int8 NULL,
        max_ts int8 NULL,
        archived_at timestamp DEFAULT now() NOT NULL,
        CONSTRAINT chat_archive_pkey PRIMARY KEY (video_id),
        CONSTRAINT chat_archive_video_id_fkey FOREIGN KEY (video_id) REFERENCES public.videos(id),
        CONSTRAINT chat_archive_channel_directory_fk FOREIGN KEY (channel_id) REFERENCES public.channel_directory(user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_chat_archive_channel_file ON public.chat_archive USING btree (channel_id, file_key);
"""

# Same as before, plus: user_first_message.video_id moves together with message_id, and
# user_channel_activity.first_video_id together with first_message_id (same CASE condition).
_RECORD_MESSAGE_STATS = """
CREATE OR REPLACE FUNCTION public.record_message_stats(p_channel_id text, p_video_id text, p_user_id text, p_timestamp double precision, p_is_member boolean, p_member_status bigint, p_message_id text, p_emote_count integer, p_is_moderator boolean, p_is_verified boolean, p_is_owner boolean, p_amount double precision, p_currency text)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
        DECLARE
            v_prev_is_member boolean;
            v_was_new_user boolean;
        BEGIN
            INSERT INTO public.video_message_stats (video_id) VALUES (p_video_id)
                ON CONFLICT (video_id) DO NOTHING;

            UPDATE public.video_message_stats
            SET total_messages = total_messages + 1,
                messages_nonmembers = messages_nonmembers + (CASE WHEN p_is_member THEN 0 ELSE 1 END),
                messages_members = messages_members + (CASE WHEN p_is_member THEN 1 ELSE 0 END)
            WHERE video_id = p_video_id;

            SELECT is_member INTO v_prev_is_member
            FROM public.video_user_seen
            WHERE video_id = p_video_id AND user_id = p_user_id
            FOR UPDATE;

            v_was_new_user := NOT FOUND;

            IF v_was_new_user THEN
                INSERT INTO public.video_user_seen (video_id, user_id, is_member)
                VALUES (p_video_id, p_user_id, p_is_member);

                UPDATE public.video_message_stats
                SET unique_users = unique_users + 1,
                    unique_members = unique_members + (CASE WHEN p_is_member THEN 1 ELSE 0 END),
                    unique_nonmembers = unique_nonmembers + (CASE WHEN p_is_member THEN 0 ELSE 1 END)
                WHERE video_id = p_video_id;
            ELSIF p_is_member AND NOT v_prev_is_member THEN
                UPDATE public.video_user_seen SET is_member = true
                WHERE video_id = p_video_id AND user_id = p_user_id;

                UPDATE public.video_message_stats
                SET unique_members = unique_members + 1,
                    unique_nonmembers = unique_nonmembers - 1
                WHERE video_id = p_video_id;
            END IF;

            INSERT INTO public.user_first_message (user_id, channel_id, message_id, video_id, "timestamp")
            VALUES (p_user_id, p_channel_id, p_message_id, p_video_id, p_timestamp)
            ON CONFLICT (user_id) DO UPDATE
            SET channel_id = EXCLUDED.channel_id,
                message_id = EXCLUDED.message_id,
                video_id = EXCLUDED.video_id,
                "timestamp" = EXCLUDED."timestamp"
            WHERE EXCLUDED."timestamp" < public.user_first_message."timestamp";

            INSERT INTO public.user_channel_activity
                (channel_id, user_id, first_message_id, first_video_id, "timestamp", total_messages, last_message_ts,
                 max_member_status, emote_count, ever_moderator, ever_verified, ever_owner,
                 last_is_moderator, last_is_verified, last_is_owner)
            VALUES (p_channel_id, p_user_id, p_message_id, p_video_id, p_timestamp, 1, p_timestamp, p_member_status,
                    p_emote_count, p_is_moderator, p_is_verified, p_is_owner,
                    p_is_moderator, p_is_verified, p_is_owner)
            ON CONFLICT (channel_id, user_id) DO UPDATE
            SET total_messages = public.user_channel_activity.total_messages + 1,
                last_message_ts = GREATEST(public.user_channel_activity.last_message_ts, EXCLUDED.last_message_ts),
                max_member_status = GREATEST(public.user_channel_activity.max_member_status, EXCLUDED.max_member_status),
                emote_count = public.user_channel_activity.emote_count + EXCLUDED.emote_count,
                ever_moderator = public.user_channel_activity.ever_moderator OR EXCLUDED.ever_moderator,
                ever_verified = public.user_channel_activity.ever_verified OR EXCLUDED.ever_verified,
                ever_owner = public.user_channel_activity.ever_owner OR EXCLUDED.ever_owner,
                last_is_moderator = CASE WHEN EXCLUDED."timestamp" >= public.user_channel_activity.last_message_ts
                    THEN EXCLUDED.last_is_moderator ELSE public.user_channel_activity.last_is_moderator END,
                last_is_verified = CASE WHEN EXCLUDED."timestamp" >= public.user_channel_activity.last_message_ts
                    THEN EXCLUDED.last_is_verified ELSE public.user_channel_activity.last_is_verified END,
                last_is_owner = CASE WHEN EXCLUDED."timestamp" >= public.user_channel_activity.last_message_ts
                    THEN EXCLUDED.last_is_owner ELSE public.user_channel_activity.last_is_owner END,
                first_message_id = CASE WHEN EXCLUDED."timestamp" < public.user_channel_activity."timestamp"
                    THEN EXCLUDED.first_message_id ELSE public.user_channel_activity.first_message_id END,
                first_video_id = CASE WHEN EXCLUDED."timestamp" < public.user_channel_activity."timestamp"
                    THEN EXCLUDED.first_video_id ELSE public.user_channel_activity.first_video_id END,
                "timestamp" = LEAST(public.user_channel_activity."timestamp", EXCLUDED."timestamp");

            IF p_amount IS NOT NULL THEN
                INSERT INTO public.user_channel_superchats (channel_id, user_id, currency, superchat_count, total_amount)
                VALUES (p_channel_id, p_user_id, COALESCE(p_currency, 'UNKNOWN'), 1, p_amount)
                ON CONFLICT (channel_id, user_id, currency) DO UPDATE
                SET superchat_count = public.user_channel_superchats.superchat_count + 1,
                    total_amount = public.user_channel_superchats.total_amount + EXCLUDED.total_amount;
            END IF;
        END;
        $function$;
"""

# Groups by the pointer tables' own video columns instead of joining messages (whose rows for
# archived videos are no longer in Postgres).
_REFRESH_FIRST_MESSAGE_COUNTS = """
CREATE OR REPLACE FUNCTION public.refresh_first_message_counts()
 RETURNS void
 LANGUAGE sql
AS $function$
            UPDATE public.video_message_stats SET first_messages = 0, first_channel_messages = 0
            WHERE first_messages != 0 OR first_channel_messages != 0;

            UPDATE public.video_message_stats vms
            SET first_messages = fg.cnt
            FROM (
                SELECT video_id, COUNT(*) AS cnt
                FROM public.user_first_message
                GROUP BY video_id
            ) fg
            WHERE fg.video_id = vms.video_id;

            UPDATE public.video_message_stats vms
            SET first_channel_messages = fc.cnt
            FROM (
                SELECT first_video_id AS video_id, COUNT(*) AS cnt
                FROM public.user_channel_activity
                GROUP BY first_video_id
            ) fc
            WHERE fc.video_id = vms.video_id;
        $function$;
"""

# Partition per channel, so the backfill can run (and commit/resume) one channel at a time.
_PARTITIONS = """
    SELECT cd.user_id, 'messages_' || cd.db_suffix
    FROM channel_directory cd
    WHERE to_regclass('public.messages_' || cd.db_suffix) IS NOT NULL
    ORDER BY cd.db_suffix
"""

_FINALIZE = """
    ALTER TABLE public.user_first_message ALTER COLUMN video_id SET NOT NULL;
    ALTER TABLE public.user_channel_activity ALTER COLUMN first_video_id SET NOT NULL;

    -- Dropping the parent FK also drops its per-partition children (_fkey_1 .. _fkey_N).
    ALTER TABLE public.user_first_message DROP CONSTRAINT IF EXISTS user_first_message_message_fkey;
    ALTER TABLE public.user_channel_activity DROP CONSTRAINT IF EXISTS user_channel_activity_message_fkey;

    ALTER TABLE public.user_first_message DROP CONSTRAINT IF EXISTS user_first_message_video_id_fkey;
    ALTER TABLE public.user_first_message ADD CONSTRAINT user_first_message_video_id_fkey
        FOREIGN KEY (video_id) REFERENCES public.videos(id);
    ALTER TABLE public.user_channel_activity DROP CONSTRAINT IF EXISTS user_channel_activity_first_video_id_fkey;
    ALTER TABLE public.user_channel_activity ADD CONSTRAINT user_channel_activity_first_video_id_fkey
        FOREIGN KEY (first_video_id) REFERENCES public.videos(id);
"""

def _backfill(db:DB.PostgresClass) -> None:
    cur = db.cursor
    # user_first_message's PK is user_id only -- a temporary channel_id index keeps each
    # per-channel UPDATE from rescanning the whole table 110 times.
    cur.execute("CREATE INDEX IF NOT EXISTS tmp_ufm_channel_null ON public.user_first_message (channel_id) WHERE video_id IS NULL")
    db.database.commit()

    # Left to itself the planner does one primary-key lookup into the partition per pointer row
    # -- two random reads each, which ran at ~200 rows/s on this server's disks (an hour+ for the
    # big channels). Forcing a hash join instead reads each partition once, sequentially.
    cur.execute("SET enable_nestloop = off")
    cur.execute("SET work_mem = '256MB'")

    cur.execute(_PARTITIONS)
    partitions = cur.fetchall()
    for i,(channel_id,partition) in enumerate(partitions,1):
        start = time.time()
        cur.execute(f"""
            UPDATE public.user_first_message ufm SET video_id = m.video_id
            FROM public.{partition} m
            WHERE ufm.channel_id = %s AND ufm.video_id IS NULL
              AND m.channel_id = ufm.channel_id AND m.message_id = ufm.message_id
        """,(channel_id,))
        ufm_rows = cur.rowcount
        cur.execute(f"""
            UPDATE public.user_channel_activity uca SET first_video_id = m.video_id
            FROM public.{partition} m
            WHERE uca.channel_id = %s AND uca.first_video_id IS NULL
              AND m.channel_id = uca.channel_id AND m.message_id = uca.first_message_id
        """,(channel_id,))
        uca_rows = cur.rowcount
        db.database.commit()
        if ufm_rows or uca_rows:
            LOG.logger.info(f"[{i}/{len(partitions)}] {partition}: {ufm_rows:,} user_first_message + {uca_rows:,} user_channel_activity row(s) backfilled in {time.time()-start:,.0f}s")

    cur.execute("DROP INDEX IF EXISTS public.tmp_ufm_channel_null")
    db.database.commit()

def main() -> None:
    db = DB.PostgresClass()
    cur = db.cursor

    LOG.logger.info("Adding columns and tables...")
    cur.execute(_ADD_COLUMNS_AND_TABLES)
    cur.execute(_RECORD_MESSAGE_STATS)
    cur.execute(_REFRESH_FIRST_MESSAGE_COUNTS)
    db.database.commit()

    LOG.logger.info("Backfilling pointer video ids from messages...")
    _backfill(db)

    cur.execute("SELECT count(*) FROM public.user_first_message WHERE video_id IS NULL")
    ufm_missing = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM public.user_channel_activity WHERE first_video_id IS NULL")
    uca_missing = cur.fetchone()[0]
    if ufm_missing or uca_missing:
        LOG.logger.error(f"{ufm_missing:,} user_first_message and {uca_missing:,} user_channel_activity row(s) still have no video id -- "
                         "leaving the message FKs in place. Investigate before re-running.")
        db.Close()
        return

    LOG.logger.info("Dropping message FKs, adding video FKs...")
    cur.execute("SET lock_timeout = '15s'")
    cur.execute(_FINALIZE)
    db.database.commit()

    # The backfill rewrote every row of both tables; reclaim the dead copies.
    db.database.autocommit = True
    for table in ("user_first_message","user_channel_activity"):
        LOG.logger.info(f"VACUUM FULL {table}...")
        cur.execute(f"VACUUM (FULL, ANALYZE) public.{table}")
    db.Close()
    LOG.logger.info("Migration complete.")

if __name__ == "__main__":
    main()
