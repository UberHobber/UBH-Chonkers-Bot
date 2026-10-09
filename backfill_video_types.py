# Native Stuff
import time,random

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG
import modules.Classes as C
import modules.Database as DB

"""
One-time migration + backfill for videos.video_type / videos.content_duration.

1. Adds the two columns (and video_type's CHECK constraint) to public.videos, and rebuilds
   channel_video_summary to split public videos by type. Idempotent -- safe to re-run.
   EnsureSchema only creates missing tables, it never alters existing ones, so this has to
   happen here. Run this BEFORE the updated Main.py: its video INSERT/UPDATE now writes both
   columns and fails against a videos table that doesn't have them yet.
2. Classifies every video whose video_type is still NULL. Re-fetches each one from the Data API
   (videos().list, 50 IDs per call -- 1 quota unit each) rather than reading the cached details
   JSON from S3, since that's far simpler and cheap at this volume. Videos the API no longer
   returns (deleted/privated) fall back to scheduled_start: set -> 'stream', otherwise left NULL.

Videos whose Shorts check comes back inconclusive stay NULL, so re-running this script retries
just those. Premieres are counted as 'stream' (see Classify_Video in modules/Classes.py).

Once this has run, regenerate sql/ and SKILL.MD:
    python .claude/skills/chat-database/update_schema.py
"""

_MIGRATION = """
    ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS video_type text NULL;
    ALTER TABLE public.videos ADD COLUMN IF NOT EXISTS content_duration int8 NULL;
    ALTER TABLE public.videos DROP CONSTRAINT IF EXISTS videos_video_type_check;
    ALTER TABLE public.videos ADD CONSTRAINT videos_video_type_check
        CHECK (video_type IN ('stream','upload','short'));

    -- DROP + CREATE rather than CREATE OR REPLACE: OR REPLACE can only append columns to the
    -- end of a view, and the new per-type columns belong in the middle.
    DROP VIEW IF EXISTS public.channel_video_summary;
    CREATE VIEW public.channel_video_summary AS
    SELECT cd.name AS channel_name,
        cd.user_id AS channel_id,
        count(v.id) AS total_videos,
        count(v.id) FILTER (WHERE NOT v.members) AS public_videos,
        count(v.id) FILTER (WHERE v.members) AS members_videos,
        count(v.id) FILTER (WHERE NOT v.members AND v.video_type = 'stream') AS public_streams,
        count(v.id) FILTER (WHERE NOT v.members AND v.video_type = 'upload') AS public_uploads,
        count(v.id) FILTER (WHERE NOT v.members AND v.video_type = 'short') AS public_shorts,
        count(v.id) FILTER (WHERE NOT v.members AND v.video_type IS NULL) AS public_unclassified,
        round(COALESCE(sum(v.seconds), 0) / 3600.0)::bigint AS total_hours,
        round(COALESCE(sum(v.seconds) FILTER (WHERE NOT v.members), 0) / 3600.0)::bigint AS public_hours,
        round(COALESCE(sum(v.seconds) FILTER (WHERE v.members), 0) / 3600.0)::bigint AS members_hours,
        round(COALESCE(sum(v.seconds) FILTER (WHERE NOT v.members AND v.video_type = 'stream'), 0) / 3600.0)::bigint AS public_stream_hours,
        round(COALESCE(sum(v.seconds) FILTER (WHERE NOT v.members AND v.video_type IN ('upload','short')), 0) / 3600.0)::bigint AS public_video_hours
    FROM public.channel_directory cd
    LEFT JOIN (
        SELECT id, channel_id, members, video_type,
            COALESCE(CASE WHEN video_type = 'stream' THEN duration END, content_duration, duration) AS seconds
        FROM public.videos
    ) v ON v.channel_id = cd.user_id
    GROUP BY cd.name, cd.user_id;
"""

_UPDATE = """
    UPDATE public.videos
    SET video_type = COALESCE(%s, video_type),
        content_duration = COALESCE(%s, content_duration)
    WHERE id = %s
"""

def main() -> None:
    db = DB.PostgresClass()
    db.cursor.execute(_MIGRATION)
    db.database.commit()
    LOG.logger.info("videos.video_type/content_duration columns and channel_video_summary view are in place.")

    CFG.load_channel_directory(db.cursor)
    yt = C.YT_API(db)

    tally = {"stream":0,"upload":0,"short":0,"unresolved":0}
    for channel_name in CFG.CHANNELS_TO_PROCESS:
        CFG.select_channel(channel_name)
        db.cursor.execute("SELECT id, scheduled_start FROM public.videos WHERE channel_id = %s AND video_type IS NULL",(CFG.YT_USER_ID,))
        pending:dict[str,bool] = {vid_id:scheduled_start is not None for vid_id,scheduled_start in db.cursor.fetchall()}
        LOG.logger.info(f"{channel_name}: {len(pending):,} unclassified video(s).")
        ids = list(pending.keys())

        for i in range(0,len(ids),50):
            batch = ids[i:i+50]
            request = yt.api.videos().list(part="contentDetails,id,snippet,liveStreamingDetails",id=",".join(batch),maxResults=50)
            items:list[dict] = C._execute_with_retry(request).get("items",[])
            returned:set[str] = set()

            for item in items:
                returned.add(item["id"])
                video_type,content_duration = C.Classify_Video(item)
                db.cursor.execute(_UPDATE,(video_type,content_duration,item["id"]))
                tally[video_type or "unresolved"] += 1
                if item.get("liveStreamingDetails") is None:
                    # Classify_Video just hit youtube.com/shorts/ for this one -- pace those
                    # requests so a big backfill doesn't get the IP throttled.
                    time.sleep(random.uniform(0.5,1.5))

            for vid_id in batch:
                if vid_id in returned:
                    continue
                if pending[vid_id]:
                    db.cursor.execute(_UPDATE,("stream",None,vid_id))
                    tally["stream"] += 1
                else:
                    LOG.logger.warning(f"{vid_id}: not returned by the API and has no scheduled_start -- leaving video_type NULL.")
                    tally["unresolved"] += 1

            db.database.commit()

    LOG.logger.info(
        f"Backfill complete: {tally['stream']:,} stream(s), {tally['upload']:,} upload(s), {tally['short']:,} short(s), {tally['unresolved']:,} left unclassified."
    )

if __name__ == "__main__":
    main()
