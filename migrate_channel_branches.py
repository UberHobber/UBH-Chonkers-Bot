# Other Project Files
import modules.logconfig as LOG
import modules.Database as DB

"""
One-time migration: adds the branches lookup table and channel_directory.channel_type, and moves
the old 'Official' pseudo-branch channels into their real branch.

'Official' was never really a branch -- it marked channels that aren't a talent's own channel
(a branch's main channel like HoloEN, or a unit's channel like ReGLOSS). channel_type now
carries that distinction, so each official channel can sit in the branch/group it actually
belongs to, and "everything ReGLOSS" or "everything EN" includes its official channel too.

branches.sort_order drives the order Main.py processes channels in (see
load_channel_directory in modules/Settings.py) -- add a branch by inserting a row here, no
code change needed. Idempotent -- safe to re-run.

Once this has run, regenerate sql/ and SKILL.MD:
    python .claude/skills/chat-database/update_schema.py
"""

_MIGRATION = """
    CREATE TABLE IF NOT EXISTS public.branches (
        name text NOT NULL,
        sort_order int4 NOT NULL,
        CONSTRAINT pk_branches PRIMARY KEY (name),
        CONSTRAINT branches_sort_order_unique UNIQUE (sort_order)
    );
    INSERT INTO public.branches (name, sort_order) VALUES
        ('EN', 1), ('ID', 2), ('Stars_EN', 3), ('JP', 4), ('Stars', 5)
    ON CONFLICT (name) DO UPDATE SET sort_order = EXCLUDED.sort_order;

    ALTER TABLE public.channel_directory ADD COLUMN IF NOT EXISTS channel_type text DEFAULT 'talent' NOT NULL;
    ALTER TABLE public.channel_directory DROP CONSTRAINT IF EXISTS channel_directory_channel_type_check;
    ALTER TABLE public.channel_directory ADD CONSTRAINT channel_directory_channel_type_check
        CHECK (channel_type IN ('talent','official'));

    -- Keyed by db_suffix (stable) rather than name. Branch-level channels get group 'Branch';
    -- unit channels join their unit's existing group.
    UPDATE public.channel_directory cd
    SET channel_type = 'official', branch = m.branch, "group" = m.grp
    FROM (VALUES
        ('hololive', 'JP', 'Branch'),
        ('holoen',   'EN', 'Branch'),
        ('regloss',  'JP', 'ReGLOSS'),
        ('flowglow', 'JP', 'FlowGlow'),
        ('asobi',    'JP', 'Asobi')
    ) AS m(db_suffix, branch, grp)
    WHERE cd.db_suffix = m.db_suffix;

    ALTER TABLE public.channel_directory DROP CONSTRAINT IF EXISTS channel_directory_branches_fk;
    ALTER TABLE public.channel_directory ADD CONSTRAINT channel_directory_branches_fk
        FOREIGN KEY (branch) REFERENCES public.branches(name);
"""

def main() -> None:
    db = DB.PostgresClass()
    db.cursor.execute(_MIGRATION)
    db.database.commit()

    db.cursor.execute("""
        SELECT cd.name, cd.branch, cd."group", cd.channel_type, cd.debut
        FROM channel_directory cd JOIN branches b ON b.name = cd.branch
        WHERE cd.process
        ORDER BY b.sort_order, (cd.channel_type = 'official'), cd.debut, cd.name
    """)
    order = "\n".join(f"  {i+1:>3}. {name} ({branch} / {group}, {channel_type}, {debut})" for i,(name,branch,group,channel_type,debut) in enumerate(db.cursor.fetchall()))
    LOG.logger.info(f"Migration complete. Processing order is now:\n{order}")

if __name__ == "__main__":
    main()
