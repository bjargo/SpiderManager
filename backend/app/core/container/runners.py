import os
import json
from abc import ABC, abstractmethod
from typing import Dict, Any
from jinja2 import Template

class BaseRunner(ABC):
    @abstractmethod
    def get_dockerfile_content(self, context_vars: Dict[str, Any]) -> str:
        """返回构建该语言镜像所需的 Dockerfile 模板内容"""
        pass

    @abstractmethod
    def get_dockerignore_content(self) -> str:
        """返回该语言特有的 .dockerignore 内容"""
        pass

    def prepare_context(self, local_path: str, context_vars: Dict[str, Any]) -> None:
        """在本地构建目录下准备相关构建文件（Dockerfile、.dockerignore等）"""
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
    def __init__(self, plugin_dir: str):
        self.plugin_dir = plugin_dir

    def _render_template(self, filename: str, context_vars: Dict[str, Any]) -> str:
        template_path = os.path.join(self.plugin_dir, filename)
        if not os.path.exists(template_path):
            return ""
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        tmpl = Template(content)
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
                runner = PluginRunner(plugin_dir)
                for alias in aliases:
                    cls._runners_cache[alias.lower()] = runner
            except Exception:
                pass # 忽略解析失败的插件

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
