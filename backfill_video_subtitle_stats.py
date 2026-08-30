# Native Stuff
# (none)

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG
import modules.Database as DB

"""
One-time backfill: populates video_subtitle_stats for every video/language that already has
subtitle rows, aggregating directly over the existing cue table(s) in SQL rather than looping
back through Get_Subtitles. Needed because record_subtitle_stats() (see
sql/video_subtitle_stats.sql) only runs going forward, on newly fetched cues -- videos whose
captions were already downloaded before video_subtitle_stats existed would otherwise never
get a row.

Run once, after creating video_subtitle_stats on the live database (EnsureSchema does this
automatically on next startup -- see sql/README.md).
"""

# {channel_id_expr} is either "s.channel_id" (public subtitles, which has the column) or a
# "%s" placeholder bound to CFG.YT_USER_ID (members-only subtitles_<suffix>_members tables,
# which don't -- same split as the subtitles table itself). {where_clause} filters the shared
# public.subtitles table down to one channel; the per-talent members tables need no filter,
# they already hold exactly one channel's rows.
_UPSERT = """
    INSERT INTO video_subtitle_stats
        (video_id, channel_id, language, total_subtitles, total_words, speech_duration_ms,
         subtitles_per_min, words_per_min, speech_coverage_pct)
    SELECT s.video_id, MAX({channel_id_expr}) AS channel_id, s.language,
        COUNT(*),
        SUM(array_length(regexp_split_to_array(trim(s.text), '\\s+'), 1)),
        SUM(s.end_ms - s.start_ms),
        ROUND((COUNT(*) / (NULLIF(v.duration,0) / 60.0))::numeric, 2),
        ROUND((SUM(array_length(regexp_split_to_array(trim(s.text), '\\s+'), 1)) / (NULLIF(v.duration,0) / 60.0))::numeric, 2),
        ROUND((SUM(s.end_ms - s.start_ms) / 1000.0 / NULLIF(v.duration,0) * 100)::numeric, 2)
    FROM {table} s
    JOIN videos v ON v.id = s.video_id
    {where_clause}
    GROUP BY s.video_id, s.language, v.duration
    ON CONFLICT (video_id, language) DO UPDATE
    SET channel_id = EXCLUDED.channel_id,
        total_subtitles = EXCLUDED.total_subtitles,
        total_words = EXCLUDED.total_words,
        speech_duration_ms = EXCLUDED.speech_duration_ms,
        subtitles_per_min = EXCLUDED.subtitles_per_min,
        words_per_min = EXCLUDED.words_per_min,
        speech_coverage_pct = EXCLUDED.speech_coverage_pct
"""

def main() -> None:
    db = DB.PostgresClass()
    CFG.load_channel_directory(db.cursor)

    total = 0
    for channel_name in CFG.CHANNELS_TO_PROCESS:
        CFG.select_channel(channel_name)
        subtitles_table = CFG.DB_TABLES["subtitles"]

        if not DB.TableExists(db.cursor,subtitles_table):
            LOG.logger.info(f"{channel_name}: no {subtitles_table} table, skipping.")
            continue

        if CFG.GET_MEMBERS_ONLY is False:
            query = _UPSERT.format(table=subtitles_table,channel_id_expr="s.channel_id",where_clause="WHERE s.channel_id = %s")
            db.cursor.execute(query,(CFG.YT_USER_ID,))
        else:
            query = _UPSERT.format(table=subtitles_table,channel_id_expr="%s",where_clause="")
            db.cursor.execute(query,(CFG.YT_USER_ID,))

        LOG.logger.info(f"{channel_name}: upserted {db.cursor.rowcount:,} video/language row(s).")
        total += db.cursor.rowcount
        db.database.commit()

    LOG.logger.info(f"Backfill complete. {total:,} video_subtitle_stats row(s) upserted across {len(CFG.CHANNELS_TO_PROCESS):,} channel(s).")

if __name__ == "__main__":
    main()
