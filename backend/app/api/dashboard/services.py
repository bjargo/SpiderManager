import json
from datetime import datetime, timedelta
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from redis.asyncio import Redis

from app.api.projects.models import Project
from .schemas import DashboardStats, TrendData, RecentTask

class DashboardService:
    @staticmethod
    async def get_stats(redis: Redis, session: AsyncSession) -> DashboardStats:
        # 1. Total Spiders (Projects)
        result = await session.execute(select(func.count()).select_from(Project))
        total_spiders = result.scalar() or 0

        # 2. Nodes
        cursor = 0
        match_pattern = "node:status:*"
        online_nodes = 0
        total_nodes = 0 
        
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=match_pattern, count=100)
            if keys:
                values = await redis.mget(keys)
                for val in values:
                    if val:
                        try:
                            # 兼容 key 返回 bytes
                            data = json.loads(val.decode("utf-8") if isinstance(val, bytes) else val)
                            total_nodes += 1
                            if data.get("status", "online") == "online":
                                online_nodes += 1
                        except:
                            pass
            if int(cursor) == 0:
                break
                
        # 3. Tasks today
        now = datetime.now()
        today_start = datetime(now.year, now.month, now.day).timestamp()
        
        cursor = 0
        task_pattern = "task:status:*"
        tasks_today = 0
        failed_tasks_today = 0
        
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=task_pattern, count=100)
            if keys:
                values = await redis.mget(keys)
                for val in values:
                    if val:
                        try:
                            data = json.loads(val.decode("utf-8") if isinstance(val, bytes) else val)
                            start_time = data.get("start_time")
                            
                            if start_time and start_time >= today_start:
                                tasks_today += 1
                                if data.get("status") == "failed":
                                    failed_tasks_today += 1
                        except:
                            pass
            if int(cursor) == 0:
                break

        return DashboardStats(
            onlineNodes=online_nodes,
            totalNodes=total_nodes or online_nodes,
            totalSpiders=total_spiders,
            tasksToday=tasks_today,
            failedTasksToday=failed_tasks_today
        )

    @staticmethod
    async def get_trends(redis: Redis) -> List[TrendData]:
        # Last 7 days
        now = datetime.now()
        today = datetime(now.year, now.month, now.day)
        
        days_map = {}
        ordered_days = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            date_str = f"{d.month}-{d.day}"
            days_map[d.date()] = {"success": 0, "failure": 0, "date_str": date_str}
            ordered_days.append(d.date())
            
        cursor = 0
        task_pattern = "task:status:*"
        
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=task_pattern, count=500)
            if keys:
                values = await redis.mget(keys)
                for val in values:
                    if val:
                        try:
                            data = json.loads(val.decode("utf-8") if isinstance(val, bytes) else val)
                            start_time = data.get("start_time")
                            status = data.get("status")
                            if start_time and status in ["success", "failed"]:
                                task_date = datetime.fromtimestamp(start_time).date()
                                if task_date in days_map:
                                    if status == "success":
                                        days_map[task_date]["success"] += 1
                                    elif status == "failed":
                                        days_map[task_date]["failure"] += 1
                        except:
                            pass
            if int(cursor) == 0:
                break
                
        return [
            TrendData(
                date=days_map[d]["date_str"], 
                success=days_map[d]["success"], 
                failure=days_map[d]["failure"]
            )
            for d in ordered_days
        ]

    @staticmethod
    async def get_recent_tasks(redis: Redis, session: AsyncSession) -> List[RecentTask]:
        cursor = 0
        task_pattern = "task:status:*"
        all_tasks = []
        
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=task_pattern, count=500)
            if keys:
                values = await redis.mget(keys)
                for val in values:
                    if val:
                        try:
                            data = json.loads(val.decode("utf-8") if isinstance(val, bytes) else val)
                            if "start_time" in data:
                                all_tasks.append(data)
                        except:
                            continue
            if int(cursor) == 0:
                break
                
        # Sort by start_time descending
        all_tasks.sort(key=lambda x: x.get("start_time", 0), reverse=True)
        recent_5 = all_tasks[:5]
        
        # Optionally, map project_ids to project names if we want to be perfect
        project_ids = [t.get("project_id") for t in recent_5 if t.get("project_id")]
        project_name_map = {}
        if project_ids:
            result = await session.execute(select(Project).where(Project.project_id.in_(project_ids)))
            for proj in result.scalars():
                project_name_map[proj.project_id] = proj.name
        
        result_list = []
        for t in recent_5:
            start_ts = t.get("start_time")
            end_ts = t.get("end_time")
            start_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S") if start_ts else ""
            end_str = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S") if end_ts else None
            
            p_id = t.get("project_id", "unknown_project")
            spider_name = project_name_map.get(p_id, p_id)
            
            node_id = t.get("node_id") or t.get("node_target") or "public"
            node_name = f"Node-{node_id[:6]}" if node_id != "public" else "Public Queue"
            
            result_list.append(RecentTask(
                id=t.get("task_id", "unknown"),
                spiderName=spider_name,
                nodeName=node_name,
                status=t.get("status", "running"),
                startTime=start_str,
                endTime=end_str
            ))
            
        return result_list
