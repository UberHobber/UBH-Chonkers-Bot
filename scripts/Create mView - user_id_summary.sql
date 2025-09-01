create materialized view user_id_summary as
	select
	m.user_id as User_ID,
	u.latest_name as Username,
	count(*) as Messages,
	count(distinct user_name) as Username_Count,
	max(user_member_status) as Highest_Membership
	from
	messages m
	inner join
	user_ids u on m.user_id = u.id
	group by
	m.user_id, u.latest_name
	order by
	Messages desc;

create unique index idx_user_id_summary_user_id
on user_id_summary (User_ID);