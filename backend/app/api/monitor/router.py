from fastapi import APIRouter
from . import schemas
from .services import MonitorService

router = APIRouter()

@router.get("/system", response_model=schemas.SystemPerformanceOut, summary="获取系统性能数据")
def get_system_performance():
    """
    获取服务器的 CPU、内存和磁盘状态信息。
    """
    return MonitorService.get_system_performance()
