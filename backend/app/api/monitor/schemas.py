from pydantic import BaseModel
from typing import Dict, Any, List

class CPUInfo(BaseModel):
    """
    CPU 信息模型
    """
    percent: float
    core_count_physical: int
    core_count_logical: int
    frequency_current: float
    frequency_min: float
    frequency_max: float

class MemoryInfo(BaseModel):
    """
    内存信息模型
    """
    total: int
    available: int
    percent: float
    used: int
    free: int

class DiskInfo(BaseModel):
    """
    磁盘信息模型
    """
    device: str
    mountpoint: str
    fstype: str
    total: int
    used: int
    free: int
    percent: float

class SystemPerformanceOut(BaseModel):
    """
    系统性能输出模型
    """
    cpu: CPUInfo
    memory: MemoryInfo
    disks: List[DiskInfo]
    boot_time: float
