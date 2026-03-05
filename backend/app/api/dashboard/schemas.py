from typing import Optional, List
from pydantic import BaseModel

class DashboardStats(BaseModel):
    onlineNodes: int
    totalNodes: int
    totalSpiders: int
    tasksToday: int
    failedTasksToday: int

class TrendData(BaseModel):
    date: str
    success: int
    failure: int

class RecentTask(BaseModel):
    id: str
    spiderId: Optional[int] = None
    spiderName: str
    nodeName: str
    status: str
    startTime: str
    endTime: Optional[str] = None
