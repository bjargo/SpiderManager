import psutil
from typing import List
from . import schemas

class MonitorService:
    """
    系统性能监控服务层
    提供获取系统各项指标的纯业务逻辑函数
    """

    @staticmethod
    def get_cpu_info() -> schemas.CPUInfo:
        """
        获取当前 CPU 信息
        """
        cpu_freq = psutil.cpu_freq()
        return schemas.CPUInfo(
            percent=psutil.cpu_percent(interval=0.1),
            core_count_physical=psutil.cpu_count(logical=False) or 0,
            core_count_logical=psutil.cpu_count(logical=True) or 0,
            frequency_current=cpu_freq.current if cpu_freq else 0.0,
            frequency_min=cpu_freq.min if cpu_freq else 0.0,
            frequency_max=cpu_freq.max if cpu_freq else 0.0
        )

    @staticmethod
    def get_memory_info() -> schemas.MemoryInfo:
        """
        获取当前内存信息
        """
        mem = psutil.virtual_memory()
        return schemas.MemoryInfo(
            total=mem.total,
            available=mem.available,
            percent=mem.percent,
            used=mem.used,
            free=mem.free
        )

    @staticmethod
    def get_disks_info() -> List[schemas.DiskInfo]:
        """
        获取所有磁盘分区信息
        """
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append(schemas.DiskInfo(
                    device=part.device,
                    mountpoint=part.mountpoint,
                    fstype=part.fstype,
                    total=usage.total,
                    used=usage.used,
                    free=usage.free,
                    percent=usage.percent
                ))
            except PermissionError:
                # 忽略没有权限访问的磁盘分区
                continue
        return disks

    @staticmethod
    def get_system_performance() -> schemas.SystemPerformanceOut:
        """
        获取综合的系统性能数据
        """
        return schemas.SystemPerformanceOut(
            cpu=MonitorService.get_cpu_info(),
            memory=MonitorService.get_memory_info(),
            disks=MonitorService.get_disks_info(),
            boot_time=psutil.boot_time()
        )
