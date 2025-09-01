create materialized view nickname_summary as
	select matched_nickname as nickname, count(matched_nickname) as occurrences
	from nickname_matches
	group by matched_nickname