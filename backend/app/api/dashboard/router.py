from typing import List
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.db.database import get_async_session
from app.core.schemas.api_response import ApiResponse
from .schemas import DashboardStats, TrendData, RecentTask
from .services import DashboardService

router = APIRouter()

@router.get("/stats", response_model=ApiResponse[DashboardStats], summary="获取大盘核心统计指标")
async def get_dashboard_stats(redis: Redis = Depends(get_redis), session: AsyncSession = Depends(get_async_session)):
    """获取在线节点、总爬虫数、今日运行任务、今日失败任务"""
    stats = await DashboardService.get_stats(redis, session)
    return ApiResponse.success(data=stats)

@router.get("/trends", response_model=ApiResponse[List[TrendData]], summary="获取过去7天运行趋势")
async def get_dashboard_trends(redis: Redis = Depends(get_redis), session: AsyncSession = Depends(get_async_session)):
    """获取成功/失败任务按天分布"""
    trends = await DashboardService.get_trends(redis, session)
    return ApiResponse.success(data=trends)

@router.get("/recent", response_model=ApiResponse[List[RecentTask]], summary="获取最近运行的任务")
async def get_dashboard_recent(redis: Redis = Depends(get_redis), session: AsyncSession = Depends(get_async_session)):
    """获取最新 5 个任务及其状态"""
    recent = await DashboardService.get_recent_tasks(redis, session)
    return ApiResponse.success(data=recent)
