from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Optional, List
from datetime import datetime
import json
import uuid

from app.core.run_config import resolve_stored_run_config


def _json_list(value: Any) -> Any:
    """
    把数据库中 JSON 文本形式的列表列转换为 Python 列表。

    ``target_nodes`` 与 ``args`` 在库中以 JSON 文本存储，直接交给 Pydantic 校验
    会因为不是 list 而报错，这里统一做一次容错解析。

    :param value: 原始列值（JSON 文本 / 列表 / None）
    :return: 解析后的列表；``None`` 与非列表文本保持/回落为 ``None``
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, list) else None


def _as_mapping(data: Any) -> dict[str, Any]:
    """把 ORM 实例或字典统一转换为普通字段映射，便于 before 校验器处理。"""
    if isinstance(data, dict):
        return dict(data)
    attributes = getattr(data, "__dict__", None)
    if not isinstance(attributes, dict):
        raise TypeError("无法从对象中读取爬虫字段")
    return {key: value for key, value in attributes.items() if not key.startswith("_")}


def _apply_run_config(payload: dict[str, Any]) -> dict[str, Any]:
    """把存储中的运行配置转换为对外的结构化字段。"""
    resolution = resolve_stored_run_config(
        payload.get("entrypoint"),
        payload.get("args"),
    )
    payload["entrypoint"] = resolution.entrypoint
    payload["args"] = resolution.args
    payload["run_config_status"] = resolution.status
    return payload


class SpiderBase(BaseModel):
    name: str = Field(..., description="爬虫名称")
    description: Optional[str] = Field(None, description="爬虫描述")

    # 关联项目的配置 (使用刚才建立的 Project 元数据中的信息)
    project_id: str = Field(..., description="关联的项目ID")
    source_type: str = Field(..., description="代码源类型: MINIO 或 GIT")
    source_url: str = Field(..., description="MinIO Key 或 Git URL")

    language: Optional[str] = Field("python:3.11-slim", description="Docker 基础镜像名称")
    entrypoint: Optional[str] = Field(
        None, description="入口文件相对路径，如 'main.py'（不包含解释器名称）"
    )
    args: List[str] = Field(
        default_factory=list, description="运行参数数组，每一项对应一个独立命令行参数"
    )
    # 在前端/请求中，我们用 List[str] 接收 target_nodes
    target_nodes: Optional[List[str]] = Field(None, description="指定指定的节点IDs")
    owner_id: Optional[uuid.UUID] = Field(None, description="归属用户ID")

    @field_validator("args", "target_nodes", mode="before")
    @classmethod
    def _parse_json_list(cls, value: Any) -> Any:
        return _json_list(value)


class SpiderCreate(SpiderBase):
    pass


class SpiderUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[str] = None
    language: Optional[str] = None
    entrypoint: Optional[str] = Field(
        None, description="入口文件相对路径，如 'main.py'（不包含解释器名称）"
    )
    args: Optional[List[str]] = Field(None, description="运行参数数组")
    target_nodes: Optional[List[str]] = None

    @field_validator("args", "target_nodes", mode="before")
    @classmethod
    def _parse_json_list(cls, value: Any) -> Any:
        return _json_list(value)


class SpiderFileSave(BaseModel):
    path: str = Field(..., description="ZIP 包内的文件路径")
    content: str = Field(..., description="文件内容")


class SpiderFileCreate(BaseModel):
    path: str = Field(..., description="新文件在 ZIP 包内的路径")
    content: str = Field("", description="文件初始内容，默认为空")


class SpiderFileDelete(BaseModel):
    path: str = Field(..., description="要删除的文件在 ZIP 包内的路径")


class SpiderRunRequest(BaseModel):
    target_nodes: Optional[List[str]] = Field(None, description="指定运行节点 ID 列表，为空则随机调度")
    timeout_seconds: Optional[int] = Field(None, description="任务超时秒数，默认 3600")


class SpiderOut(SpiderBase):
    id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool = False
    run_config_status: str = Field(
        "empty", description="运行配置状态：ok / empty / unparsed（unparsed 需人工重新保存）"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_run_config(cls, data: Any) -> Any:
        return _apply_run_config(_as_mapping(data))

    class Config:
        from_attributes = True


class SpiderTaskOut(BaseModel):
    id: int
    task_id: str
    spider_id: int
    spider_name: str
    status: str
    node_id: Optional[str] = None
    entrypoint: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    run_config_status: str = "empty"
    error_detail: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    is_deleted: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_run_config(cls, data: Any) -> Any:
        return _apply_run_config(_as_mapping(data))

    @field_validator("args", mode="before")
    @classmethod
    def _parse_json_list(cls, value: Any) -> Any:
        return _json_list(value)

    class Config:
        from_attributes = True


class TaskLogOut(BaseModel):
    id: int
    task_id: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True
