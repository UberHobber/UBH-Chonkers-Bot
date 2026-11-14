-- Generated from the live database schema by the maintainer's schema export script --
-- do not hand-edit. Change the database, then re-export, or edits are lost on the next export.
-- See sql/README.md for the order these files must be run in on a fresh database.

-- public.nicknames definition

-- Drop table

-- DROP TABLE public.nicknames;

CREATE TABLE public.nicknames (
	nickname text NOT NULL,
	channel_id text NOT NULL,
	CONSTRAINT pk_nicknames PRIMARY KEY (channel_id, nickname),
	CONSTRAINT nicknames_channel_directory_fk FOREIGN KEY (channel_id) REFERENCES public.channel_directory(user_id)
);


-- public.nickname_matches definition

-- Drop table

-- DROP TABLE public.nickname_matches;

CREATE TABLE public.nickname_matches (
	matched_nickname text NULL,
	message_id text NULL,
	index_start int8 NULL,
	index_end int8 NULL,
	channel_id text NOT NULL,
	CONSTRAINT unq_nickname_matches_channel_message_index UNIQUE (channel_id, message_id, index_start, index_end),
	CONSTRAINT nickname_matches_channel_directory_fk FOREIGN KEY (channel_id) REFERENCES public.channel_directory(user_id)
);
CREATE INDEX idx_nickname_matches_message_id ON public.nickname_matches USING btree (message_id);
