CREATE MATERIALIZED VIEW emote_summary AS
WITH row_chunks AS (
    SELECT 
        message_id,
        message,
        ntile(1000) OVER (ORDER BY message_id) as chunk_num
    FROM messages
    WHERE message ~ ':[a-zA-Z0-9_-]+:'
),
chunk_results AS (
    SELECT 
        match_array[1] as emote,
        COUNT(*) as chunk_count,
        chunk_num
    FROM row_chunks,
         LATERAL regexp_matches(message, ':([a-zA-Z0-9_-]+):', 'g') as match_array
    GROUP BY match_array[1], chunk_num
)
SELECT 
    emote,
    SUM(chunk_count) as uses
FROM chunk_results
GROUP BY emote
ORDER BY uses DESC;