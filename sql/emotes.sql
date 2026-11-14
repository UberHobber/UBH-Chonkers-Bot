-- Generated from the live database schema by the maintainer's schema export script --
-- do not hand-edit. Change the database, then re-export, or edits are lost on the next export.
-- See sql/README.md for the order these files must be run in on a fresh database.

-- public.emotes definition

-- Drop table

-- DROP TABLE public.emotes;

CREATE TABLE public.emotes (
	id text NOT NULL,
	"name" text NOT NULL,
	url text NULL,
	custom bool NOT NULL,
	channel_id text NOT NULL,
	CONSTRAINT pk_emotes PRIMARY KEY (channel_id, id),
	CONSTRAINT emotes_channel_directory_fk FOREIGN KEY (channel_id) REFERENCES public.channel_directory(user_id)
);
