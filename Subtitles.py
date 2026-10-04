# Native Stuff
import os,sys,shutil
from datetime import datetime,timedelta,timezone

sys.path.append(os.getcwd())

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
rather than being tied to a video's chat-processing lifecycle. Two ways to run it:

    1. In-process, as the last step of Main.py (honors CFG.SKIP_SUBTITLE_DOWNLOAD). Module-level
       code here is side-effect-free (no top-level DB/S3 connections) since Main.py imports
       this module and reuses its already-open db/s3/yt.
    2. Standalone: `python Subtitles.py`. Ignores CFG.SKIP_SUBTITLE_DOWNLOAD (running this
       script at all is the opt-in), so Main.py can run with subtitles skipped while this
       fills them in separately -- either on its own or at the same time as Main.py.

Running alongside Main.py is safe: this pass only touches the subtitles/video_subtitle_stats
tables and the videos.subtitles_processed/subtitles_fail_count columns, none of which Main.py
writes, and it stages files under its own local folder (see _local_root) so neither process's
end-of-channel rmtree deletes the other's in-flight files. Don't run two subtitle passes at
once (e.g. this script plus Main.py with SKIP_SUBTITLE_DOWNLOAD = False) -- they'd share that
folder and race on the same videos.
"""

def _local_root() -> str:
    """
    Local staging folder for the currently selected channel's subtitle files. A sibling of
    CFG.LOCAL_DATA_PATH rather than inside it, since Main.py rmtree's LOCAL_DATA_PATH at the
    end of each channel and this pass may be running concurrently in another process.
    """
    return f"{CFG.LOCAL_DATA_PATH}_{CFG.SUBTITLE_TAG}"

def run(db:DB.PostgresClass, s3:C.H3Client, yt:C.YT_API, channel_names:list[str], force:bool=False) -> None:
    """
    Subtitle catch-up pass for every channel in channel_names.

    Processes videos one at a time on the calling thread -- the shared AdaptiveRateLimiter
    already serializes every subtitle attempt to one request every CFG.SUBTITLE_REQUEST_DELAY
    (-180)s, so extra workers would add no throughput. Staying on the calling thread also
    means Ctrl+C stops the run right away instead of a pool draining its queued videos first.

    :param force: Run even if CFG.SKIP_SUBTITLE_DOWNLOAD is set. Used by the standalone entry point.
    :type force: Boolean
    """
    if CFG.SKIP_SUBTITLE_DOWNLOAD and not force:
        LOG.logger.info("Skipping subtitle pass (CFG.SKIP_SUBTITLE_DOWNLOAD).")
        return

    subtitle_rate_limiter = C.AdaptiveRateLimiter(CFG.SUBTITLE_REQUEST_DELAY,CFG.SUBTITLE_REQUEST_DELAY_CEILING)
    worker_db = DB.PostgresClass()
    BAR_POSITION = 1

    for channel_name in channel_names:
        CFG.select_channel(channel_name)
        LOG.logger.info(f"Subtitle pass: {channel_name}")
        local_root = _local_root()
        channel_bucket = C.H3Bucket(s3.client,CFG.CHANNEL_SUFFIX,local_root)
        os.makedirs(f"{local_root}/{CFG.SUBTITLE_TAG}",exist_ok=True)

        breaker = C.SubtitleCircuitBreaker(CFG.SUBTITLE_CIRCUIT_BREAKER_THRESHOLD)
        videos = DB.GetVideosNeedingSubtitles(db.cursor,CFG.DB_TABLES["videos"],CFG.YT_USER_ID,CFG.GET_MEMBERS_ONLY)
        tally = {"fetched":0,"no_captions":0,"deferred":0,"throttled":0,"errors":0}

        def process_subtitle(video:dict) -> None:
            video_id = video["id"]
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
                    tally["fetched" if stats.fetched > 0 else "no_captions"] += 1
                else:
                    tally["deferred"] += 1
                    LOG.logger.info(f"{video_id}: No captions yet, video ended recently -- will retry next run.")
            except C.SubtitleThrottled:
                tally["throttled"] += 1
                breaker.record_throttle()
                worker_db.database.rollback()
                gave_up = DB.RecordSubtitleThrottle(worker_db.cursor,CFG.DB_TABLES["videos"],video_id,CFG.SUBTITLE_GIVEUP_AFTER_RUNS)
                worker_db.database.commit()
                if gave_up:
                    LOG.logger.warning(f"{video_id}: Throttled on {CFG.SUBTITLE_GIVEUP_AFTER_RUNS} separate runs now -- giving up on this video's subtitles permanently.")
                else:
                    LOG.logger.warning(f"{video_id}: Subtitle download throttled, giving up on this video for this run.")
            except Exception as e:
                tally["errors"] += 1
                worker_db.database.rollback()
                LOG.logger.error(f"{video_id}: Subtitle download error: {e}")

        try:
            with LOG.TQDM_Logging():
                with tqdm(desc='Subtitles Processed',total=len(videos),bar_format='{desc}: {n_fmt}/{total_fmt}',ncols=80,position=0,leave=False) as bar:
                    for video in videos:
                        if breaker.tripped:
                            LOG.logger.warning(f"Circuit breaker tripped -- skipping the remaining {len(videos) - bar.n:,} video(s) for {channel_name} this run.")
                            break
                        process_subtitle(video)
                        bar.update(1)
        finally:
            shutil.rmtree(local_root,ignore_errors=True)

        LOG.logger.info(f"{channel_name}: {tally['fetched']:,} fetched, {tally['no_captions']:,} no captions, {tally['deferred']:,} deferred, {tally['throttled']:,} throttled, {tally['errors']:,} error(s) out of {len(videos):,} video(s).")

    LOG.logger.info("Subtitle pass complete.\n")

if __name__ == "__main__":
    # Standalone invocation needs its own bootstrap, same as Main.py's top.
    db = DB.PostgresClass()
    DB.EnsureSchema(db.cursor,CFG.GET_MEMBERS_ONLY)
    db.database.commit()
    CFG.load_channel_directory(db.cursor)
    s3 = C.H3Client()
    yt = C.YT_API(db)
    run(db,s3,yt,CFG.CHANNELS_TO_PROCESS,force=True)
