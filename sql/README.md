# Database bootstrap scripts

Each `.sql` file here is a runnable DDL script for one cluster of tables (plus any
materialized views/functions that belong to it). They're generated directly from the live
`YTDB_Public` schema by [`.claude/skills/chat-database/update_schema.py`](../.claude/skills/chat-database/update_schema.py)
— **do not hand-edit these files**; edit the `NARRATIVE` dict in that script instead (for
rationale comments) or the schema itself (for structural changes), then re-run it:

```
python .claude/skills/chat-database/update_schema.py
```

That same run also regenerates [`SKILL.MD`](../.claude/skills/chat-database/SKILL.MD) from
the same live introspection, so the two can never drift apart.

`modules.Database.EnsureSchema` (called once at startup, see `Main.py`) runs these files
automatically against whatever database it connects to — it checks each file's "anchor"
table/matview and only runs a file if that's missing, so this is safe to leave in place
permanently rather than being a one-time setup step. The `*_members` files are only run when
`CFG.GET_MEMBERS_ONLY` is `True`, so a public-only deployment never gets members-only tables
it doesn't need.

## Run order

Later files have foreign keys pointing at tables created by earlier ones:

1. `channel_directory.sql`
2. `user_ids.sql`
3. `videos.sql`
4. `tags.sql`
5. `emotes.sql`
6. `messages.sql` — must come before `nicknames.sql`/`nicknames_members.sql`/`messages_members.sql`
7. `nicknames.sql`
8. `subtitles.sql`
9. `user_first_channel_message_stats.sql` — must come before `video_message_stats.sql`:
   `record_message_stats()`/`record_message_stats_batch()` (defined in the latter) write to
   `user_first_message`/`user_first_channel_message` (defined in the former).
10. `video_message_stats.sql`
11. `user_global_message_rankings.sql` — its materialized views select from
    `user_first_channel_message`, created in step 8.

**Members-only files** (only needed/run if you're tracking members-only content — see below):

12. `emotes_members.sql`
13. `messages_members.sql` — must come before `nicknames_members.sql`: its
    `nickname_matches_<talent>_members` tables FK to `messages_<talent>_members(message_id)`.
14. `nicknames_members.sql`
15. `subtitles_members.sql`

After creating the `channel_directory` row(s) for whichever channels you're tracking, also
create their `messages_<db_suffix>` partition of `public.messages` — see
`modules.Database.AddChannel`/`EnsureMessagesPartition`, or add it manually the same way the
existing per-talent partitions in `messages.sql` are declared.

The `*_<talent>_members` tables (`emotes`, `messages`, `nickname_matches`, `subtitles`) are
hardcoded per-talent (one table per name, e.g. `subtitles_kiara_members`) rather than created
dynamically per channel — see the note at the top of `channel_directory.sql` for why these
predate the unified `channel_id` schema. Adding members-only support for a talent that isn't
already one of the five in these files means adding its table to the relevant `_members.sql`
file (and to `FILE_GROUPS` in `update_schema.py`) by hand.
