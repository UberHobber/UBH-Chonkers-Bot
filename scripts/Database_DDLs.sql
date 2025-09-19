-- Table Creation

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
ALTER TABLE public.nickname_matches ADD CONSTRAINT fk_nickname_matches_message_id_messages_message_id FOREIGN KEY (message_id) REFERENCES public.messages(message_id);

-- Materialized Views

CREATE MATERIALIZED VIEW public.emote_summary
TABLESPACE pg_default
AS WITH row_chunks AS (
         SELECT messages.message_id,
            messages.message,
            ntile(1000) OVER (ORDER BY messages.message_id) AS chunk_num
           FROM messages
          WHERE messages.message ~ ':[a-zA-Z0-9_-]+:'::text
        ), chunk_results AS (
         SELECT match_array.match_array[1] AS emote,
            count(*) AS chunk_count,
            row_chunks.chunk_num
           FROM row_chunks,
            LATERAL regexp_matches(row_chunks.message, ':([a-zA-Z0-9_-]+):'::text, 'g'::text) match_array(match_array)
          GROUP BY (match_array.match_array[1]), row_chunks.chunk_num
        )
 SELECT emote,
    sum(chunk_count) AS uses
   FROM chunk_results
  GROUP BY emote
  ORDER BY (sum(chunk_count)) DESC
WITH DATA;

CREATE MATERIALIZED VIEW public.message_type_summary
TABLESPACE pg_default
AS SELECT type AS message_type,
    count(message_id) AS messages
   FROM messages
  GROUP BY type
WITH DATA;

CREATE MATERIALIZED VIEW public.nickname_summary
TABLESPACE pg_default
AS SELECT matched_nickname AS nickname,
    count(matched_nickname) AS occurrences
   FROM nickname_matches
  GROUP BY matched_nickname
WITH DATA;

CREATE MATERIALIZED VIEW public.user_id_summary
TABLESPACE pg_default
AS SELECT m.user_id,
    u.latest_name AS username,
    count(*) AS messages,
    min(m.datetime) AS first_message,
    max(m.datetime) AS latest_message,
    count(DISTINCT m.user_name) AS username_count,
    max(m.user_member_status) AS highest_membership,
        CASE
            WHEN count(*) = 1 THEN '1 message'::text
            WHEN count(*) >= 2 AND count(*) <= 4 THEN '2-4 messages'::text
            WHEN count(*) >= 5 AND count(*) <= 9 THEN '5-9 messages'::text
            WHEN count(*) >= 10 AND count(*) <= 19 THEN '10-19 messages'::text
            WHEN count(*) >= 20 AND count(*) <= 29 THEN '20-29 messages'::text
            WHEN count(*) >= 30 AND count(*) <= 39 THEN '30-39 messages'::text
            WHEN count(*) >= 40 AND count(*) <= 49 THEN '40-49 messages'::text
            WHEN count(*) >= 50 AND count(*) <= 99 THEN '50-99 messages'::text
            WHEN count(*) >= 100 AND count(*) <= 199 THEN '100-199 messages'::text
            WHEN count(*) >= 200 AND count(*) <= 999 THEN '200-999 messages'::text
            WHEN count(*) >= 1000 AND count(*) <= 1999 THEN '1000-1999 messages'::text
            WHEN count(*) >= 2000 AND count(*) <= 9999 THEN '2000-9999 messages'::text
            WHEN count(*) >= 10000 AND count(*) <= 19999 THEN '10000-19999 messages'::text
            WHEN count(*) >= 20000 AND count(*) <= 99999 THEN '20000-99999 messages'::text
            WHEN count(*) > 100000 THEN '100000+ messages'::text
            ELSE 'No messages'::text
        END AS message_bucket
   FROM messages m
     JOIN user_ids u ON m.user_id = u.id
  GROUP BY m.user_id, u.latest_name
  ORDER BY (count(*)) DESC
WITH DATA;
CREATE UNIQUE INDEX idx_user_id_summary_user_id ON public.user_id_summary USING btree (user_id);

CREATE MATERIALIZED VIEW public.user_message_buckets
TABLESPACE pg_default
AS WITH user_message_counts AS (
         SELECT messages.user_id,
            count(*) AS total_messages
           FROM messages
          GROUP BY messages.user_id
        )
 SELECT
        CASE
            WHEN total_messages = 1 THEN '1 message'::text
            WHEN total_messages >= 2 AND total_messages <= 4 THEN '2-4 messages'::text
            WHEN total_messages >= 5 AND total_messages <= 9 THEN '5-9 messages'::text
            WHEN total_messages >= 10 AND total_messages <= 19 THEN '10-19 messages'::text
            WHEN total_messages >= 20 AND total_messages <= 29 THEN '20-29 messages'::text
            WHEN total_messages >= 30 AND total_messages <= 39 THEN '30-39 messages'::text
            WHEN total_messages >= 40 AND total_messages <= 49 THEN '40-49 messages'::text
            WHEN total_messages >= 50 AND total_messages <= 99 THEN '50-99 messages'::text
            WHEN total_messages >= 100 AND total_messages <= 199 THEN '100-199 messages'::text
            WHEN total_messages >= 200 AND total_messages <= 999 THEN '200-999 messages'::text
            WHEN total_messages >= 1000 AND total_messages <= 1999 THEN '1000-1999 messages'::text
            WHEN total_messages >= 2000 AND total_messages <= 9999 THEN '2000-9999 messages'::text
            WHEN total_messages >= 10000 AND total_messages <= 19999 THEN '10000-19999 messages'::text
            WHEN total_messages >= 20000 AND total_messages <= 99999 THEN '20000-99999 messages'::text
            WHEN total_messages > 100000 THEN '100000+ messages'::text
            ELSE 'No messages'::text
        END AS message_bucket,
    count(*) AS users_in_bucket,
    min(total_messages) AS min_messages,
    max(total_messages) AS max_messages,
    avg(total_messages)::numeric(10,2) AS avg_messages
   FROM user_message_counts
  GROUP BY (
        CASE
            WHEN total_messages = 1 THEN '1 message'::text
            WHEN total_messages >= 2 AND total_messages <= 4 THEN '2-4 messages'::text
            WHEN total_messages >= 5 AND total_messages <= 9 THEN '5-9 messages'::text
            WHEN total_messages >= 10 AND total_messages <= 19 THEN '10-19 messages'::text
            WHEN total_messages >= 20 AND total_messages <= 29 THEN '20-29 messages'::text
            WHEN total_messages >= 30 AND total_messages <= 39 THEN '30-39 messages'::text
            WHEN total_messages >= 40 AND total_messages <= 49 THEN '40-49 messages'::text
            WHEN total_messages >= 50 AND total_messages <= 99 THEN '50-99 messages'::text
            WHEN total_messages >= 100 AND total_messages <= 199 THEN '100-199 messages'::text
            WHEN total_messages >= 200 AND total_messages <= 999 THEN '200-999 messages'::text
            WHEN total_messages >= 1000 AND total_messages <= 1999 THEN '1000-1999 messages'::text
            WHEN total_messages >= 2000 AND total_messages <= 9999 THEN '2000-9999 messages'::text
            WHEN total_messages >= 10000 AND total_messages <= 19999 THEN '10000-19999 messages'::text
            WHEN total_messages >= 20000 AND total_messages <= 99999 THEN '20000-99999 messages'::text
            WHEN total_messages > 100000 THEN '100000+ messages'::text
            ELSE 'No messages'::text
        END)
  ORDER BY (min(total_messages))
WITH DATA;

CREATE MATERIALIZED VIEW public.user_name_summary
TABLESPACE pg_default
AS SELECT user_name,
    user_id,
    count(message) AS messages
   FROM messages
  GROUP BY user_name, user_id
WITH DATA;

CREATE MATERIALIZED VIEW public.video_chat_summary
TABLESPACE pg_default
AS SELECT video_id,
    count(*) AS messages,
    min(datetime) AS first_message,
    max(datetime) AS last_message,
    EXTRACT(epoch FROM max(datetime) - min(datetime)) AS chat_duration,
        CASE
            WHEN EXTRACT(epoch FROM max(datetime) - min(datetime)) > 0::numeric THEN count(*)::numeric / (EXTRACT(epoch FROM max(datetime) - min(datetime)) / 60::numeric)
            ELSE 0::numeric
        END AS chats_per_min,
    count(DISTINCT user_id) AS unique_users
   FROM messages
  GROUP BY video_id
WITH DATA;

CREATE OR REPLACE VIEW public.emote_summary_view
AS SELECT emote,
    uses
   FROM emote_summary;

CREATE OR REPLACE VIEW public.message_type_summary_view
AS SELECT message_type,
    messages
   FROM message_type_summary;

CREATE OR REPLACE VIEW public.nickname_summary_view
AS SELECT nickname,
    occurrences
   FROM nickname_summary;

CREATE OR REPLACE VIEW public.user_id_summary_view
AS SELECT user_id,
    username,
    messages,
    first_message,
    latest_message,
    username_count,
    highest_membership,
    message_bucket
   FROM user_id_summary;

CREATE OR REPLACE VIEW public.user_message_buckets_view
AS SELECT message_bucket,
    users_in_bucket,
    min_messages,
    max_messages,
    avg_messages
   FROM user_message_buckets;

CREATE OR REPLACE VIEW public.user_name_summary_view
AS SELECT user_name,
    user_id,
    messages
   FROM user_name_summary;

CREATE OR REPLACE VIEW public.video_chat_summary_view
AS SELECT video_id,
    messages,
    first_message,
    last_message,
    chat_duration,
    chats_per_min,
    unique_users
   FROM video_chat_summary;