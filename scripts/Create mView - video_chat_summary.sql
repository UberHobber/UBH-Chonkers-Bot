create materialized view video_chat_summary as
	select video_id as Video_Id,
	count(*) as Messages,
	min(datetime) as First_Message,
	max(datetime) as Last_Message,
	extract(EPOCH from (max(datetime) - min(datetime))) as Chat_Duration,
	case
		when extract(minute from (max(datetime) - min(datetime))) > 0 then (count(*) / extract(minute from (max(datetime) - min(datetime))))
		else 0
	end as chats_per_min
	from messages
	group by video_id;