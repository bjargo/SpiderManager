import os
import shutil
import sys
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.container.runners import RunnerFactory, PluginRunner
from app.core.run_config import build_exec_argv


def test_runner_factory_detection():
    """测试不同语言标识的前缀解析与执行器匹配"""
    py_runner = RunnerFactory.get_runner("python:3.11-slim")
    assert isinstance(py_runner, PluginRunner)

    node_runner = RunnerFactory.get_runner("node")
    assert isinstance(node_runner, PluginRunner)

    go_runner = RunnerFactory.get_runner("golang")
    assert isinstance(go_runner, PluginRunner)

    # 兜底到 default
    default_runner = RunnerFactory.get_runner("ruby")
    assert isinstance(default_runner, PluginRunner)


def test_exec_argv_per_runtime():
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


def test_runner_prepare_context():
    """测试模板渲染与构建文件生成 (.dockerignore 与 Dockerfile)"""
    test_dir = "./temp_test_runner_context"
    os.makedirs(test_dir, exist_ok=True)
    try:
        runner = RunnerFactory.get_runner("python")
        runner.prepare_context(
            test_dir,
            {"entrypoint": "run.py", "runtime_argv": '["python", "run.py"]'},
        )

        dockerfile = os.path.join(test_dir, "Dockerfile")
        dockerignore = os.path.join(test_dir, ".dockerignore")

        assert os.path.exists(dockerfile)
        assert os.path.exists(dockerignore)
        # 依赖清单缺失时由 manifest 的 ensure_files 补齐占位文件
        assert os.path.exists(os.path.join(test_dir, "requirements.txt"))
        assert os.path.exists(os.path.join(test_dir, "spidermanager_sdk.py"))

        with open(dockerfile, "r", encoding="utf-8") as f:
            content = f.read()
            assert 'CMD ["python", "run.py"]' in content
            assert "WORKDIR /app" in content

        with open(dockerignore, "r", encoding="utf-8") as f:
            ignore_content = f.read()
            assert ".git" in ignore_content
            assert "__pycache__" in ignore_content
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_python_runner_build_cache_optimization():
    """依赖层应先于业务代码 COPY，且仅在 BuildKit 下注入 pip 缓存挂载。"""
    runner = RunnerFactory.get_runner("python")
    with_buildkit = runner.get_dockerfile_content(
        {
            "entrypoint": "run.py",
            "runtime_argv": '["python", "run.py"]',
            "buildkit_enabled": True,
        }
    )
    without_buildkit = runner.get_dockerfile_content(
        {
            "entrypoint": "run.py",
            "runtime_argv": '["python", "run.py"]',
            "buildkit_enabled": False,
        }
    )

    assert "--mount=type=cache,target=/root/.cache/pip" in with_buildkit
    assert "--mount=type=cache" not in without_buildkit
    assert "--no-cache-dir" not in with_buildkit
    assert with_buildkit.index("COPY requirements.txt") < with_buildkit.index("COPY . /app/")
    assert "pip show spidermanager-sdk" in with_buildkit


def test_dockerfile_cmd_never_uses_shell_form():
    """所有内置运行时都必须把运行命令渲染为 exec 形式 CMD，参数不经过 Shell。"""
    import json

    for language, entrypoint, args in (
        ("python", "main.py", ["--mode", "all"]),
        ("nodejs", "index.js", []),
        ("golang", "main.go", ["-v"]),
        ("default", "run.sh", []),
    ):
        runner = RunnerFactory.get_runner(language)
        dockerfile = runner.get_dockerfile_content(
            {
                "entrypoint": entrypoint,
                "runtime_argv": json.dumps(build_exec_argv(language, entrypoint, args)),
                "buildkit_enabled": True,
            }
        )
        cmd_lines = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]
        assert cmd_lines, f"{language} template has no CMD line"
        assert cmd_lines[-1].startswith("CMD ["), f"{language} CMD is not exec form"


def test_builtin_sdk_is_unignored_when_project_dockerignore_excludes_it(tmp_path):
    dockerignore = tmp_path / ".dockerignore"
    dockerignore.write_text("spidermanager_sdk.py\n", encoding="utf-8")

    RunnerFactory.get_runner("python").prepare_context(
        str(tmp_path),
        {"entrypoint": "run.py", "runtime_argv": '["python", "run.py"]'},
    )

    lines = dockerignore.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "!spidermanager_sdk.py"


def test_runtime_asset_changes_cache_fingerprint(tmp_path):
    (tmp_path / "Dockerfile.template").write_text("FROM python:3.11\n", encoding="utf-8")
    (tmp_path / ".dockerignore.template").write_text("__pycache__\n", encoding="utf-8")
    asset = tmp_path / "sdk.py"
    asset.write_text("VERSION = 1\n", encoding="utf-8")
    runner = PluginRunner(
        str(tmp_path),
        {"include_files": {"sdk.py": "sdk.py"}},
    )

    first = runner.get_cache_fingerprint()
    asset.write_text("VERSION = 2\n", encoding="utf-8")
    assert runner.get_cache_fingerprint() != first
