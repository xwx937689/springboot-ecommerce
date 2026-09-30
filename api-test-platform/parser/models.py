
"""Phase 4 - 统一接口数据模型（与具体 OpenAPI 版本解耦）。

后面 Phase 5/6/7 全部基于这些模型工作，不依赖原始 JSON 结构。
"""
from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ParameterLocation(str, Enum):
    QUERY = "query"
    PATH = "path"
    HEADER = "header"
    COOKIE = "cookie"


class AuthType(str, Enum):
    NONE = "none"          # 公开接口（网关白名单）
    JWT = "jwt"            # 需 Bearer token
    UNKNOWN = "unknown"


class Parameter(BaseModel):
    name: str
    location: ParameterLocation
    required: bool = False
    type: str = "string"
    description: str | None = None


class RequestBody(BaseModel):
    required: bool = False
    content_type: str = "application/json"
    schema_ref: str | None = None        # #/components/schemas/XXX
    example: dict | None = None


class ResponseSpec(BaseModel):
    status_code: int
    description: str | None = None
    schema_ref: str | None = None


class Endpoint(BaseModel):
    """一个可测试接口单元。"""
    service: str                         # 来源服务，如 user-service
    tag: str                             # OpenAPI tag，如 auth / products
    operation_id: str | None = None
    method: HttpMethod
    path: str                            # 原始路径，如 /api/auth/login
    summary: str | None = None
    auth: AuthType = AuthType.UNKNOWN
    parameters: list[Parameter] = Field(default_factory=list)
    request_body: RequestBody | None = None
    responses: list[ResponseSpec] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.method.value} {self.path}"


class ServiceContract(BaseModel):
    """一个服务的全部接口集合。"""
    service: str
    title: str
    version: str
    base_path: str = ""                  # 网关路由前缀，如 /api
    endpoints: list[Endpoint] = Field(default_factory=list)
    schemas: dict = Field(default_factory=dict)  # components.schemas，供造数据/给 LLM 上下文
