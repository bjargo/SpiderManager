"""
公共枚举定义模块
集中管理所有业务枚举，避免分散定义导致的一致性问题。
"""
import enum


class UserRole(str, enum.Enum):
    """
    用户角色枚举。
    - admin: 系统管理员，拥有所有权限
    - developer: 开发者，可操作自己拥有的资源
    - viewer: 只读用户，只能查看数据
    """
    admin = "admin"
    developer = "developer"
    viewer = "viewer"
