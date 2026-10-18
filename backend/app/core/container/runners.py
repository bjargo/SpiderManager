import os
import json
import logging
import hashlib
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any
from jinja2 import Template

logger = logging.getLogger(__name__)


# 渲染 Dockerfile 时使用的占位运行命令，仅用于计算缓存指纹。
_PLACEHOLDER_RUNTIME_ARGV = "[]"


class BaseRunner(ABC):
    @abstractmethod
    def get_dockerfile_content(self, context_vars: Dict[str, Any]) -> str:
        """返回构建该语言镜像所需的 Dockerfile 模板内容"""
        pass

    @abstractmethod
    def get_dockerignore_content(self) -> str:
        """返回该语言特有的 .dockerignore 内容"""
        pass

    def get_required_context_files(self) -> Dict[str, str]:
        """
        返回构建上下文中必须具备的占位文件（文件名 -> 占位内容）。

        模板会在 `COPY . /app/` 之前单独 COPY 依赖清单（如 requirements.txt）
        以命中依赖层缓存，因此文件缺失时需要使用占位内容补齐，避免构建失败。
        """
        return {}

    def get_cache_fingerprint(self) -> str:
        digest = hashlib.sha256()
        dockerfile = self.get_dockerfile_content(
            {
                "entrypoint": "",
                "runtime_argv": _PLACEHOLDER_RUNTIME_ARGV,
                "buildkit_enabled": True,
            }
        )
        dockerignore = self.get_dockerignore_content()
        for name, content in (
            ("Dockerfile.template", dockerfile),
            (".dockerignore.template", dockerignore),
        ):
            digest.update(name.encode("utf-8"))
            digest.update(content.encode("utf-8"))
        for name, content in sorted(self.get_required_context_files().items()):
            digest.update(name.encode("utf-8"))
            digest.update(content.encode("utf-8"))
        return digest.hexdigest()

    def _ensure_context_files(self, local_path: str) -> None:
        """补齐缺失的构建上下文占位文件，已存在的文件不会被覆盖。"""
        for filename, content in self.get_required_context_files().items():
            file_path = os.path.join(local_path, filename)
            if os.path.exists(file_path):
                continue
            parent_dir = os.path.dirname(file_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

    def prepare_context(self, local_path: str, context_vars: Dict[str, Any]) -> None:
        """在本地构建目录下准备相关构建文件（Dockerfile、.dockerignore等）"""
        self._ensure_context_files(local_path)

        # 写入 Dockerfile
        dockerfile_path = os.path.join(local_path, "Dockerfile")
        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(self.get_dockerfile_content(context_vars))

        # 写入 .dockerignore
        dockerignore_path = os.path.join(local_path, ".dockerignore")
        if not os.path.exists(dockerignore_path):
            with open(dockerignore_path, "w", encoding="utf-8") as f:
                f.write(self.get_dockerignore_content())

class PluginRunner(BaseRunner):
    """通过物理目录（包含 manifest.json 和 .template 等）动态加载的执行器"""

    # 模板文件在进程生命周期内视为不可变（新增/修改插件需重启生效），
    # 缓存编译后的 Template 对象，避免每次构建都重复读盘与编译。
    _template_cache: Dict[str, Template] = {}

    def __init__(self, plugin_dir: str, manifest: Dict[str, Any] | None = None):
        self.plugin_dir = plugin_dir
        self.manifest = manifest or {}

    def get_required_context_files(self) -> Dict[str, str]:
        return self.manifest.get("ensure_files", {})

    def prepare_context(self, local_path: str, context_vars: Dict[str, Any]) -> None:
        context_root = Path(local_path).resolve()
        plugin_root = Path(self.plugin_dir).resolve()

        for source_name, destination_name in self.manifest.get("include_files", {}).items():
            source_path = (plugin_root / source_name).resolve()
            destination_path = (context_root / destination_name).resolve()
            try:
                source_path.relative_to(plugin_root)
                destination_path.relative_to(context_root)
            except ValueError as exc:
                raise ValueError("Runtime plugin files must stay within their source and build directories.") from exc
            if not source_path.is_file():
                raise FileNotFoundError(f"Runtime plugin file not found: {source_path}")

            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination_path)

        super().prepare_context(local_path, context_vars)

        included_files = self.manifest.get("include_files", {})
        if included_files:
            dockerignore_path = context_root / ".dockerignore"
            lines = dockerignore_path.read_text(encoding="utf-8").splitlines()
            for destination_name in included_files.values():
                destination_path = (context_root / destination_name).resolve()
                relative_path = destination_path.relative_to(context_root).as_posix()
                path_parts = relative_path.split("/")
                for index in range(1, len(path_parts)):
                    parent_path = "/".join(path_parts[:index])
                    pattern = f"!{parent_path}/"
                    if pattern not in lines:
                        lines.append(pattern)
                pattern = f"!{relative_path}"
                if pattern not in lines:
                    lines.append(pattern)
            dockerignore_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def get_cache_fingerprint(self) -> str:
        digest = hashlib.sha256(super().get_cache_fingerprint().encode("ascii"))
        for source_name, destination_name in sorted(
            self.manifest.get("include_files", {}).items()
        ):
            source_path = (Path(self.plugin_dir).resolve() / source_name).resolve()
            digest.update(source_name.encode("utf-8"))
            digest.update(destination_name.encode("utf-8"))
            digest.update(source_path.read_bytes())
        return digest.hexdigest()

    def _render_template(self, filename: str, context_vars: Dict[str, Any]) -> str:
        template_path = os.path.join(self.plugin_dir, filename)
        if not os.path.exists(template_path):
            return ""

        tmpl = self._template_cache.get(template_path)
        if tmpl is None:
            with open(template_path, "r", encoding="utf-8") as f:
                tmpl = Template(f.read())
            self._template_cache[template_path] = tmpl
        return tmpl.render(**context_vars)

    def get_dockerfile_content(self, context_vars: Dict[str, Any]) -> str:
        return self._render_template("Dockerfile.template", context_vars)

    def get_dockerignore_content(self) -> str:
        # dockerignore 通常不需要渲染变更，直接当作文本也行。为保持统一也通过 Jinja2 渲染。
        return self._render_template(".dockerignore.template", {})


class RunnerFactory:
    _runners_cache: Dict[str, PluginRunner] = {}
    _is_initialized = False

    @classmethod
    def _initialize_registry(cls):
        """动态扫描 runtimes 目录并注册所有符合规范的插件"""
        if cls._is_initialized:
            return

        cls._runners_cache.clear()

        base_dir = os.path.dirname(os.path.abspath(__file__))
        runtimes_dir = os.path.join(base_dir, "runtimes")

        if not os.path.exists(runtimes_dir):
            cls._is_initialized = True
            return

        for entry in os.listdir(runtimes_dir):
            plugin_dir = os.path.join(runtimes_dir, entry)
            if not os.path.isdir(plugin_dir):
                continue

            manifest_path = os.path.join(plugin_dir, "manifest.json")
            if not os.path.exists(manifest_path):
                continue

            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)

                aliases = manifest.get("aliases", [])
                runner = PluginRunner(plugin_dir, manifest)
                for alias in aliases:
                    cls._runners_cache[alias.lower()] = runner
            except Exception as exc:
                logger.warning("跳过无法解析的插件目录 %s: %s", plugin_dir, exc)

        cls._is_initialized = True

    @classmethod
    def get_runner(cls, language: str) -> BaseRunner:
        """
        根据语言标识提取前缀并动态返回对应的执行器。
        如果未找到专属执行器，则返回 default 插件配置。
        """
        cls._initialize_registry()
        lang_key = language.lower().split(':')[0]

        if lang_key in cls._runners_cache:
            return cls._runners_cache[lang_key]

        # 尝试返回兜底的 default runner，如果也没有就报错。
        if "default" in cls._runners_cache:
            return cls._runners_cache["default"]

        raise ValueError(f"No configured runner or default plugin for language: {language}")
