"""
结构化运行配置（入口文件 + 参数数组）。

运行配置由「入口文件」和「参数数组」两部分组成，在 API 请求体、响应体和数据库中
都以独立字段存在，因此线上报文中不会再出现 ``python main.py --mode all`` 这类
整条命令字符串，避免被机房侧安全设备误判为恶意命令而重置连接。

执行侧统一使用 argv 数组 + ``shell=False``，参数不具备 Shell 语义。
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# ── 与业务相符的上限 ────────────────────────────────────────────────
ENTRYPOINT_MAX_LEN = 255
ARGS_MAX_COUNT = 64
ARG_MAX_LEN = 512

# 运行时别名，必须与 runtimes/*/manifest.json 的 aliases 保持一致。
_ALIASES_PYTHON = frozenset({"python", "py"})
_ALIASES_NODE = frozenset({"nodejs", "node.js", "node"})
_ALIASES_GO = frozenset({"golang", "go"})

# 控制字符：包含换行、回车、制表符等，一律拒绝。
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

# Shell 语法特征：这些片段会让安全设备把参数误判为命令注入。
_FORBIDDEN_SEQUENCES = (";", "|", "&&", "||", ">", "<", "`", "$(", "${")

_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class RunConfigError(ValueError):
    """运行配置不合法。消息面向用户，可直接用于 HTTP 400 响应。"""


@dataclass(frozen=True)
class RunConfigResolution:
    """已存储运行配置的只读解析结果。"""

    entrypoint: str | None = None
    args: list[str] = field(default_factory=list)
    status: str = "empty"  # ok / empty / unparsed
    detail: str | None = None

    @property
    def is_runnable(self) -> bool:
        return self.status == "ok" and bool(self.entrypoint)


# ── 基础校验 ────────────────────────────────────────────────────────


def _check_forbidden(
    value: str,
    *,
    field_name: str,
    forbidden: Sequence[str] = _FORBIDDEN_SEQUENCES,
) -> None:
    if _CONTROL_CHAR_RE.search(value):
        raise RunConfigError(f"{field_name}不允许包含换行、制表符等控制字符")
    for token in forbidden:
        if token in value:
            raise RunConfigError(f"{field_name}不允许包含 Shell 语法片段 {token!r}")


def detect_family(language: str | None) -> str:
    """
    根据语言标识返回运行时族：python / node / go / shell。

    判定方式与 RunnerFactory.get_runner 的别名解析保持一致（按 ':' 取前缀后精确匹配），
    未命中任何已注册运行时则回落到 shell（对应 default 运行时）。
    """
    lang_key = (language or "").strip().lower().split(":")[0]
    if lang_key in _ALIASES_PYTHON:
        return "python"
    if lang_key in _ALIASES_NODE:
        return "node"
    if lang_key in _ALIASES_GO:
        return "go"
    return "shell"


def validate_entrypoint(entrypoint: str) -> str:
    """
    校验入口文件：必须是代码包内的相对文件路径，且不含 Shell 语法。

    :param entrypoint: 入口文件相对路径，例如 ``main.py``
    :return: 规范化后的入口文件路径
    :raises RunConfigError: 路径为空、超长、绝对路径、含 ``..``、含空白或 Shell 语法
    """
    if not isinstance(entrypoint, str):
        raise RunConfigError("入口文件必须是字符串")

    value = entrypoint.strip()
    if not value:
        raise RunConfigError("入口文件不能为空")
    if len(value) > ENTRYPOINT_MAX_LEN:
        raise RunConfigError(f"入口文件长度不能超过 {ENTRYPOINT_MAX_LEN} 个字符")
    if any(char.isspace() for char in value):
        raise RunConfigError("入口文件是单个文件路径，不能包含空格；命令参数请填入参数列表")

    _check_forbidden(value, field_name="入口文件")

    if value.startswith(("-", "~", "/")) or _WINDOWS_ABSOLUTE_RE.match(value):
        raise RunConfigError("入口文件必须是代码包内的相对路径，不能以 '-'、'~' 或 '/' 开头")

    normalized = posixpath.normpath(value.replace("\\", "/"))
    parts = normalized.split("/")
    if ".." in parts:
        raise RunConfigError("入口文件不允许包含 '..' 路径穿越")
    if not parts[-1] or "." not in parts[-1]:
        raise RunConfigError("入口文件必须是具体的文件路径，例如 'main.py'")
    return normalized


def validate_args(args: Iterable[Any] | None) -> list[str]:
    """
    校验参数数组，逐个元素独立校验并保留元素内部空格。

    :param args: 参数序列，每一项对应一个独立的命令行参数
    :return: 校验后的参数列表副本
    :raises RunConfigError: 类型错误、空字符串、超长或包含 Shell 语法
    """
    if args is None:
        return []
    if isinstance(args, (str, bytes)):
        raise RunConfigError("参数必须是字符串数组，每一项对应一个独立参数")

    items = list(args)
    if len(items) > ARGS_MAX_COUNT:
        raise RunConfigError(f"参数数量不能超过 {ARGS_MAX_COUNT} 个")

    validated: list[str] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, str):
            raise RunConfigError(f"第 {index} 个参数必须是字符串")
        if not item.strip():
            raise RunConfigError(f"第 {index} 个参数不能为空")
        if len(item) > ARG_MAX_LEN:
            raise RunConfigError(f"第 {index} 个参数长度不能超过 {ARG_MAX_LEN} 个字符")
        _check_forbidden(item, field_name=f"第 {index} 个参数")
        validated.append(item)
    return validated


# ── 序列化 ──────────────────────────────────────────────────────────


def deserialize_args(raw: Any) -> list[str]:
    """
    把数据库中的参数列（JSON 数组文本）解析为列表。

    :param raw: ``None``、JSON 数组文本或已是列表的值
    :return: 校验后的参数列表
    :raises RunConfigError: 文本不是合法的字符串数组
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple)):
        return validate_args(raw)
    if not isinstance(raw, str):
        raise RunConfigError("参数必须是字符串数组")

    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RunConfigError("参数不是合法的 JSON 数组") from exc
    if not isinstance(decoded, list):
        raise RunConfigError("参数必须是 JSON 数组")
    return validate_args(decoded)


def serialize_args(args: Iterable[Any] | None) -> str | None:
    """把参数列表序列化为数据库存储文本；空列表返回 ``None``。"""
    validated = validate_args(args)
    if not validated:
        return None
    return json.dumps(validated, ensure_ascii=False)


# ── 写入路径 ────────────────────────────────────────────────────────


def normalize_run_config_input(
    entrypoint: str | None,
    args: Iterable[Any] | None,
) -> tuple[str | None, list[str]]:
    """
    归一化创建/更新请求中的运行配置。

    :param entrypoint: 入口文件，可为 ``None``
    :param args: 参数列表，可为 ``None``
    :return: (入口文件或 None, 参数列表)
    :raises RunConfigError: 任一部分不合法
    """
    normalized_entrypoint = (
        validate_entrypoint(entrypoint) if entrypoint is not None else None
    )
    return normalized_entrypoint, validate_args(args)


def normalize_run_config_update(
    current_entrypoint: Any,
    current_args_raw: Any,
    provided: Mapping[str, Any],
) -> tuple[str | None, list[str]]:
    """
    计算更新请求之后应写入的运行配置。

    未提交的字段沿用记录当前值，避免部分提交把另一部分清空。

    :param current_entrypoint: 记录当前的 entrypoint 列
    :param current_args_raw: 记录当前的 args 列（JSON 文本）
    :param provided: 请求中显式提交的字段（``exclude_unset`` 结果）
    :return: (入口文件或 None, 参数列表)
    :raises RunConfigError: 任一部分不合法
    """
    if "entrypoint" not in provided and "args" not in provided:
        resolution = resolve_stored_run_config(current_entrypoint, current_args_raw)
        if resolution.status == "unparsed":
            raise RunConfigError("该爬虫已保存的运行配置无法解析，请重新填写入口文件与参数")
        return resolution.entrypoint, resolution.args

    entrypoint_input = (
        provided["entrypoint"] if "entrypoint" in provided else current_entrypoint
    )
    if "args" in provided:
        args_input: Any = provided["args"]
    else:
        try:
            args_input = deserialize_args(current_args_raw)
        except RunConfigError:
            args_input = None

    return normalize_run_config_input(entrypoint_input, args_input)


# ── 读取路径 ────────────────────────────────────────────────────────


def resolve_stored_run_config(
    entrypoint: Any,
    args_raw: Any,
) -> RunConfigResolution:
    """
    解析已存储的运行配置。

    :param entrypoint: spiders/spider_tasks 的 entrypoint 列
    :param args_raw: spiders/spider_tasks 的 args 列（JSON 文本）
    :return: RunConfigResolution
    """
    if not entrypoint and not args_raw:
        return RunConfigResolution(status="empty")

    try:
        resolved_entrypoint = validate_entrypoint(entrypoint) if entrypoint else None
        resolved_args = deserialize_args(args_raw)
    except RunConfigError as exc:
        return RunConfigResolution(status="unparsed", detail=str(exc))

    if resolved_entrypoint is None:
        return RunConfigResolution(status="empty")
    return RunConfigResolution(
        entrypoint=resolved_entrypoint, args=resolved_args, status="ok"
    )


# ── 执行路径 ────────────────────────────────────────────────────────


def build_exec_argv(
    language: str | None,
    entrypoint: str,
    args: Sequence[str] | None = None,
) -> list[str]:
    """
    构建容器内执行的 argv 数组。

    :param language: 爬虫语言标识（Docker 基础镜像名）
    :param entrypoint: 入口文件相对路径
    :param args: 参数列表
    :return: argv 数组，可直接用于 Dockerfile 的 exec 形式 CMD
    """
    validated_args = list(args or [])
    family = detect_family(language)
    if family == "python":
        return ["python", entrypoint, *validated_args]
    if family == "node":
        return ["node", entrypoint, *validated_args]
    if family == "go":
        # golang 运行时在构建阶段编译为 /app/spider_bin，入口文件仅参与编译。
        return ["/app/spider_bin", *validated_args]
    return ["/bin/sh", entrypoint, *validated_args]


def build_subprocess_argv(
    language: str | None,
    entrypoint: str,
    args: Sequence[str] | None = None,
) -> list[str]:
    """
    构建 Worker 本机子进程执行的 argv 数组。

    返回值必须以 ``subprocess.Popen(argv, shell=False)`` 的方式使用。

    :param language: 爬虫语言标识，可为 ``None``（对应旧子进程模式）
    :param entrypoint: 入口文件相对路径
    :param args: 参数列表
    :return: argv 数组
    """
    validated_args = list(args or [])
    family = detect_family(language)
    if family == "python":
        return ["python", entrypoint, *validated_args]
    if family == "node":
        return ["node", entrypoint, *validated_args]
    if family == "go":
        return ["go", "run", entrypoint, *validated_args]
    return [entrypoint, *validated_args]


def flatten_entrypoint(entrypoint: str, flattened_dir: str | None) -> str:
    """
    目录结构被打平后修正入口文件路径。

    :param entrypoint: 原入口文件路径
    :param flattened_dir: 被打平的顶层目录名，``None`` 表示未打平
    :return: 修正后的入口文件路径
    """
    if not flattened_dir or not entrypoint:
        return entrypoint

    prefix = f"{flattened_dir}/"
    if entrypoint == flattened_dir:
        return "."
    if entrypoint.startswith(prefix):
        return entrypoint[len(prefix):]
    return entrypoint
