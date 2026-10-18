# Native Stuff
import argparse,gzip,json,os,tempfile,threading,time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# Installed Stuff
from botocore.exceptions import ClientError
import psycopg2
from psycopg2.extras import execute_values

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG
import modules.Database as DB
import modules.Classes as C

"""
Converts each processed video's raw chat_downloader JSON on S3 from the legacy indented
messages/{id}.json to minified, gzip-compressed messages/{id}.json.gz (~17x smaller, measured),
and -- in the same pass, since it has to read every file anyway -- corrects the
moderator/verified/owner flags that MessageClass's old badge loop got wrong (it kept only the
LAST badge's flags; see C.Badge_Flags).

Per video:
    1. Read the raw chat (legacy .json, or an already-converted .json.gz).
    2. Legacy only: upload the .json.gz, read it back, check the message_id set matches, and
       only then delete the .json.
    3. Public videos: UPDATE any messages row whose flags differ from C.Badge_Flags, queue its
       (channel_id, user_id) in badge_fix_pending, and set videos.raw_chat_compressed -- all in
       one transaction, so an interrupted run resumes cleanly.
At the end of each channel, user_channel_activity's ever_*/last_is_* are recomputed for the
queued users, and their badge_fix_pending rows cleared. archive_chat.py only archives videos
with raw_chat_compressed set, and refuses a channel with badge_fix_pending rows left.

Only touches processed videos (the bot never revisits those), so it's safe to run while the bot
is running -- the bot converts unprocessed videos' files itself the next time it merges into
them. Videos with no raw file at all are still marked raw_chat_compressed (nothing to correct
from), and counted in the summary. Members-only videos are compressed but not corrected (their
rows live in the legacy *_members tables, which are out of scope).

Bucket versioning must be OFF on the S3 store, or deleting the legacy objects frees nothing.
Idempotent -- safe to re-run.

Usage: compress_raw_chat.py [--channel NAME ...] [--dry-run] [--workers N]
"""

def _get_bytes(client,bucket:str,key:str) -> bytes|None:
    try:
        return client.get_object(Bucket=bucket,Key=key)["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404","NoSuchKey"): #type:ignore
            return None
        raise

def _convert(client,bucket:str,video_id:str,tmp_dir:str) -> tuple[list[dict[str,Any]]|None,int,int]:
    """
    Makes sure video_id's raw chat exists only as .json.gz, and returns
    (messages or None if there's no raw file, legacy bytes freed, compressed bytes written).
    """
    gz_key,legacy_key = C.Raw_Chat_Keys(video_id)
    legacy = _get_bytes(client,bucket,legacy_key)
    if legacy is None:
        compressed = _get_bytes(client,bucket,gz_key)
        if compressed is None:
            return None,0,0
        return json.loads(gzip.decompress(compressed)),0,0

    messages:list[dict[str,Any]] = json.loads(legacy)
    # A previous run (or the bot) may already have written the .gz -- merge rather than
    # overwrite, so nothing that's only in one of the two copies is lost.
    existing = _get_bytes(client,bucket,gz_key)
    if existing is not None:
        seen = {m["message_id"] for m in messages}
        messages += [m for m in json.loads(gzip.decompress(existing)) if m["message_id"] not in seen]

    local_path = os.path.join(tmp_dir,f"{video_id}.json.gz")
    C.Write_Raw_Chat(local_path,messages)
    written = os.path.getsize(local_path)
    client.upload_file(local_path,bucket,gz_key)
    os.remove(local_path)

    # Verify the uploaded copy before deleting the original. (Content check rather than the
    # store's checksum -- see the response_checksum_validation note in C.H3Client.)
    check = json.loads(gzip.decompress(_get_bytes(client,bucket,gz_key) or b"[]"))
    if {m["message_id"] for m in check} != {m["message_id"] for m in messages}:
        raise RuntimeError(f"{bucket}/{gz_key}: uploaded copy doesn't match -- original left in place")
    client.delete_object(Bucket=bucket,Key=legacy_key)
    return messages,len(legacy),written

_worker_db = threading.local()

def _worker_cursor():
    """Each pool thread's own read-only DB connection (psycopg2 connections aren't shareable across threads)."""
    if getattr(_worker_db,"conn",None) is None:
        _worker_db.conn = psycopg2.connect(host=CFG.DB_HOST,port=CFG.DB_PORT,database=CFG.DB_NAME,user=CFG.DB_USR,password=CFG.DB_PASS)
        _worker_db.conn.set_session(readonly=True,autocommit=True)
    return _worker_db.conn.cursor()

def _find_flag_fixes(partition:str,video_id:str,messages:list[dict[str,Any]]) -> list[tuple]:
    """
    Read-only: compares this video's messages rows with C.Badge_Flags of its raw chat and returns
    (message_id, user_id, ismoderator, isverified, isowner) for each row that disagrees.
    """
    truth = {}
    for m in messages:
        truth[m["message_id"]] = C.Badge_Flags((m.get("author") or {}).get("badges"))
    with _worker_cursor() as cur:
        cur.execute(f"SELECT message_id, user_id, ismoderator, isverified, isowner FROM public.{partition} WHERE video_id = %s",(video_id,))
        rows = cur.fetchall()
    fixes = []
    for message_id,user_id,is_mod,is_ver,is_own in rows:
        flags = truth.get(message_id)
        if flags is not None and flags != (is_mod,is_ver,is_own):
            fixes.append((message_id,user_id,*flags))
    return fixes

def _process_video(client,bucket:str,video_id:str,tmp_dir:str,partition:str|None) -> tuple[bool,int,int,list[tuple]]:
    """
    One pool task: convert the raw file and (for public videos) find its flag fixes. Returns
    (has_raw_file, legacy bytes freed, compressed bytes written, fixes) -- the parsed chat is
    dropped here, so a finished-but-unconsumed result is small.
    """
    messages,freed,written = _convert(client,bucket,video_id,tmp_dir)
    if messages is None:
        return False,0,0,[]
    fixes = _find_flag_fixes(partition,video_id,messages) if partition else []
    return True,freed,written,fixes

def _apply_flag_fixes(cur,partition:str,channel_id:str,fixes:list[tuple]) -> int:
    """UPDATEs the rows _find_flag_fixes reported and queues their users in badge_fix_pending. Returns the count."""
    if fixes:
        # execute_values takes exactly one %s (the VALUES list), so the channel literal is
        # mogrified in -- keeps the lookup on the (channel_id, message_id) primary key.
        channel_literal = cur.mogrify("%s",(channel_id,)).decode()
        execute_values(cur,f"""
            UPDATE public.{partition} m
            SET ismoderator = f.ismoderator, isverified = f.isverified, isowner = f.isowner
            FROM (VALUES %s) AS f(message_id, user_id, ismoderator, isverified, isowner)
            WHERE m.channel_id = {channel_literal} AND m.message_id = f.message_id
        """,fixes)
        execute_values(cur,"""
            INSERT INTO public.badge_fix_pending (channel_id, user_id) VALUES %s
            ON CONFLICT DO NOTHING
        """,list({(channel_id,f[1]) for f in fixes}))
    return len(fixes)

def _recompute_pending(db:DB.PostgresClass,channel_id:str,partition:str) -> int:
    """
    Re-derives user_channel_activity's ever_* and last_is_* for this channel's
    badge_fix_pending users from their (now corrected) messages, then clears them. The old bug
    could only turn a true flag false, never the reverse, so both are OR-ed with what's stored.
    """
    cur = db.cursor
    cur.execute(f"""
        WITH agg AS (
            SELECT m.user_id, bool_or(m.ismoderator) AS mod, bool_or(m.isverified) AS ver, bool_or(m.isowner) AS own
            FROM public.badge_fix_pending p JOIN public.{partition} m ON m.user_id = p.user_id
            WHERE p.channel_id = %(c)s
            GROUP BY m.user_id
        ), latest AS (
            SELECT DISTINCT ON (m.user_id) m.user_id, m.ismoderator AS mod, m.isverified AS ver, m.isowner AS own
            FROM public.badge_fix_pending p JOIN public.{partition} m ON m.user_id = p.user_id
            WHERE p.channel_id = %(c)s
            ORDER BY m.user_id, m."timestamp" DESC
        )
        UPDATE public.user_channel_activity uca
        SET ever_moderator = uca.ever_moderator OR agg.mod,
            ever_verified = uca.ever_verified OR agg.ver,
            ever_owner = uca.ever_owner OR agg.own,
            last_is_moderator = uca.last_is_moderator OR latest.mod,
            last_is_verified = uca.last_is_verified OR latest.ver,
            last_is_owner = uca.last_is_owner OR latest.own
        FROM agg JOIN latest USING (user_id)
        WHERE uca.channel_id = %(c)s AND uca.user_id = agg.user_id
    """,{"c":channel_id})
    updated = cur.rowcount
    cur.execute("DELETE FROM public.badge_fix_pending WHERE channel_id = %s",(channel_id,))
    db.database.commit()
    return updated

def main() -> None:
    parser = argparse.ArgumentParser(description="Compress raw chat JSON on S3 and correct badge flags.")
    parser.add_argument("--channel",action="append",help="Channel name to process (repeatable). Default: every channel.")
    parser.add_argument("--dry-run",action="store_true",help="Report how much would be converted, change nothing.")
    parser.add_argument("--workers",type=int,default=2,help="Parallel S3 conversions (memory: each holds one video's chat). Default 2.")
    args = parser.parse_args()

    if CFG.GET_MEMBERS_ONLY:
        LOG.logger.error("Run this in public mode (GET_MEMBERS_ONLY = False) -- it corrects public messages partitions.")
        return

    db = DB.PostgresClass()
    cur = db.cursor
    CFG.load_channel_directory(cur)
    client = C.H3Client().client
    channels = args.channel or list(CFG.CHANNEL_DIRECTORY.keys())

    totals = {"videos":0,"converted":0,"no_raw":0,"fixes":0,"users":0,"freed":0,"written":0}
    with tempfile.TemporaryDirectory() as tmp_dir:
        for channel_name in channels:
            CFG.select_channel(channel_name)
            channel_id,partition,bucket = CFG.YT_USER_ID,CFG.DB_TABLES["messages"],CFG.CHANNEL_SUFFIX
            has_partition = DB.TableExists(cur,partition)

            # Finish any recompute an interrupted run left behind before queueing more.
            if has_partition and not args.dry_run:
                users = _recompute_pending(db,channel_id,partition)
                totals["users"] += users

            cur.execute("""SELECT id, members FROM public.videos
                           WHERE channel_id = %s AND processed AND NOT raw_chat_compressed ORDER BY id""",(channel_id,))
            videos = cur.fetchall()
            if not videos:
                continue

            # Channels processed before S3 storage existed may have no bucket at all -- every
            # video is then simply "no raw file".
            try:
                client.head_bucket(Bucket=bucket)
                bucket_exists = True
            except ClientError:
                bucket_exists = False
            if not bucket_exists:
                LOG.logger.warning(f"{channel_name}: no S3 bucket '{bucket}' -- {len(videos):,} video(s) have no raw chat to compress or correct from.")
                if not args.dry_run:
                    cur.execute("UPDATE public.videos SET raw_chat_compressed = true WHERE id = ANY(%s)",([v[0] for v in videos],))
                    db.database.commit()
                totals["videos"] += len(videos); totals["no_raw"] += len(videos)
                continue

            if args.dry_run:
                legacy_bytes = legacy_count = biggest = 0
                for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket,Prefix=f"{CFG.MESSAGES_TAG}/"):
                    for o in page.get("Contents",[]):
                        if o["Key"].endswith(".json"):
                            legacy_bytes += o["Size"]; legacy_count += 1; biggest = max(biggest,o["Size"])
                LOG.logger.info(f"{channel_name}: {len(videos):,} video(s) to check; {legacy_count:,} legacy file(s), "
                                f"{legacy_bytes/1024**3:,.1f} GB -> ~{legacy_bytes*0.057/1024**3:,.1f} GB; largest {biggest/1024**2:,.0f} MB")
                totals["videos"] += len(videos); totals["freed"] += legacy_bytes; totals["written"] += int(legacy_bytes*0.057)
                continue

            start = time.time()
            ch = {"converted":0,"no_raw":0,"fixes":0}
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                # S3 work (download/compress/upload/verify) and each video's read-only flag lookup
                # run in the pool, so the NAS downloads keep going while a lookup waits on the DB's
                # disks; only the (rare) fix UPDATEs and the progress marker run on this thread's
                # connection, in video order. Up to 2x `workers` videos are in flight -- results
                # are small (the parsed chat is dropped in the worker), so this costs little memory.
                pending:deque = deque()
                queue_iter = iter(videos)
                def _top_up():
                    while len(pending) < args.workers * 2:
                        nxt = next(queue_iter,None)
                        if nxt is None:
                            return
                        part = partition if has_partition and not nxt[1] else None
                        pending.append((nxt[0],nxt[1],pool.submit(_process_video,client,bucket,nxt[0],tmp_dir,part)))
                _top_up()
                i = 0
                while pending:
                    video_id,members,future = pending.popleft()
                    has_raw,freed,written,fixes = future.result()
                    _top_up()
                    i += 1
                    if not has_raw:
                        ch["no_raw"] += 1
                    else:
                        ch["converted"] += 1 if freed else 0
                        totals["freed"] += freed; totals["written"] += written
                        ch["fixes"] += _apply_flag_fixes(cur,partition,channel_id,fixes)
                    cur.execute("UPDATE public.videos SET raw_chat_compressed = true WHERE id = %s",(video_id,))
                    db.database.commit()
                    if i % 50 == 0:
                        LOG.logger.info(f"  {channel_name}: {i:,}/{len(videos):,} video(s)...")

            users = _recompute_pending(db,channel_id,partition) if has_partition else 0
            totals["videos"] += len(videos); totals["users"] += users
            for k in ch: totals[k] += ch[k]
            LOG.logger.info(f"{channel_name}: {len(videos):,} video(s) in {time.time()-start:,.0f}s -- {ch['converted']:,} converted, "
                            f"{ch['no_raw']:,} without a raw file, {ch['fixes']:,} message flag fix(es), {users:,} user rollup(s) updated")

    db.Close()
    verb = "Would convert" if args.dry_run else "Done:"
    LOG.logger.info(f"{verb} {totals['videos']:,} video(s); {totals['freed']/1024**3:,.1f} GB legacy JSON -> "
                    f"{totals['written']/1024**3:,.1f} GB compressed. {totals['converted']:,} converted, {totals['no_raw']:,} without a raw file, "
                    f"{totals['fixes']:,} message flag fix(es), {totals['users']:,} user rollup(s) updated.")

if __name__ == "__main__":
    main()
