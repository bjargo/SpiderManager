from pydantic import BaseModel, Field

class NodeStatus(BaseModel):
    """节点状态返回模型 (Type Hinting & Pydantic DataClass)"""
    node_id: str = Field(..., description="节点唯一标识")
    name: str = Field(None, description="节点名称/别名")
    role: str = Field("worker", description="节点角色 (master/worker)")
    ip: str = Field("127.0.0.1", description="节点IP地址")
    cpu_usage: float = Field(..., description="CPU使用率")
    mem_usage: float = Field(..., description="内存使用率")
    disk_usage: float = Field(0.0, description="磁盘使用率")
    memory_total_mb: int = Field(..., description="内存总大小(MB)")
    memory_used_mb: int = Field(..., description="已使用内存大小(MB)")
    last_heartbeat: str = Field(..., description="最后心跳时间 (ISO格式)")
    status: str = Field(..., description="节点状态 (online/offline)")

    # 扩展配置字段 (Hash 存储)
    mac_address: str = Field("", description="物理MAC地址")
    enabled: bool = Field(True, description="是否启用 (禁用后不再接收任务)")
    max_runners: int = Field(1, ge=1, le=80, description="当前节点最大并发执行任务数")

class NodeConfigUpdate(BaseModel):
    """节点完整配置更新请求"""
    name: str = Field(..., max_length=50)
    mac_address: str = Field(..., max_length=50)
    enabled: bool = Field(...)
    max_runners: int = Field(..., ge=1, le=80)
