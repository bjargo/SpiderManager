"""
项目管理路由 (CRUD) — 全异步
"""
import uuid
import logging
from typing import List
from datetime import datetime

from app.core.timezone import now

from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import audit_log

from app.api.projects.models import Project
from app.api.projects.schemas import ProjectCreate, ProjectUpdate, ProjectOut
from app.api.spiders.models import Spider
from app.api.users.models import User
from app.core.schemas.api_response import ApiResponse
from app.db.database import get_async_session
from app.core.dependencies import require_developer, require_viewer, verify_resource_owner

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=ApiResponse[List[ProjectOut]], summary="获取所有项目")
async def list_projects(
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    stmt = select(Project).where(Project.is_deleted == False).order_by(Project.id.desc())

    result_projects = await session.execute(stmt)
    projects = result_projects.scalars().all()

    result: List[ProjectOut] = []
    for p in projects:
        count_stmt = select(func.count()).where(Spider.project_id == p.project_id, Spider.is_deleted == False)

        count_result = await session.execute(count_stmt)
        spider_count = count_result.scalar_one()
        result.append(ProjectOut(
            project_id=p.project_id,
            name=p.name,
            description=p.description,
            created_at=p.created_at.isoformat(),
            updated_at=p.updated_at.isoformat(),
            spider_count=spider_count,
            owner_id=p.owner_id,
            is_deleted=p.is_deleted,
        ))
    return ApiResponse.success(data=result)


@router.post("", response_model=ApiResponse[ProjectOut], summary="创建项目")
@audit_log(action="CREATE", resource_type="project")
async def create_project(
    body: ProjectCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
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

    out = ProjectOut(
        project_id=db_project.project_id, name=db_project.name,
        description=db_project.description,
        created_at=db_project.created_at.isoformat(),
        updated_at=db_project.updated_at.isoformat(),
        spider_count=0,
        owner_id=db_project.owner_id,
        is_deleted=db_project.is_deleted,
    )
    logger.info(f"Project '{body.name}' created as {project_id}.")
    return ApiResponse.success(data=out)


@router.post("/{project_id}/update", response_model=ApiResponse[ProjectOut], summary="修改项目")
@audit_log(action="UPDATE", resource_type="project")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
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

    count_result = await session.execute(
        select(func.count()).where(Spider.project_id == project_id, Spider.is_deleted == False)
    )
    spider_count = count_result.scalar_one()

    out = ProjectOut(
        project_id=db_project.project_id, name=db_project.name,
        description=db_project.description,
        created_at=db_project.created_at.isoformat(),
        updated_at=db_project.updated_at.isoformat(),
        spider_count=spider_count,
        owner_id=db_project.owner_id,
        is_deleted=db_project.is_deleted,
    )
    logger.info(f"Project {project_id} updated.")
    return ApiResponse.success(data=out)


@router.post("/{project_id}/delete", response_model=ApiResponse, summary="删除项目")
@audit_log(action="DELETE", resource_type="project")
async def delete_project(
    project_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    result = await session.execute(
        select(Project).where(Project.project_id == project_id, Project.is_deleted == False)
    )
    db_project = result.scalars().first()
    if not db_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    verify_resource_owner(db_project.owner_id, operator, resource_name="项目")

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
    return ApiResponse.success(message=f"项目 {db_project.name} 及其 {len(spiders)} 个爬虫已删除")
