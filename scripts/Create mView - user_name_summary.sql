create materialized view user_name_summary as
	select user_name as User_Name, user_id as User_ID,count(message) as Messages
	from messages
	group by user_name,user_id