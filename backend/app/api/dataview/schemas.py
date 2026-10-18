"""
数据查看模块 —— 请求/响应模型

所有查询接口均为只读浏览，响应中统一携带 `guard` 字段，
向前端与使用者明示当前生效的查询限制（行数上限、语句超时、并发数等）。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QueryGuardInfo(BaseModel):
    """当前生效的查询防护参数（只读展示，便于使用者理解限制来源）。"""
    max_limit: int = Field(..., description="单次请求返回的最大行数")
    default_limit: int = Field(..., description="未指定 limit 时的默认行数")
    statement_timeout_ms: int = Field(..., description="单条 SQL 的数据库级超时（毫秒），超时由 PostgreSQL 熔断")
    max_concurrent_queries: int = Field(..., description="允许同时进行的浏览查询数量")
    metadata_cache_ttl_seconds: float = Field(..., description="表清单/统计信息缓存时长（秒）")
    pagination: str = Field(..., description="分页方式说明")
    note: str = Field(..., description="使用提示")


class DataTableSummary(BaseModel):
    """数据表采集概况（数据量取自数据库统计信息，非实时精确值）。"""
    table_name: str = Field(..., description="数据表名（爬虫写入的 table_name）")
    estimated_rows: int = Field(0, description="记录数估算值（来自 PostgreSQL 统计信息）")
    table_bytes: int = Field(0, description="表占用的磁盘空间（字节，含索引）")
    spider_id: Optional[int] = Field(None, description="关联爬虫 ID（按同名爬虫匹配）")
    description: Optional[str] = Field(None, description="爬虫描述")
    task_count: int = Field(0, description="产生过该表数据的任务数")
    success_count: int = Field(0, description="其中成功结束的任务数")
    last_collected_at: Optional[datetime] = Field(None, description="最近一次采集时间")
    last_task_id: Optional[str] = Field(None, description="最近一次采集对应的任务 ID")


class DataTableListResponse(BaseModel):
    """数据表清单响应。"""
    items: List[DataTableSummary] = Field(default_factory=list)
    total_tables: int = 0
    guard: QueryGuardInfo
    generated_at: datetime


class RecentTaskItem(BaseModel):
    """某数据表最近的采集任务。"""
    task_id: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class DataTableOverviewResponse(BaseModel):
    """单表采集概况。"""
    table_name: str
    estimated_rows: int = 0
    table_bytes: int = 0
    task_count: int = 0
    success_count: int = 0
    last_collected_at: Optional[datetime] = None
    sample_fields: List[str] = Field(default_factory=list, description="采样得到的字段名预览")
    sampled_rows: int = Field(0, description="字段预览的采样行数")
    has_task_index: bool = Field(False, description="是否存在 _task_id 索引（缺失时按任务过滤会变慢）")
    recent_tasks: List[RecentTaskItem] = Field(default_factory=list)
    guard: QueryGuardInfo


class DataRowItem(BaseModel):
    """单条采集数据。"""
    id: int = Field(..., description="自增主键 _id")
    task_id: str = Field(..., description="写入该记录的任务 ID")
    data: Dict[str, Any] = Field(default_factory=dict, description="采集到的业务数据（JSON）")
    created_at: Optional[datetime] = Field(None, description="入库时间")


class DataRowsResponse(BaseModel):
    """数据行分页响应（游标分页，禁止深翻页）。"""
    table_name: str
    items: List[DataRowItem] = Field(default_factory=list)
    limit: int = 0
    next_cursor: Optional[int] = Field(None, description="下一页游标（当前页最后一行的 _id）")
    has_more: bool = False
    estimated_total: int = Field(0, description="全表记录数估算值")
    exact_total: Optional[int] = Field(None, description="按任务过滤时的精确条数（仅在索引过滤时提供）")
    order: str = Field("desc", description="排序方向：desc=最新优先 / asc=最早优先")
    query_ms: float = Field(0.0, description="本次查询的数据库耗时（毫秒）")
    guard: QueryGuardInfo
