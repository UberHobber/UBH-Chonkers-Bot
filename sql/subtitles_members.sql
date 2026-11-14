-- Generated from the live database schema by the maintainer's schema export script --
-- do not hand-edit. Change the database, then re-export, or edits are lost on the next export.
-- See sql/README.md for the order these files must be run in on a fresh database.

-- public.subtitles_ame_members definition

-- Drop table

-- DROP TABLE public.subtitles_ame_members;

CREATE TABLE public.subtitles_ame_members (
	video_id text NOT NULL,
	language text DEFAULT 'en'::text NOT NULL,
	seq int4 NOT NULL,
	start_ms int8 NOT NULL,
	end_ms int8 NOT NULL,
	text text NOT NULL,
	CONSTRAINT pk_subtitles_ame_members PRIMARY KEY (video_id, language, seq),
	CONSTRAINT fk_subtitles_video_id_ame_members FOREIGN KEY (video_id) REFERENCES public.videos(id)
);


-- public.subtitles_calli_members definition

-- Drop table

-- DROP TABLE public.subtitles_calli_members;

CREATE TABLE public.subtitles_calli_members (
	video_id text NOT NULL,
	language text DEFAULT 'en'::text NOT NULL,
	seq int4 NOT NULL,
	start_ms int8 NOT NULL,
	end_ms int8 NOT NULL,
	text text NOT NULL,
	CONSTRAINT pk_subtitles_calli_members PRIMARY KEY (video_id, language, seq),
	CONSTRAINT fk_subtitles_video_id_calli_members FOREIGN KEY (video_id) REFERENCES public.videos(id)
);


-- public.subtitles_gura_members definition

-- Drop table

-- DROP TABLE public.subtitles_gura_members;

CREATE TABLE public.subtitles_gura_members (
	video_id text NOT NULL,
	language text DEFAULT 'en'::text NOT NULL,
	seq int4 NOT NULL,
	start_ms int8 NOT NULL,
	end_ms int8 NOT NULL,
	text text NOT NULL,
	CONSTRAINT pk_subtitles_gura_members PRIMARY KEY (video_id, language, seq),
	CONSTRAINT fk_subtitles_video_id_gura_members FOREIGN KEY (video_id) REFERENCES public.videos(id)
);


-- public.subtitles_ina_members definition

-- Drop table

-- DROP TABLE public.subtitles_ina_members;

CREATE TABLE public.subtitles_ina_members (
	video_id text NOT NULL,
	language text DEFAULT 'en'::text NOT NULL,
	seq int4 NOT NULL,
	start_ms int8 NOT NULL,
	end_ms int8 NOT NULL,
	text text NOT NULL,
	CONSTRAINT pk_subtitles_ina_members PRIMARY KEY (video_id, language, seq),
	CONSTRAINT fk_subtitles_video_id_ina_members FOREIGN KEY (video_id) REFERENCES public.videos(id)
);


-- public.subtitles_kiara_members definition

-- Drop table

-- DROP TABLE public.subtitles_kiara_members;

CREATE TABLE public.subtitles_kiara_members (
	video_id text NOT NULL,
	language text DEFAULT 'en'::text NOT NULL,
	seq int4 NOT NULL,
	start_ms int8 NOT NULL,
	end_ms int8 NOT NULL,
	text text NOT NULL,
	CONSTRAINT pk_subtitles_kiara_members PRIMARY KEY (video_id, language, seq),
	CONSTRAINT fk_subtitles_video_id_kiara_members FOREIGN KEY (video_id) REFERENCES public.videos(id)
);
