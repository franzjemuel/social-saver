async def system_health(pool,queue_name="media_jobs"):
    q=await pool.fetchrow("select * from pgmq.metrics($1)",queue_name)
    workers=await pool.fetchrow(
      """select count(*) filter(where last_seen_at>now()-interval '2 minutes') as healthy,
      count(*) as total from worker_heartbeats""")
    sessions=await pool.fetch(
      """select platform,status,count(*) as count from provider_sessions
      group by platform,status order by platform,status""")
    watch_errors=await pool.fetchval("select count(*) from watches where status='error'")
    return {
      "queue":{"length":int(q["queue_length"]),"oldest_age_seconds":q["oldest_msg_age_sec"],
               "total_messages":int(q["total_messages"])},
      "workers":{"healthy":int(workers["healthy"] or 0),"total":int(workers["total"] or 0)},
      "provider_sessions":[dict(x) for x in sessions],
      "watch_errors":int(watch_errors or 0),
    }
