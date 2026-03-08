import json
from functools import wraps
from typing import Any, Callable, Coroutine
from fastapi import BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Callable, Coroutine
from fastapi import BackgroundTasks

from app.core.audit.models import AuditLog
from app.api.users.models import User
from app.db.database import async_session_maker
from app.core.context import current_user_ctx, current_request_ctx

async def _save_audit_log_async(
    operator: User,
    action: str,
    resource_type: str,
    resource_id: str,
    status_code: int = 200,
    original_value: str | None = None,
    new_value: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
):
    """
    异步保存审计日志到底层数据库，供 BackgroundTasks 使用。
    """
    role_str = "viewer"
    if operator.role:
        role_str = operator.role.value if hasattr(operator.role, "value") else str(operator.role)

    res_id_str = str(resource_id)

    log_entry = AuditLog(
        operator_id=operator.id,
        role=role_str,
        action=action.upper(),
        resource_type=resource_type.lower(),
        resource_id=res_id_str,
        original_value=original_value,
        new_value=new_value,
        ip_address=ip_address,
        user_agent=user_agent,
        status_code=status_code,
    )

    async with async_session_maker() as session:
        session.add(log_entry)
        await session.commit()

async def record_audit_log(
    session: AsyncSession,
    operator: User,
    action: str,
    resource_type: str,
    resource_id: str,
    status_code: int = 200,
    original_value: str | None = None,
    new_value: str | None = None,
    request: Request | None = None,
):
    """
    记录审计日志。供遗留接口使用的主动调用方法。
    优化点：从上下文中获取请求的 IP 地址和 User-Agent。
    日志将加入当前 session 的事务中，随外层 commit 一起生效。
    """
    cmd_req = request or current_request_ctx.get()

    ip_address = None
    user_agent = None
    if cmd_req:
        user_agent = cmd_req.headers.get("user-agent")
        if cmd_req.client:
            ip_address = cmd_req.client.host
        elif "x-forwarded-for" in cmd_req.headers:
            ip_address = cmd_req.headers["x-forwarded-for"].split(",")[0].strip()

    role_str = "viewer"
    if operator.role:
        role_str = operator.role.value if hasattr(operator.role, "value") else str(operator.role)

    res_id_str = str(resource_id)

    log_entry = AuditLog(
        operator_id=operator.id,
        role=role_str,
        action=action.upper(),
        resource_type=resource_type.lower(),
        resource_id=res_id_str,
        original_value=original_value,
        new_value=new_value,
        ip_address=ip_address,
        user_agent=user_agent,
        status_code=status_code,
    )
    session.add(log_entry)



def audit_log(action: str, resource_type: str) -> Callable:
    """
    声明式审计日志装饰器。
    
    能够自动从:
      1. ContextVars 获取当前 User 和 Request (提取 IP 等)
      2. 拦截路由函数的返回值，如果是 ApiResponse，取 data.id 或原样记录。
    
    使用要求：被装饰的路由函数**必须**显式声明 `background_tasks: BackgroundTasks` 参数，
    以便使用 FastAPI 原生机制实现无阻塞日志记录。如果未声明，将同步堵塞执行（仍可工作）。
    """
    def decorator(func: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 1. 提取当前上下文
            user = current_user_ctx.get()
            request = current_request_ctx.get()

            ip_address = None
            user_agent = None
            if request:
                user_agent = request.headers.get("user-agent")
                if request.client:
                    ip_address = request.client.host
                elif "x-forwarded-for" in request.headers:
                    ip_address = request.headers["x-forwarded-for"].split(",")[0].strip()

            # 2. 执行核心业务，并捕获返回值 / 异常
            status_code = 200
            new_value = None
            resource_id_val = "0"
            background_tasks: BackgroundTasks | None = kwargs.get("background_tasks")

            try:
                response = await func(*args, **kwargs)
                
                # 尝试从 ApiResponse 解析 resource_id 等快照
                if hasattr(response, "data") and response.data is not None:
                    data = response.data
                    if isinstance(data, dict):
                        res_id = data.get("id") or data.get("task_id") or data.get("job_id") or data.get(f"{resource_type}_id")
                    else:
                        res_id = getattr(data, "id", getattr(data, "task_id", getattr(data, "job_id", None)))
                    
                    if res_id is not None:
                        resource_id_val = str(res_id)
                        
                    # 序列化 data 用于 new_value 保留原始变更痕迹
                    if hasattr(data, "model_dump"):
                        new_value = json.dumps(data.model_dump(exclude_unset=True), default=str, ensure_ascii=False)
                    elif hasattr(data, "dict"):
                        new_value = json.dumps(data.dict(exclude_unset=True), default=str, ensure_ascii=False)
                    else:
                        new_value = json.dumps(data, default=str, ensure_ascii=False)
                        
                if resource_id_val == "0" or resource_id_val == "None":
                    for key in [f"{resource_type}_id", "id", "task_id", "job_id"]:
                        if key in kwargs and kwargs[key] is not None:
                            resource_id_val = str(kwargs[key])
                            break
                    
                return response
            except Exception as e:
                status_code = getattr(e, "status_code", 500)
                raise
            finally:
                if user:
                    # 避免拦截中报错时截断
                    if background_tasks:
                        background_tasks.add_task(
                            _save_audit_log_async,
                            operator=user,
                            action=action,
                            resource_type=resource_type,
                            resource_id=resource_id_val,
                            status_code=status_code,
                            new_value=new_value,
                            ip_address=ip_address,
                            user_agent=user_agent,
                        )
                    else:
                        # 降级：如果调用方忘了注入 background_tasks，手动跑一下
                        await _save_audit_log_async(
                            operator=user,
                            action=action,
                            resource_type=resource_type,
                            resource_id=resource_id_val,
                            status_code=status_code,
                            new_value=new_value,
                            ip_address=ip_address,
                            user_agent=user_agent,
                        )

        return wrapper
    return decorator
