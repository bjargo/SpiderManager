import json
from typing import List, Optional
from datetime import datetime
from sqlmodel import Session, select
from fastapi import HTTPException, status

from app.api.spiders.models import Spider
from app.api.spiders.schemas import SpiderCreate, SpiderUpdate
from app.common.timezone import now

class SpiderService:
    @staticmethod
    def create_spider(db: Session, spider_in: SpiderCreate) -> Spider:
        # 序列化 target_nodes 列表为 JSON 字符串进行保存
        target_nodes_str = json.dumps(spider_in.target_nodes) if spider_in.target_nodes else None
        
        db_spider = Spider(
            name=spider_in.name,
            description=spider_in.description,
            project_id=spider_in.project_id,
            source_type=spider_in.source_type,
            source_url=spider_in.source_url,
            command=spider_in.command,
            target_nodes=target_nodes_str
        )
        try:
            db.add(db_spider)
            db.commit()
            db.refresh(db_spider)
            return db_spider
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not create spider, it might already exist: {str(e)}"
            )

    @staticmethod
    def get_spider(db: Session, spider_id: int) -> Spider:
        spider = db.get(Spider, spider_id)
        if not spider:
            raise HTTPException(status_code=404, detail="Spider not found")
        return spider

    @staticmethod
    def get_spiders(db: Session, skip: int = 0, limit: int = 100) -> List[Spider]:
        statement = select(Spider).offset(skip).limit(limit)
        results = db.exec(statement).all()
        return results

    @staticmethod
    def update_spider(db: Session, spider_id: int, spider_in: SpiderUpdate) -> Spider:
        db_spider = SpiderService.get_spider(db, spider_id)
        
        update_data = spider_in.dict(exclude_unset=True)
        if "target_nodes" in update_data:
            update_data["target_nodes"] = json.dumps(update_data["target_nodes"]) if update_data["target_nodes"] else None
            
        for key, value in update_data.items():
            setattr(db_spider, key, value)
            
        db_spider.updated_at = now()
        
        try:
            db.add(db_spider)
            db.commit()
            db.refresh(db_spider)
            return db_spider
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not update spider: {str(e)}"
            )

    @staticmethod
    def delete_spider(db: Session, spider_id: int) -> None:
        db_spider = SpiderService.get_spider(db, spider_id)
        try:
            db.delete(db_spider)
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not delete spider: {str(e)}"
            )