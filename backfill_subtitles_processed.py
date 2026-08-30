# Native Stuff
# (none)

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG
import modules.Database as DB

"""
One-time migration: sets videos.subtitles_processed = true for every video that already has
at least one row in its subtitles table, so the new recurring pipeline (Subtitles.py) doesn't
redownload the entire historical catalog on its first run after the subtitles_processed
column is added.

Run once, after adding the videos.subtitles_processed column to the live database (see the
chat-database skill's schema-change workflow -- sql/README.md), before the first Main.py run
that includes Subtitles.py's pass.

Distinct from Subtitles.py on purpose: this is a one-off flag migration over historical data,
not a permanent stage of every run.
"""

def main() -> None:
    db = DB.PostgresClass()
    CFG.load_channel_directory(db.cursor)

    total = 0
    for channel_name in CFG.CHANNELS_TO_PROCESS:
        CFG.select_channel(channel_name)

        if CFG.GET_MEMBERS_ONLY is False:
            query = (
                'UPDATE videos SET subtitles_processed = true '
                'WHERE channel_id = %s AND members = false '
                'AND id IN (SELECT DISTINCT video_id FROM subtitles WHERE channel_id = %s)'
            )
            db.cursor.execute(query,(CFG.YT_USER_ID,CFG.YT_USER_ID))
        else:
            subtitles_table = CFG.DB_TABLES["subtitles"]
            if not DB.TableExists(db.cursor,subtitles_table):
                LOG.logger.info(f"{channel_name}: no {subtitles_table} table, skipping.")
                continue
            query = (
                'UPDATE videos SET subtitles_processed = true '
                f'WHERE channel_id = %s AND members = true '
                f'AND id IN (SELECT DISTINCT video_id FROM {subtitles_table})'
            )
            db.cursor.execute(query,(CFG.YT_USER_ID,))

        LOG.logger.info(f"{channel_name}: marked {db.cursor.rowcount:,} video(s) subtitles_processed.")
        total += db.cursor.rowcount
        db.database.commit()

    LOG.logger.info(f"Backfill complete. {total:,} video(s) marked subtitles_processed across {len(CFG.CHANNELS_TO_PROCESS):,} channel(s).")

if __name__ == "__main__":
    main()
