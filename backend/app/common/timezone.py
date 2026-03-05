"""
全局时区工具模块

所有业务代码获取当前时间时，统一调用 now()，
确保无论节点部署在哪个时区，所有时间戳都与 Master 节点配置的时区对齐。

时区由 config.py 中的 TIMEZONE 字段控制，
可通过 docker-compose.yml 环境变量统一管理所有节点。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings

APP_TZ = ZoneInfo(settings.TIMEZONE)


def now() -> datetime:
    """
    返回应用配置时区的当前时间（naive datetime，不含 tzinfo）。

    返回 naive datetime 是因为现有数据库字段和前端都不处理时区信息，
    剥离 tzinfo 保持与现有数据格式兼容。
    """
    return datetime.now(APP_TZ).replace(tzinfo=None)
