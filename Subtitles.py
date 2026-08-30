# Native Stuff
import os,shutil,threading
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor,as_completed

# Installed Stuff
from tqdm import tqdm

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG
import modules.Classes as C
import modules.Database as DB

"""
Runs the subtitle-download pass for every channel, independent of chat/video processing.

Subtitle downloads used to run inline inside Main.py's per-video worker pool, right after
that video's chat messages. Because subtitle requests are paced by a single AdaptiveRateLimiter
shared across every worker thread (a real gap enforced globally, backing off from
CFG.SUBTITLE_REQUEST_DELAY up to CFG.SUBTITLE_REQUEST_DELAY_CEILING under throttling), a
worker blocked waiting on that shared limiter was a worker not fetching the next video's
chat -- chat throughput was being slowed by a YouTube limit that has nothing to do with chat.

This module runs as its own pass instead, driven by the videos.subtitles_processed column
rather than being tied to a video's chat-processing lifecycle -- see Main.py's call to
run() at the end of its per-run pipeline. Module-level code here is side-effect-free (no top
-level DB/S3 connections) since Main.py imports this module in-process and reuses its
already-open db/s3/yt rather than this module opening its own.
"""

def run(db:DB.PostgresClass, s3:C.H3Client, yt:C.YT_API, channel_names:list[str]) -> None:
    """
    Subtitle catch-up pass for every channel in channel_names. Meant to be called once, as
    the last step of a Main.py run, reusing its already-open db/s3/yt.

    Uses exactly one dedicated worker thread -- the shared AdaptiveRateLimiter already
    serializes every subtitle attempt to one request every CFG.SUBTITLE_REQUEST_DELAY(-180)s
    regardless of thread count, so more workers would only add DB-connection/bar-position
    overhead without any real throughput gain.
    """
    if CFG.SKIP_SUBTITLE_DOWNLOAD:
        LOG.logger.info("Skipping subtitle pass (CFG.SKIP_SUBTITLE_DOWNLOAD).")
        return

    subtitle_rate_limiter = C.AdaptiveRateLimiter(CFG.SUBTITLE_REQUEST_DELAY,CFG.SUBTITLE_REQUEST_DELAY_CEILING)
    worker_db = DB.PostgresClass()
    tqdm.set_lock(threading.RLock())
    BAR_POSITION = 1  # fixed -- exactly one worker thread for this entire run, no pool needed.

    for channel_name in channel_names:
        CFG.select_channel(channel_name)
        LOG.logger.info(f"Subtitle pass: {channel_name}")
        channel_bucket = C.H3Bucket(s3.client,CFG.CHANNEL_SUFFIX,CFG.LOCAL_DATA_PATH)
        for d_path in CFG.DATA_PATHS:
            os.makedirs(d_path,exist_ok=True)

        breaker = C.SubtitleCircuitBreaker(CFG.SUBTITLE_CIRCUIT_BREAKER_THRESHOLD)
        videos = DB.GetVideosNeedingSubtitles(db.cursor,CFG.DB_TABLES["videos"],CFG.YT_USER_ID,CFG.GET_MEMBERS_ONLY)

        def process_subtitle(video:dict) -> None:
            video_id = video["id"]
            if breaker.tripped:
                LOG.logger.warning(f"{video_id}: Skipping subtitles, circuit breaker tripped for this run.")
                return
            try:
                stats = yt.Get_Subtitles(video_id,channel_bucket,db=worker_db,rate_limiter=subtitle_rate_limiter,bar_position=BAR_POSITION)
                breaker.record_success()

                # Same "just ended, maybe not finalized yet" grace period chat already gives
                # NoChatReplay (CFG.CHAT_REPLAY_GRACE_HOURS) -- YouTube may not have finished
                # generating auto-captions the instant a video stops being live, so don't
                # permanently give up on a video that recently ended just because this
                # attempt came back empty.
                now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                recently_ended = video["end_time"] is not None and (now_utc - video["end_time"]) < timedelta(hours=CFG.SUBTITLE_GRACE_HOURS)
                if stats.fetched > 0 or not recently_ended:
                    DB.UpdateEntry(worker_db.cursor,CFG.DB_TABLES["videos"],"subtitles_processed",True,"id",video_id)
                    worker_db.database.commit()
                else:
                    LOG.logger.info(f"{video_id}: No captions yet, video ended recently -- will retry next run.")
            except C.SubtitleThrottled:
                breaker.record_throttle()
                worker_db.database.rollback()
                LOG.logger.warning(f"{video_id}: Subtitle download throttled, giving up on this video.")
            except Exception as e:
                worker_db.database.rollback()
                LOG.logger.error(f"{video_id}: Subtitle download error: {e}")

        with LOG.TQDM_Logging():
            with tqdm(desc='Subtitles Processed',total=len(videos),bar_format='{desc}: {n_fmt}/{total_fmt}',ncols=80,position=0,leave=False) as bar:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    futures = [executor.submit(process_subtitle,v) for v in videos]
                    for future in as_completed(futures):
                        future.result()
                        bar.update(1)

        shutil.rmtree(channel_bucket.local_root,ignore_errors=True)

    LOG.logger.info("Subtitle pass complete.\n")

if __name__ == "__main__":
    # Standalone invocation (manual run) needs its own bootstrap, same as Main.py's top.
    db = DB.PostgresClass()
    DB.EnsureSchema(db.cursor,CFG.GET_MEMBERS_ONLY)
    db.database.commit()
    CFG.load_channel_directory(db.cursor)
    s3 = C.H3Client()
    yt = C.YT_API(db)
    run(db,s3,yt,CFG.CHANNELS_TO_PROCESS)
