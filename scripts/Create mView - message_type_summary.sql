create materialized view message_type_summary as
	select type as Message_Type, count(message_id) as Messages
	from messages
	group by type