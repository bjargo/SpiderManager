from typing import TypeVar, Generic, Optional
from pydantic import BaseModel

T = TypeVar("T")

class ApiResponse(BaseModel, Generic[T]):
    """统一 API 返回格式"""
    code: int = 200
    data: Optional[T] = None
    message: str = "success"

    @classmethod
    def success(cls, data: T = None, message: str = "success"):
        return cls(code=200, data=data, message=message)

    @classmethod
    def error(cls, code: int = 500, message: str = "error", data: T = None):
        return cls(code=code, data=data, message=message)
