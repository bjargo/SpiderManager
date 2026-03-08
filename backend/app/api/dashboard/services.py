import json
from datetime import datetime, timedelta
from app.core.timezone import now as now_tz
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from redis.asyncio import Redis
from app.api.spiders.models import Spider
from app.api.tasks.models import SpiderTask
from .schemas import DashboardStats, TrendData, RecentTask
import logging

logger = logging.getLogger(__name__)


class DashboardService:
    @staticmethod
    async def get_stats(redis: Redis, session: AsyncSession) -> DashboardStats:
        # 1. Total Spiders
        result = await session.execute(select(func.count()).select_from(Spider))
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
                        except Exception as e:
                            logger.error(f"Error parsing node status data: {e}", exc_info=True)
            if int(cursor) == 0:
                break
                
        # 3. Tasks today
        now = now_tz()
        today_start = datetime(now.year, now.month, now.day)
        
        # 今日任务总数
        tasks_today_query = select(func.count()).select_from(SpiderTask).where(
            SpiderTask.created_at >= today_start
        )
        tasks_today_result = await session.execute(tasks_today_query)
        tasks_today = tasks_today_result.scalar() or 0

        # 今日失败任务
        failed_tasks_today_query = select(func.count()).select_from(SpiderTask).where(
            and_(
                SpiderTask.created_at >= today_start,
                SpiderTask.status.in_(["failed", "timeout", "error"])
            )
        )
        failed_tasks_today_result = await session.execute(failed_tasks_today_query)
        failed_tasks_today = failed_tasks_today_result.scalar() or 0

        return DashboardStats(
            onlineNodes=online_nodes,
            totalNodes=total_nodes or online_nodes,
            totalSpiders=total_spiders,
            tasksToday=tasks_today,
            failedTasksToday=failed_tasks_today
        )

    @staticmethod
    async def get_trends(redis: Redis, session: AsyncSession) -> List[TrendData]:
        # Last 7 days
        now = now_tz()
        today = datetime(now.year, now.month, now.day)
        last_7_days_start = today - timedelta(days=6)
        
        days_map = {}
        ordered_days = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            date_str = f"{d.month}-{d.day}"
            days_map[d.date()] = {"success": 0, "failure": 0, "date_str": date_str}
            ordered_days.append(d.date())
            
        # 查询最近 7 天的记录
        query = select(SpiderTask.status, SpiderTask.created_at).where(
            SpiderTask.created_at >= last_7_days_start
        )
        result = await session.execute(query)
        tasks = result.all()
        
        for task_status, created_at in tasks:
            if not created_at:
                continue
            task_date = created_at.date()
            if task_date in days_map:
                if task_status == "success":
                    days_map[task_date]["success"] += 1
                elif task_status in ["failed", "timeout", "error"]:
                    days_map[task_date]["failure"] += 1
                    
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
        # 查询最近的 5 个任务
        query = select(SpiderTask).order_by(SpiderTask.created_at.desc()).limit(5)
        result = await session.execute(query)
        recent_tasks = result.scalars().all()
        
        result_list = []
        for t in recent_tasks:
            start_str = t.started_at.strftime("%Y-%m-%d %H:%M:%S") if t.started_at else t.created_at.strftime("%Y-%m-%d %H:%M:%S")
            end_str = t.finished_at.strftime("%Y-%m-%d %H:%M:%S") if t.finished_at else None
            
            node_id = t.node_id or "public"
            node_name = f"Node-{node_id[:6]}" if node_id != "public" else "Public Queue"
            
            result_list.append(RecentTask(
                id=t.task_id,
                spiderId=t.spider_id,
                spiderName=t.spider_name,
                nodeName=node_name,
                status=t.status,
                startTime=start_str,
                endTime=end_str
            ))
            
        return result_list
