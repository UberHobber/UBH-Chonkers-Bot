CREATE TABLE public.emotes (
	id text NOT NULL,
	"name" text NOT NULL,
	url text NULL,
	custom bool NOT NULL,
	CONSTRAINT idx_emotes_id UNIQUE (id)
);

CREATE TABLE public.nicknames (
	nickname text NOT NULL,
	CONSTRAINT pk_nicknames_nickname PRIMARY KEY (nickname)
);

CREATE TABLE public.user_ids (
	id text NOT NULL,
	latest_name text NULL,
	custom_url text NULL,
	viewcount int8 NULL,
	subscribers int8 NULL,
	region text NULL,
	processed bool DEFAULT false NOT NULL,
	"exists" bool DEFAULT true NOT NULL,
	created timestamp NULL,
	CONSTRAINT pk_user_ids_id PRIMARY KEY (id)
);
CREATE INDEX idx_user_ids_created ON public.user_ids USING btree (created);
CREATE INDEX idx_user_ids_subscribers ON public.user_ids USING btree (subscribers);
CREATE INDEX idx_user_ids_viewcount ON public.user_ids USING btree (viewcount);

CREATE TABLE public.videos (
	id text NOT NULL,
	title text NULL,
	livestream bool NULL,
	islive bool NULL,
	processed bool DEFAULT false NOT NULL,
	publishedat timestamp NULL,
	scheduled_start timestamp NULL,
	start_time timestamp NULL,
	end_time timestamp NULL,
	duration int8 GENERATED ALWAYS AS (EXTRACT(epoch FROM end_time - start_time)) STORED NULL,
	CONSTRAINT pk_videos_id PRIMARY KEY (id)
);
CREATE INDEX idx_videos_duration ON public.videos USING btree (duration);
CREATE INDEX idx_videos_published ON public.videos USING btree (publishedat);
CREATE INDEX idx_videos_schduled ON public.videos USING btree (scheduled_start);
CREATE INDEX idx_videos_start_time ON public.videos USING btree (start_time);
CREATE INDEX itx_videos_end_time ON public.videos USING btree (end_time);

CREATE TABLE public.messages (
	message_id text NOT NULL,
	message text NULL,
	"timestamp" int8 NULL,
	time_in_seconds float4 NULL,
	"type" text NULL,
	video_id text NULL,
	user_id text NULL,
	user_name text NULL,
	user_member_status int8 NULL,
	ismoderator bool DEFAULT false NOT NULL,
	isverified bool DEFAULT false NOT NULL,
	isowner bool DEFAULT false NOT NULL,
	amount float4 NULL,
	currency text NULL,
	symbol text NULL,
	color text NULL,
	datetime timestamp GENERATED ALWAYS AS (
CASE
    WHEN "timestamp" > '1000000000000000'::bigint THEN to_timestamp(("timestamp" / 1000000)::double precision)
    ELSE to_timestamp("timestamp"::double precision)
END) STORED NULL,
	CONSTRAINT pk_messages_message_id PRIMARY KEY (message_id)
);
CREATE INDEX idx_mesages_user_id ON public.messages USING btree (user_id);
CREATE INDEX idx_mesages_video_id ON public.messages USING btree (video_id);
CREATE INDEX idx_messages_datetime ON public.messages USING btree (datetime);
CREATE INDEX idx_messages_message_text ON public.messages USING btree (message) WHERE (message ~ ':[a-zA-Z0-9_-]+:'::text);
CREATE INDEX idx_messages_null ON public.messages USING btree (message) WHERE (message IS NOT NULL);


-- public.messages foreign keys

ALTER TABLE public.messages ADD CONSTRAINT fk_messages_user_id_user_ids_id FOREIGN KEY (user_id) REFERENCES public.user_ids(id);
ALTER TABLE public.messages ADD CONSTRAINT fk_messages_video_id_videos_id FOREIGN KEY (video_id) REFERENCES public.videos(id);

CREATE TABLE public.nickname_matches (
	matched_nickname text NULL,
	message_id text NULL,
	index_start int8 NULL,
	index_end int8 NULL,
	CONSTRAINT unq_message_id_index_start_index_end UNIQUE (message_id, index_start, index_end)
);
CREATE INDEX idx_nickname_matches_message_id ON public.nickname_matches USING btree (message_id);


-- public.nickname_matches foreign keys

ALTER TABLE public.nickname_matches ADD CONSTRAINT fk_nickname_matches_message_id_messages_message_id FOREIGN KEY (message_id) REFERENCES public.messages(message_id);