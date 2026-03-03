"""
用户数据库模型
继承 fastapi-users 提供的 SQLModelBaseUserDB，自动包含以下字段：
- id (UUID), email, hashed_password, is_active, is_superuser, is_verified
"""
from fastapi_users_db_sqlmodel import SQLModelBaseUserDB


class User(SQLModelBaseUserDB, table=True):
    __tablename__ = "users"
