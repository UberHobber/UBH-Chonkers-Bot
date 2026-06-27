import sys,os
import psycopg2
sys.path.append(os.getcwd())
import modules.logconfig as LOG
import modules.Database as DB

db = DB.PostgresClass()

talents = [
    "Calli",
    "Kiara",
    "Ina",
    "Gura",
    "Ame",
    "IRyS",
    "Kronii",
    "Bae",
    "Fauna",
    "Mumei",
    "Sana",
    "Shiori",
    "Biboo",
    "Nerissa",
    "FWMC",
    "ERB",
    "Gigi",
    "Ceci",
    "Raora"
]

# Generate all unique pairs of talents
talent_pairs = [
    (t1, t2)
    for i, t1 in enumerate(talents)
    for t2 in talents[i:]
]

def execute_step(description: str, sql: str):
    LOG.logger.info(f"Starting: {description}")
    try:
        db.cursor.execute(sql)
        db.database.commit()
        LOG.logger.info(f"Completed: {description}")
    except Exception as e:
        db.database.rollback()
        LOG.logger.error(f"Failed: {description} — {e}")
        raise

# ---------------------------------------------------------------------------
# user_id_summary
# ---------------------------------------------------------------------------
v1_view_name = "user_id_summary"
v1_new_view_name = f"{v1_view_name}_new"
v1_regular_view_name = f"{v1_view_name}_view"

talent_columns = "\n".join(
    f"""    COALESCE(t{i}.messages, 0::bigint) AS {t.lower()}_message_count,
    t{i}.first_message AS {t.lower()}_first_message,
    t{i}.latest_message AS {t.lower()}_latest_message,
    GREATEST(COALESCE(t{i}.highest_membership, 0::bigint)) AS {t.lower()}_highest_membership{"," if i < len(talents) else ""}"""
    for i, t in enumerate(talents, start=1)
)
total_messages = " + ".join(
    f"COALESCE(t{i}.messages, 0::bigint)"
    for i, _ in enumerate(talents, start=1)
)
v1_joins = "\n".join(
    f"     LEFT JOIN user_id_summary_{t.lower()} t{i} ON u.id = t{i}.user_id"
    for i, t in enumerate(talents, start=1)
)
v1_indexes = [
    {
        "new_name":   f"idx_{v1_new_view_name}_unique",
        "final_name": f"idx_{v1_view_name}_unique",
        "definition": f"CREATE UNIQUE INDEX idx_{v1_new_view_name}_unique ON public.{v1_new_view_name} USING btree (id);"
    },
]

# ---------------------------------------------------------------------------
# user_id_summary_2
# ---------------------------------------------------------------------------
v2_view_name = "user_id_summary_2"
v2_new_view_name = f"{v2_view_name}_new"
v2_regular_view_name = f"{v2_view_name}_view"

v2_union_blocks = "\n        UNION ALL\n".join(
    f"""         SELECT u.id,
            u.latest_name,
            '{t}'::text AS talent,
            COALESCE(t{i}.messages, 0::bigint) AS message_count,
            t{i}.first_message,
            t{i}.latest_message,
            COALESCE(t{i}.highest_membership, 0::bigint) AS highest_membership
           FROM user_ids u
             JOIN user_id_summary_{t.lower()} t{i} ON u.id = t{i}.user_id"""
    for i, t in enumerate(talents, start=1)
)
v2_indexes = [
    {
        "new_name":   f"idx_{v2_new_view_name}_composite",
        "final_name": f"idx_{v2_view_name}_composite",
        "definition": f"CREATE INDEX idx_{v2_new_view_name}_composite ON public.{v2_new_view_name} USING btree (id, talent);"
    },
    {
        "new_name":   f"idx_{v2_new_view_name}_global_rank",
        "final_name": f"idx_{v2_view_name}_global_rank",
        "definition": f"CREATE INDEX idx_{v2_new_view_name}_global_rank ON public.{v2_new_view_name} USING btree (global_rank);"
    },
    {
        "new_name":   f"idx_{v2_new_view_name}_talent",
        "final_name": f"idx_{v2_view_name}_talent",
        "definition": f"CREATE INDEX idx_{v2_new_view_name}_talent ON public.{v2_new_view_name} USING btree (talent);"
    },
    {
        "new_name":   f"idx_{v2_new_view_name}_talent_rank",
        "final_name": f"idx_{v2_view_name}_talent_rank",
        "definition": f"CREATE INDEX idx_{v2_new_view_name}_talent_rank ON public.{v2_new_view_name} USING btree (talent, talent_rank);"
    },
    {
        "new_name":   f"idx_{v2_new_view_name}_user_id",
        "final_name": f"idx_{v2_view_name}_user_id",
        "definition": f"CREATE INDEX idx_{v2_new_view_name}_user_id ON public.{v2_new_view_name} USING btree (id);"
    },
]

# ---------------------------------------------------------------------------
# message_summary_by_month
# ---------------------------------------------------------------------------
v3_view_name = "message_summary_by_month"
v3_new_view_name = f"{v3_view_name}_new"
v3_regular_view_name = f"{v3_view_name}_view"

v3_union_blocks = "\n        UNION ALL\n".join(
    f"""         SELECT '{t}'::text AS talent_name,
            date_trunc('month'::text, messages_{t.lower()}.datetime) AS month,
            count(*) AS message_count
           FROM messages_{t.lower()}
          GROUP BY (date_trunc('month'::text, messages_{t.lower()}.datetime))"""
    for t in talents
)
v3_indexes = [
    {
        "new_name":   f"idx_{v3_new_view_name}_talent_name_month",
        "final_name": f"idx_{v3_view_name}_talent_name_month",
        "definition": f"CREATE INDEX idx_{v3_new_view_name}_talent_name_month ON public.{v3_new_view_name} USING btree (talent_name, month);"
    },
]

# ---------------------------------------------------------------------------
# video_chat_summary
# ---------------------------------------------------------------------------
v4_view_name = "video_chat_summary"
v4_new_view_name = f"{v4_view_name}_new"
v4_regular_view_name = f"{v4_view_name}_view"

v4_union_blocks = "\n        UNION ALL\n".join(
    f"""         SELECT '{t}'::text AS talent,
            v.video_id,
            v.title,
            v.publishedat,
            v.duration,
            COALESCE(v.messages, 0::bigint) AS messages,
            v.first_message,
            v.last_message,
            COALESCE(v.chat_duration, 0::numeric) AS chat_duration,
            COALESCE(v.chats_per_min, 0::numeric) AS chats_per_min,
            COALESCE(v.unique_users, 0::bigint) AS unique_users,
            v.video_rank
           FROM video_chat_summary_{t.lower()} v"""
    for t in talents
)
v4_indexes = [
    {
        "new_name":   f"idx_{v4_new_view_name}_global_rank",
        "final_name": f"idx_{v4_view_name}_global_rank",
        "definition": f"CREATE INDEX idx_{v4_new_view_name}_global_rank ON public.{v4_new_view_name} USING btree (global_rank);"
    },
    {
        "new_name":   f"idx_{v4_new_view_name}_talent",
        "final_name": f"idx_{v4_view_name}_talent",
        "definition": f"CREATE INDEX idx_{v4_new_view_name}_talent ON public.{v4_new_view_name} USING btree (talent);"
    },
    {
        "new_name":   f"idx_{v4_new_view_name}_video_id",
        "final_name": f"idx_{v4_view_name}_video_id",
        "definition": f"CREATE INDEX idx_{v4_new_view_name}_video_id ON public.{v4_new_view_name} USING btree (video_id);"
    },
]

# ---------------------------------------------------------------------------
# user_overlap (single scan of user_id_summary_new, no self-join)
# ---------------------------------------------------------------------------
v5_view_name = "user_overlap"
v5_new_view_name = f"{v5_view_name}_new"
v5_regular_view_name = f"{v5_view_name}_view"

overlap_unions = "\n    UNION ALL\n".join(
    f"""    SELECT '{t1}'::text AS talent_1, '{t2}'::text AS talent_2,
        count(*) AS shared_users
    FROM public.{v1_new_view_name}
    WHERE {t1.lower()}_message_count > 0 AND {t2.lower()}_message_count > 0"""
    for t1, t2 in talent_pairs
)
v5_indexes = []

# ---------------------------------------------------------------------------
# user_overlap_stats (single scan of user_id_summary_2_new, no view wrapper)
# ---------------------------------------------------------------------------
v6_view_name = "user_overlap_stats"
v6_new_view_name = f"{v6_view_name}_new"
v6_regular_view_name = f"{v6_view_name}_view"
v6_indexes = []

# ---------------------------------------------------------------------------
# Collect all indexes
# ---------------------------------------------------------------------------
all_indexes = v1_indexes + v2_indexes + v3_indexes + v4_indexes + v5_indexes + v6_indexes

all_index_creates = "\n".join(idx["definition"] for idx in all_indexes)
all_index_renames = "\n".join(
    f"ALTER INDEX {idx['new_name']} RENAME TO {idx['final_name']};"
    for idx in all_indexes
)

# ---------------------------------------------------------------------------
# Step 0: Clean up any previous failed run
# ---------------------------------------------------------------------------
execute_step("Drop previous failed run", f"""
    DROP MATERIALIZED VIEW IF EXISTS public.{v6_new_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v5_new_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v4_new_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v3_new_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v2_new_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v1_new_view_name};
""")

# ---------------------------------------------------------------------------
# Step 1: Create materialized views (one at a time)
# ---------------------------------------------------------------------------
execute_step("Create user_id_summary_new", f"""
    CREATE MATERIALIZED VIEW public.{v1_new_view_name}
    TABLESPACE pg_default
    AS SELECT u.id,
        u.latest_name,
        {total_messages} AS total_messages,
    {talent_columns}
       FROM user_ids u
    {v1_joins}
    WITH DATA;
""")

execute_step("Create user_id_summary_2_new", f"""
    CREATE MATERIALIZED VIEW public.{v2_new_view_name}
    TABLESPACE pg_default
    AS SELECT id,
        latest_name,
        talent,
        message_count,
        first_message,
        latest_message,
        highest_membership,
        rank() OVER (PARTITION BY talent ORDER BY message_count DESC) AS talent_rank,
        rank() OVER (ORDER BY message_count DESC) AS global_rank
       FROM (
    {v2_union_blocks}
    ) combined_data
    WITH DATA;
""")

execute_step("Create message_summary_by_month_new", f"""
    CREATE MATERIALIZED VIEW public.{v3_new_view_name}
    TABLESPACE pg_default
    AS WITH all_messages AS (
    {v3_union_blocks}
            )
     SELECT talent_name,
        month,
        message_count
       FROM all_messages
      ORDER BY talent_name, month
    WITH DATA;
""")

execute_step("Create video_chat_summary_new", f"""
    CREATE MATERIALIZED VIEW public.{v4_new_view_name}
    TABLESPACE pg_default
    AS SELECT talent,
        video_id,
        title,
        publishedat,
        duration,
        messages,
        first_message,
        last_message,
        chat_duration,
        chats_per_min,
        unique_users,
        video_rank,
        rank() OVER (ORDER BY messages DESC) AS global_rank
       FROM (
    {v4_union_blocks}
    ) combined_data
    WITH DATA;
""")

execute_step("Create user_overlap_new", f"""
    CREATE MATERIALIZED VIEW public.{v5_new_view_name}
    TABLESPACE pg_default
    AS
    {overlap_unions}
    WITH DATA;
""")

execute_step("Create user_overlap_stats_new", f"""
    CREATE MATERIALIZED VIEW public.{v6_new_view_name}
    TABLESPACE pg_default
    AS SELECT id,
        count(DISTINCT talent) AS number_of_talents,
        array_agg(DISTINCT talent ORDER BY talent) AS talents,
        sum(message_count) AS message_count
       FROM public.{v2_new_view_name}
       GROUP BY id
    WITH DATA;
""")

# ---------------------------------------------------------------------------
# Step 2: Create indexes (each one individually)
# ---------------------------------------------------------------------------
for idx in all_indexes:
    execute_step(f"Create index {idx['new_name']}", idx["definition"])

# ---------------------------------------------------------------------------
# Step 3: Swap all views atomically (keep as one transaction)
# ---------------------------------------------------------------------------
execute_step("Swap all views", f"""
    -- Drop regular views first
    DROP VIEW IF EXISTS public.{v6_regular_view_name};
    DROP VIEW IF EXISTS public.{v5_regular_view_name};
    DROP VIEW IF EXISTS public.{v4_regular_view_name};
    DROP VIEW IF EXISTS public.{v3_regular_view_name};
    DROP VIEW IF EXISTS public.{v2_regular_view_name};
    DROP VIEW IF EXISTS public.{v1_regular_view_name};
    -- Drop materialized views in dependency order (dependents first)
    DROP MATERIALIZED VIEW IF EXISTS public.{v6_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v5_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v4_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v3_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v1_view_name};
    DROP MATERIALIZED VIEW IF EXISTS public.{v2_view_name};
    -- Rename new views into place
    ALTER MATERIALIZED VIEW public.{v1_new_view_name} RENAME TO {v1_view_name};
    ALTER MATERIALIZED VIEW public.{v2_new_view_name} RENAME TO {v2_view_name};
    ALTER MATERIALIZED VIEW public.{v3_new_view_name} RENAME TO {v3_view_name};
    ALTER MATERIALIZED VIEW public.{v4_new_view_name} RENAME TO {v4_view_name};
    ALTER MATERIALIZED VIEW public.{v5_new_view_name} RENAME TO {v5_view_name};
    ALTER MATERIALIZED VIEW public.{v6_new_view_name} RENAME TO {v6_view_name};
""")

# ---------------------------------------------------------------------------
# Step 4: Rename indexes
# ---------------------------------------------------------------------------
for idx in all_indexes:
    execute_step(
        f"Rename index {idx['new_name']} → {idx['final_name']}",
        f"ALTER INDEX {idx['new_name']} RENAME TO {idx['final_name']};"
    )

# ---------------------------------------------------------------------------
# Step 5: Recreate regular views
# ---------------------------------------------------------------------------
regular_views = [
    (v1_regular_view_name, v1_view_name),
    (v2_regular_view_name, v2_view_name),
    (v3_regular_view_name, v3_view_name),
    (v4_regular_view_name, v4_view_name),
    (v5_regular_view_name, v5_view_name),
    (v6_regular_view_name, v6_view_name),
]
for view_name, source in regular_views:
    execute_step(
        f"Recreate view {view_name}",
        f"CREATE OR REPLACE VIEW public.{view_name} AS SELECT * FROM public.{source};"
    )

LOG.logger.info("All steps completed successfully.")