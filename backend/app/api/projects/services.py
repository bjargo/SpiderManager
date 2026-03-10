"""
项目管理业务逻辑层

将 router.py 中的项目 CRUD 逻辑全部迁移至此，
router 仅负责接口响应和参数解析。
"""
import uuid
import logging

from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects.models import Project
from app.api.projects.schemas import ProjectCreate, ProjectUpdate, ProjectOut
from app.api.spiders.models import Spider
from app.api.users.models import User
from app.core.dependencies import verify_resource_owner
from app.core.timezone import now

logger = logging.getLogger(__name__)


async def _count_project_spiders(project_id: str, session: AsyncSession) -> int:
    """
    查询项目下未删除的爬虫数量。

    :param project_id: 项目 ID（proj-xxx 格式）
    :param session: 异步数据库会话
    :return: 爬虫数量
    """
    count_result = await session.execute(
        select(func.count()).where(Spider.project_id == project_id, Spider.is_deleted == False)
    )
    return count_result.scalar_one()


def _to_project_out(project: Project, spider_count: int) -> ProjectOut:
    """
    将 Project ORM 对象转换为 ProjectOut schema。

    :param project: Project ORM 对象
    :param spider_count: 关联的爬虫数量
    :return: ProjectOut schema 实例
    """
    return ProjectOut(
        project_id=project.project_id,
        name=project.name,
        description=project.description,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
        spider_count=spider_count,
        owner_id=project.owner_id,
        is_deleted=project.is_deleted,
    )


async def list_all(session: AsyncSession) -> list[ProjectOut]:
    """
    获取所有未删除的项目列表（含关联爬虫数量）。

    :param session: 异步数据库会话
    :return: ProjectOut 列表
    """
    stmt = select(Project).where(Project.is_deleted == False).order_by(Project.id.desc())
    result_projects = await session.execute(stmt)
    projects = result_projects.scalars().all()

    result: list[ProjectOut] = []
    for p in projects:
        spider_count = await _count_project_spiders(p.project_id, session)
        result.append(_to_project_out(p, spider_count))
    return result


async def create_project(
    body: ProjectCreate,
    operator: User,
    session: AsyncSession,
) -> ProjectOut:
    """
    创建一个新的项目记录。

    :param body: 项目创建请求体（name、description）
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: ProjectOut 创建结果
    """
    project_id = f"proj-{uuid.uuid4().hex[:8]}"
    db_project = Project(
        project_id=project_id,
        name=body.name,
        description=body.description,
        owner_id=operator.id,
    )
    session.add(db_project)
    await session.commit()
    await session.refresh(db_project)

    logger.info(f"Project '{body.name}' created as {project_id}.")
    return _to_project_out(db_project, spider_count=0)


async def update_project(
    project_id: str,
    body: ProjectUpdate,
    operator: User,
    session: AsyncSession,
) -> ProjectOut:
    """
    更新指定项目的名称或描述。

    :param project_id: 项目 ID（proj-xxx 格式）
    :param body: 项目更新请求体（name、description 均可选）
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: ProjectOut 更新后的项目信息
    :raises HTTPException: 404 — 项目不存在；403 — 无权操作
    """
    result = await session.execute(
        select(Project).where(Project.project_id == project_id, Project.is_deleted == False)
    )
    db_project = result.scalars().first()
    if not db_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    verify_resource_owner(db_project.owner_id, operator, resource_name="项目")

    if body.name is not None:
        db_project.name = body.name
    if body.description is not None:
        db_project.description = body.description
    db_project.updated_at = now()

    session.add(db_project)
    await session.commit()
    await session.refresh(db_project)

    spider_count = await _count_project_spiders(project_id, session)
    logger.info(f"Project {project_id} updated.")
    return _to_project_out(db_project, spider_count)


async def delete_project(
    project_id: str,
    operator: User,
    session: AsyncSession,
) -> str:
    """
    软删除项目及其关联的所有爬虫。

    :param project_id: 项目 ID（proj-xxx 格式）
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: 删除成功的消息字符串
    :raises HTTPException: 404 — 项目不存在；403 — 无权操作
    """
    result = await session.execute(
        select(Project).where(Project.project_id == project_id, Project.is_deleted == False)
    )
    db_project = result.scalars().first()
    if not db_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    verify_resource_owner(db_project.owner_id, operator, resource_name="项目")

    # 级联软删除关联爬虫
    spiders_result = await session.execute(
        select(Spider).where(Spider.project_id == project_id, Spider.is_deleted == False)
    )
    spiders = spiders_result.scalars().all()
    for spider in spiders:
        spider.is_deleted = True
        session.add(spider)

    db_project.is_deleted = True
    session.add(db_project)
    await session.commit()

    logger.info(f"Project {project_id} and {len(spiders)} associated spiders deleted.")
    return f"项目 {db_project.name} 及其 {len(spiders)} 个爬虫已删除"
