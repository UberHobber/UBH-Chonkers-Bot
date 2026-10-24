# Native Stuff
import argparse,time
from datetime import datetime,timezone

# Installed Stuff
from botocore.exceptions import ClientError
from psycopg2.extras import execute_values

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG
import modules.Database as DB
import modules.Classes as C
import modules.Archive as A
import modules.Status as ST

"""
Moves processed videos' chat out of Postgres into zstd Parquet files on S3, in each channel's own
bucket under chat_archive/ (query it with modules/Archive.py). Postgres keeps the rollups, and
chat for videos that aren't processed yet.

Per channel:
    1. Deletes orphaned chat_archive/ files -- ones an interrupted run uploaded but never
       recorded in public.chat_archive (their rows are still in Postgres, so leaving them would
       double-count in Archive queries).
    2. Picks processed, public videos with raw_chat_compressed set (compress_raw_chat.py has
       corrected their badge flags) and chat_archived not yet set, in publish order, and groups
       them into batches of about --max-rows messages (one file each, ~250MB at 10M rows).
    3. Per batch: DuckDB streams the rows out of Postgres (postgres_query, so Postgres plans the
       lookup), sorts by (video_id, timestamp) and writes the Parquet straight to S3 -- 500K-row
       row groups, which keeps reads large and sequential on the HDD NAS.
    4. Reads the file back and checks every video's row count and distinct message_id count
       against Postgres. Any mismatch deletes this run's files for the channel and stops.
       Nothing in Postgres has changed up to this point.
    5. One transaction for the whole channel: locks the partition, copies out the rows that
       stay (videos not being archived), TRUNCATEs it, puts those rows back, writes the
       chat_archive manifest and sets videos.chat_archived. TRUNCATE frees the space at once and
       writes almost no WAL -- deleting the archived rows one at a time (the first version of
       this script) ran ~25min per 10M rows on this server's disks, then needed a VACUUM FULL.
       If the transaction fails, this run's files are deleted again.

Refuses a channel that still has badge_fix_pending rows (finish compress_raw_chat.py first).
Don't run it on a channel the bot is currently processing: step 5 takes an exclusive lock on
that channel's partition, so the bot couldn't write its chat meanwhile (and a TRUNCATE can't run
while it does). Other channels are unaffected -- the bot reads and writes each channel's
partition directly, never the parent messages table. Main.py runs this automatically for each
channel that finishes cleanly (CFG.AUTO_ARCHIVE, see modules/ChannelArchiver.py). Idempotent --
an interrupted run leaves Postgres untouched and its files are cleaned up as orphans next time.

Usage: archive_chat.py [--channel NAME ...] [--dry-run] [--max-rows N]
"""

_PARQUET_BYTES_PER_ROW = 27  # measured on messages_miko (columnar zstd)

def _cleanup_orphans(cur,client,bucket:str,channel_id:str,dry_run:bool) -> int:
    cur.execute("SELECT DISTINCT file_key FROM public.chat_archive WHERE channel_id = %s",(channel_id,))
    known = {r[0] for r in cur.fetchall()}
    orphans = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket,Prefix="chat_archive/"):
        orphans += [o["Key"] for o in page.get("Contents",[]) if o["Key"] not in known]
    for key in orphans:
        LOG.logger.warning(f"{bucket}/{key}: not in chat_archive (interrupted run) -- {'would delete' if dry_run else 'deleting'}.")
        if not dry_run:
            client.delete_object(Bucket=bucket,Key=key)
    return len(orphans)

def _batches(counts:list[tuple[str,int,int,int]],max_rows:int) -> list[list[tuple[str,int,int,int]]]:
    """Groups (video_id, rows, min_ts, max_ts) into consecutive batches of about max_rows rows."""
    batches,current,rows = [],[],0
    for entry in counts:
        if current and rows + entry[1] > max_rows:
            batches.append(current); current,rows = [],0
        current.append(entry); rows += entry[1]
    if current:
        batches.append(current)
    return batches

def _export_batch(cur,duck,bucket:str,partition:str,batch:list[tuple[str,int,int,int]]) -> str:
    """Writes one batch's rows to a new Parquet file on S3 and verifies it. Returns its key. Changes nothing in Postgres."""
    video_ids = [b[0] for b in batch]
    expected = {b[0]:b[1] for b in batch}
    key = f"chat_archive/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.parquet"
    url = f"s3://{bucket}/{key}"

    pg_sql = cur.mogrify(f"SELECT * FROM public.{partition} WHERE video_id = ANY(%s)",(video_ids,)).decode()
    duck.execute(f"""
        COPY (SELECT * FROM postgres_query('pg', {A._q(pg_sql)}) ORDER BY video_id, "timestamp")
        TO {A._q(url)} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 500000)
    """)
    got = {vid:(n,distinct) for vid,n,distinct in duck.execute(
        f"SELECT video_id, count(*), count(DISTINCT message_id) FROM read_parquet({A._q(url)}) GROUP BY video_id").fetchall()}
    bad = [vid for vid in video_ids if got.get(vid) != (expected[vid],expected[vid])]
    if bad or len(got) != len(video_ids):
        raise RuntimeError(f"{url}: verification failed for {len(bad):,} video(s), e.g. {bad[:3]} "
                           f"(expected {[expected.get(v) for v in bad[:3]]}, got {[got.get(v) for v in bad[:3]]})")
    return key

def _commit_channel(db:DB.PostgresClass,channel_id:str,partition:str,files:list[tuple[str,list[tuple[str,int,int,int]]]]) -> int:
    """
    The one Postgres change per channel: manifest rows, chat_archived flags, and the partition
    emptied of the archived rows by copying the kept rows aside, TRUNCATE, and copying them back.
    Returns the number of kept rows.
    """
    cur = db.cursor
    archived_ids = [entry[0] for _,batch in files for entry in batch]
    cur.execute("SET LOCAL lock_timeout = '30s'")
    cur.execute(f"LOCK TABLE public.{partition} IN ACCESS EXCLUSIVE MODE")

    # Every stored column except generated ones (datetime), which Postgres recomputes on insert.
    cur.execute("""SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) FROM pg_attribute
                   WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped AND attgenerated = ''""",(f"public.{partition}",))
    columns = cur.fetchone()[0]

    # Kept rows are the ones for this channel's videos that aren't being archived -- found through
    # the video_id index, rather than scanning the whole partition for "not archived".
    cur.execute("SELECT id FROM public.videos WHERE channel_id = %s AND NOT (id = ANY(%s))",(channel_id,archived_ids))
    keep_ids = [r[0] for r in cur.fetchall()]
    cur.execute(f"CREATE TEMP TABLE archive_keep ON COMMIT DROP AS SELECT {columns} FROM public.{partition} WHERE video_id = ANY(%s)",(keep_ids,))
    kept = cur.rowcount

    # Every row must be either kept or verified in Parquet before anything is truncated: the
    # partition's total must equal kept + exported (catches rows whose video isn't one of this
    # channel's, or has no video_id at all, which neither set would cover).
    exported = sum(entry[1] for _,batch in files for entry in batch)
    cur.execute(f"SELECT count(*) FROM public.{partition}")
    total = cur.fetchone()[0]
    if total != kept + exported:
        raise RuntimeError(f"{partition}: {total:,} row(s), but only {kept:,} kept + {exported:,} exported are accounted for -- not truncating")

    cur.execute(f"TRUNCATE public.{partition}")
    cur.execute(f"INSERT INTO public.{partition} ({columns}) SELECT {columns} FROM archive_keep")
    if cur.rowcount != kept:
        raise RuntimeError(f"{partition}: restored {cur.rowcount:,} of {kept:,} kept row(s) -- rolled back")

    execute_values(cur,"""
        INSERT INTO public.chat_archive (video_id, channel_id, file_key, message_count, min_ts, max_ts) VALUES %s
    """,[(vid,channel_id,key,n,lo,hi) for key,batch in files for vid,n,lo,hi in batch])
    cur.execute("UPDATE public.videos SET chat_archived = true WHERE id = ANY(%s)",(archived_ids,))
    db.database.commit()
    return kept

class ArchiveSkipped(Exception):
    """A channel archive_channel() can't archive yet (pending badge fixes, bucket unreachable). Nothing was changed."""

def archive_channel(db:DB.PostgresClass,client,duck,channel_name:str,max_rows:int,dry_run:bool=False,bar_position:int=0,log_prefix:str="") -> tuple[int,int]:
    """
    Archives one channel's processed, compressed videos. Returns (messages archived, videos
    archived) -- or what would be, with dry_run. Raises ArchiveSkipped if the channel isn't
    ready. Uses CFG.channel_targets rather than CFG.select_channel, so Main.py can run it in a
    background thread (modules/ChannelArchiver.py) while the main thread works on another channel.
    """
    cur = db.cursor
    channel_id,partition,bucket = CFG.channel_targets(channel_name)
    if not DB.TableExists(cur,partition):
        db.database.commit()
        return 0,0

    cur.execute("SELECT count(*) FROM public.badge_fix_pending WHERE channel_id = %s",(channel_id,))
    if cur.fetchone()[0]:
        db.database.commit()
        raise ArchiveSkipped(f"{channel_name}: badge_fix_pending has rows -- run compress_raw_chat.py for it first.")

    cur.execute("""SELECT id FROM public.videos
                   WHERE channel_id = %s AND processed AND raw_chat_compressed AND NOT chat_archived AND NOT members
                   ORDER BY publishedat NULLS LAST, id""",(channel_id,))
    candidates = [r[0] for r in cur.fetchall()]
    cur.execute("""SELECT count(*) FROM public.videos
                   WHERE channel_id = %s AND processed AND NOT raw_chat_compressed AND NOT chat_archived AND NOT members""",(channel_id,))
    waiting = cur.fetchone()[0]
    db.database.commit()
    if waiting:
        LOG.logger.info(f"{log_prefix}{channel_name}: {waiting:,} processed video(s) still need compress_raw_chat.py before they can be archived.")
    if not candidates:
        return 0,0

    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as e:
        raise ArchiveSkipped(f"{channel_name}: S3 bucket '{bucket}' isn't reachable ({e}).") from e
    _cleanup_orphans(cur,client,bucket,channel_id,dry_run)

    cur.execute(f"""SELECT video_id, count(*), min("timestamp"), max("timestamp") FROM public.{partition}
                    WHERE video_id = ANY(%s) GROUP BY video_id""",(candidates,))
    found = {r[0]:r for r in cur.fetchall()}
    db.database.commit()
    counts = [found[v] for v in candidates if v in found]
    empty = [v for v in candidates if v not in found]
    rows = sum(c[1] for c in counts)

    if dry_run:
        LOG.logger.info(f"{log_prefix}{channel_name}: {len(counts):,} video(s), {rows:,} message(s) -> ~{rows*_PARQUET_BYTES_PER_ROW/1024**3:,.2f} GB Parquet "
                        f"in {len(_batches(counts,max_rows)):,} file(s); {len(empty):,} video(s) with no chat rows")
        return rows,len(counts)

    # No chat rows in Postgres (no chat, or members/Shorts edge cases): nothing to move, but
    # mark them so they aren't re-checked every run.
    if empty:
        cur.execute("UPDATE public.videos SET chat_archived = true WHERE id = ANY(%s)",(empty,))
        db.database.commit()
    if not counts:
        return 0,0

    start = time.time()
    cur.execute("SELECT pg_total_relation_size(%s)",(f"public.{partition}",))
    size_before = cur.fetchone()[0]
    db.database.commit()
    files:list[tuple[str,list[tuple[str,int,int,int]]]] = []
    batches = _batches(counts,max_rows)
    with ST.Bar(desc="Archiving: exporting",total=rows,bar_format='{desc}: {n_fmt}/{total_fmt}',ncols=80,position=bar_position,leave=False) as bar:
        try:
            for i,batch in enumerate(batches,1):
                files.append((_export_batch(cur,duck,bucket,partition,batch),batch))
                bar.update(sum(b[1] for b in batch))
                LOG.logger.info(f"  {log_prefix}{channel_name}: file {i}/{len(batches)} -- {len(batch):,} video(s), {bar.n:,}/{rows:,} message(s) exported and verified ({time.time()-start:,.0f}s)")
            bar.set_description_str("Archiving: committing")
            kept = _commit_channel(db,channel_id,partition,files)
        except BaseException:
            db.database.rollback()
            for key,_ in files:
                try:
                    client.delete_object(Bucket=bucket,Key=key)
                except ClientError as e:
                    LOG.logger.error(f"{log_prefix}Couldn't delete {bucket}/{key} after a failed run -- the next run's orphan cleanup will: {e}")
            raise

    db.database.autocommit = True
    cur.execute(f"ANALYZE public.{partition}")
    db.database.autocommit = False
    cur.execute("SELECT pg_total_relation_size(%s)",(f"public.{partition}",))
    size_after = cur.fetchone()[0]
    db.database.commit()
    LOG.logger.info(f"  {log_prefix}{channel_name}: {partition} truncated, {kept:,} row(s) of unarchived videos kept -- "
                    f"{size_before/1024**3:,.2f} GB -> {size_after/1024**3:,.2f} GB")
    LOG.logger.info(f"{log_prefix}{channel_name}: {rows:,} message(s) from {len(counts):,} video(s) archived in {time.time()-start:,.0f}s")
    return rows,len(counts)

def main() -> None:
    parser = argparse.ArgumentParser(description="Archive processed videos' chat from Postgres to Parquet on S3.")
    parser.add_argument("--channel",action="append",help="Channel name to archive (repeatable). Default: every channel.")
    parser.add_argument("--dry-run",action="store_true",help="Report what would be archived, change nothing.")
    parser.add_argument("--max-rows",type=int,default=10_000_000,help="Messages per Parquet file (default 10M, ~250MB).")
    args = parser.parse_args()

    if CFG.GET_MEMBERS_ONLY:
        LOG.logger.error("Run this in public mode (GET_MEMBERS_ONLY = False) -- members-only chat isn't archived.")
        return

    ST.start("archive",LOG.logger)
    db = DB.PostgresClass()
    cur = db.cursor
    CFG.load_channel_directory(cur)
    client = C.H3Client().client
    duck = A.Base_Connection()
    channels = args.channel or list(CFG.CHANNEL_DIRECTORY.keys())

    total_rows = total_videos = 0
    for index,channel_name in enumerate(channels,1):
        ST.set_info(channel=channel_name,channel_progress=f"{index}/{len(channels)}",phase="Archiving chat")
        try:
            rows,videos = archive_channel(db,client,duck,channel_name,args.max_rows,args.dry_run)
        except ArchiveSkipped as e:
            LOG.logger.warning(f"{e} Skipped.")
            continue
        total_rows += rows; total_videos += videos

    db.Close()
    verb = "Would archive" if args.dry_run else "Archived"
    LOG.logger.info(f"{verb} {total_rows:,} message(s) from {total_videos:,} video(s)"
                    + (f" (~{total_rows*_PARQUET_BYTES_PER_ROW/1024**3:,.1f} GB Parquet)." if args.dry_run else "."))

if __name__ == "__main__":
    main()
