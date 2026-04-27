import os
import shutil
import subprocess
import threading
import asyncio
import json
import logging
import hashlib
import time
from typing import Dict, Any, List
from datetime import datetime

from app.core import timezone

from redis.exceptions import RedisError, ConnectionError, TimeoutError
from app.core.redis import redis_manager
from app.worker.heartbeat import NODE_ID
from app.worker.project_loader import load_project
from app.worker.docker_manager import DockerManager
from app.core.source.factory import SourceFactory
from app.core.container.image_manager import image_manager
from config import settings

logger = logging.getLogger(__name__)


async def _update_task_status(
    task_id: str,
    status: str,
    node_id: str | None = None,
    error_detail: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    """更新 SpiderTask 数据库记录"""
    try:
        from app.db.database import async_session_maker
        from app.api.tasks.models import SpiderTask
        from sqlalchemy import update

        async with async_session_maker() as session:
            values: Dict[str, Any] = {"status": status}
            if node_id is not None:
                values["node_id"] = node_id
            if error_detail is not None:
                values["error_detail"] = error_detail
            if started_at is not None:
                values["started_at"] = started_at
            if finished_at is not None:
                values["finished_at"] = finished_at

            await session.execute(
                update(SpiderTask)
                .where(SpiderTask.task_id == task_id)
                .values(**values)
            )
            await session.commit()
    except Exception as e:
        logger.error("Failed to update task %s status in DB: %s", task_id, e)


async def _flush_logs(task_id: str, log_buffer: List[str]) -> None:
    """将日志缓冲区批量写入数据库"""
    if not log_buffer:
        return
    try:
        from app.db.database import async_session_maker
        from app.api.tasks.models import TaskLog

        async with async_session_maker() as session:
            for line in log_buffer:
                session.add(TaskLog(task_id=task_id, content=line))
            await session.commit()
    except Exception as e:
        logger.error("Failed to flush %d log lines for task %s: %s", len(log_buffer), task_id, e)


async def _emit_log(task_id: str, channel: str, message: str) -> None:
    """
    统一日志发送方法，同时完成三件事：
    1. Redis Pub/Sub 实时广播（供已连接的 WebSocket 客户端）
    2. Redis List 热缓冲（供迟到的 WebSocket 客户端回放，消除竞争时间差）
    3. 立即落盘 PostgreSQL（保证历史查询可见）

    :param task_id: 任务 UUID
    :param channel: Redis Pub/Sub 频道名
    :param message: 日志内容
    """
    hotbuf_key = f"{settings.LOG_HOTBUF_PREFIX}{task_id}"
    try:
        if redis_manager.client:
            pipe = redis_manager.client.pipeline()
            pipe.publish(channel, message)
            pipe.rpush(hotbuf_key, message)
            pipe.ltrim(hotbuf_key, -settings.LOG_HOTBUF_MAX, -1)
            pipe.expire(hotbuf_key, settings.LOG_HOTBUF_TTL)
            await pipe.execute()
    except Exception as e:
        logger.error("_emit_log redis error for task %s: %s", task_id, e)

    # 立即落盘，确保迟到用户查历史日志时可见
    await _flush_logs(task_id, [message])


def _stream_reader(
    stream,
    queue: "asyncio.Queue[str | None]",
    loop: asyncio.AbstractEventLoop,
) -> None:
    """在守护线程中逐行读取子进程输出流，放入 asyncio.Queue（线程安全）"""
    try:
        for raw_line in iter(stream.readline, b""):
            text = raw_line.decode("utf-8", errors="ignore").rstrip()
            if text:
                loop.call_soon_threadsafe(queue.put_nowait, text)
    except Exception:
        pass
    finally:
        stream.close()
        loop.call_soon_threadsafe(queue.put_nowait, None)  # 哨兵：流已结束


async def _execute_task_in_container(task_data: Dict[str, Any]) -> None:
    """
    DooD 模式：通过 DockerManager 在独立容器中运行爬虫。
    容器内自动完成: 下载代码 → 解压 → 执行脚本。
    """
    task_id: str = task_data.get("task_id", "unknown")
    project_id = task_data.get("project_id")
    source_type = task_data.get("source_type")
    source_url = task_data.get("source_url")
    script_path = task_data.get("script_path")
    language = task_data.get("language")

    status_key = f"task:status:{task_id}:{NODE_ID}" if NODE_ID else f"task:status:{task_id}:public"
    channel = f"log:channel:{task_id}"
    log_buffer: List[str] = []
    last_flush_time = time.time()
    container_name = f"spider-task-{task_id[:16]}"
    project_dir: str | None = None

    docker_mgr = DockerManager()

    try:
        # 1. 标记 running
        await _update_task_status(task_id, "running", node_id=NODE_ID, started_at=timezone.now())
        if redis_manager.client:
            running_status = {"task_id": task_id, "status": "running", "node_id": NODE_ID, "start_time": time.time()}
            await redis_manager.client.set(status_key, json.dumps(running_status), ex=7 * 24 * 3600)
        await _emit_log(task_id, channel, f"[SYSTEM: Preparing container build for task {task_id}]")

        # 2. 预编译镜像流程 (Remote Fingerprint -> Cache Check -> Build)
        try:
            handler = SourceFactory.get_handler(source_type)
            version_hash: str | None = None
            cached_image_tag: str | None = None

            # ── 2a. 尝试远程指纹探测 (Remote Probing) ──
            try:
                # 获取任务自带的构建参数 (包含 branch 等信息)
                source_kwargs = task_data.get("source_kwargs", {})
                remote_fingerprint = await asyncio.to_thread(handler.get_remote_fingerprint, source_url, **source_kwargs)

                if remote_fingerprint:
                    version_hash = remote_fingerprint  # 复用远程指纹，消除冗余哈希 [ZERO-DOWNLOAD]

                    # 提前计算镜像标签
                    script_hash = hashlib.sha256(script_path.encode()).hexdigest()[:8]
                    predict_tag = f"spider-{project_id}-{language.replace(':', '-')}:{version_hash[:12]}-{script_hash}"

                    await _emit_log(task_id, channel, f"[SYSTEM: Remote fingerprint detected: {version_hash[:12]}]")

                    # 检查镜像是否存在 (Cache Hit)
                    if await asyncio.to_thread(image_manager.check_image_exists, predict_tag):
                        cached_image_tag = predict_tag
                        await _emit_log(task_id, channel, f"[SYSTEM: Zero-Download Cache Hit! Image {cached_image_tag} found.]")
                        logger.info("Zero-Download Cache Hit for task %s: image %s found.", task_id, cached_image_tag)
            except Exception as probe_err:
                logger.warning("Remote fingerprint probe failed for task %s: %s. Falling back to download mode.", task_id, probe_err)
                await _emit_log(task_id, channel, f"[SYSTEM: Remote probe failed: {probe_err}. Falling back...]")

            # ── 2b. 执行构建 (仅在 Cache Miss 或 Probe 失败时) ──
            if not cached_image_tag:
                # 下载源码到临时目录
                project_dir = await load_project(task_id, source_type, source_url)

                # 如果探测失败或未获取到远程指纹，则回退到本地递归哈希扫描
                if not version_hash:
                    version_hash = await asyncio.to_thread(handler.get_version_hash, project_dir)

                # 组合最终标签
                script_hash = hashlib.sha256(script_path.encode()).hexdigest()[:8]
                image_tag = f"spider-{project_id}-{language.replace(':', '-')}:{version_hash[:12]}-{script_hash}"

                await _emit_log(task_id, channel, f"[SYSTEM: Source Hash: {version_hash[:12]}, Image Tag: {image_tag}]")
                await _emit_log(task_id, channel, "[SYSTEM: Building image...]")

                build_args = task_data.get("build_args", {})

                # 构建镜像
                final_image_tag = await asyncio.to_thread(
                    image_manager.build_image,
                    local_path=project_dir,
                    language=language,
                    image_tag=image_tag,
                    entrypoint=script_path,
                    build_args=build_args
                )
                task_data["image_tag"] = final_image_tag
            else:
                # 直接使用缓存项
                task_data["image_tag"] = cached_image_tag

            await _emit_log(task_id, channel, f"[SYSTEM: Image {task_data.get('image_tag')} ready. Launching container...]")

        except Exception as build_err:
            error_msg = f"Build Failed: {build_err}"
            logger.error("Failed to prepare image for task %s: %s", task_id, error_msg)
            await _emit_log(task_id, channel, f"[SYSTEM: {error_msg}]")
            raise RuntimeError(error_msg)

        # 3. 启动容器
        container = await asyncio.to_thread(docker_mgr.run_spider_container, task_data)

        await _emit_log(task_id, channel, f"[SYSTEM: Container {container.short_id} started, using image {task_data.get('image_tag')}]")

        # 4. 流式读取容器日志
        log_stream = await asyncio.to_thread(
            docker_mgr.get_container_logs, container_name, stream=True, follow=True
        )

        killed_by_user = False
        final_status = "success"
        return_code = 0

        while True:
            try:
                raw_chunk = await asyncio.to_thread(next, log_stream, None)
            except Exception as read_err:
                logger.warning("Error reading container logs: %s", read_err)
                break

            if raw_chunk is None:
                break

            text = raw_chunk.decode("utf-8", errors="ignore").rstrip() if isinstance(raw_chunk, bytes) else str(raw_chunk).rstrip()
            if not text:
                continue

            # 多行日志拆分后逐行处理
            for line in text.split("\n"):
                if not line:
                    continue

                # 推送 Redis Pub/Sub
                if redis_manager.client:
                    try:
                        await redis_manager.client.publish(channel, line)
                    except Exception as pub_err:
                        logger.error("Failed to publish log: %s", pub_err)

                log_buffer.append(line)

                current_time = time.time()
                if len(log_buffer) >= settings.LOG_FLUSH_SIZE or (current_time - last_flush_time) >= settings.LOG_FLUSH_INTERVAL:
                    await _flush_logs(task_id, log_buffer.copy())
                    log_buffer.clear()
                    last_flush_time = current_time

            # 检查 kill 信号
            if redis_manager.client:
                kill_key = f"task:kill:{task_id}"
                try:
                    is_killed = await redis_manager.client.exists(kill_key)
                    if is_killed:
                        logger.warning("Task %s received kill signal, stopping container.", task_id)
                        await _emit_log(task_id, channel, "[SYSTEM: Task killed by user]")
                        await asyncio.to_thread(docker_mgr.stop_container, container_name, 5)
                        await asyncio.to_thread(docker_mgr.remove_container, container_name, True)
                        await redis_manager.client.delete(kill_key)
                        killed_by_user = True
                        final_status = "cancelled"
                        return_code = -1
                        break
                except Exception as e:
                    logger.error("Error checking kill signal for task %s: %s", task_id, e)

            if killed_by_user:
                break

        # 4. 等待容器退出并获取退出码
        if not killed_by_user:
            try:
                result = await asyncio.to_thread(container.wait, timeout=30)
                return_code = result.get("StatusCode", -1)
            except Exception:
                # 容器可能已被 auto_remove 清理，视为正常
                return_code = 0

            if return_code == 0:
                final_status = "success"
                logger.info("Task %s container finished successfully.", task_id)
                await _emit_log(task_id, channel, "[SYSTEM: Task Finished Successfully]")
            else:
                final_status = "failed"
                logger.error("Task %s container exited with code %s.", task_id, return_code)
                await _emit_log(task_id, channel, f"[SYSTEM: Task Failed with code {return_code}]")

        # Flush 剩余日志
        if log_buffer:
            await _flush_logs(task_id, log_buffer.copy())
            log_buffer.clear()

        # 写回最终状态
        if redis_manager.client:
            end_status = {
                "task_id": task_id, "status": final_status,
                "node_id": NODE_ID, "return_code": return_code, "end_time": time.time(),
            }
            await redis_manager.client.set(status_key, json.dumps(end_status), ex=7 * 24 * 3600)

        await _update_task_status(
            task_id, final_status,
            finished_at=timezone.now(),
            error_detail=f"exit code: {return_code}" if return_code != 0 else None,
        )

    except Exception as e:
        logger.error("Unexpected error executing task %s in container: %s", task_id, e, exc_info=True)

        if log_buffer:
            await _flush_logs(task_id, log_buffer.copy())
            log_buffer.clear()

        if redis_manager.client:
            error_status = {"task_id": task_id, "status": "error", "node_id": NODE_ID, "error_detail": str(e)}
            await redis_manager.client.set(status_key, json.dumps(error_status), ex=7 * 24 * 3600)
        await _emit_log(task_id, channel, f"[SYSTEM: Task failed with unexpected error: {e}]")
        await _update_task_status(task_id, "error", error_detail=str(e), finished_at=timezone.now())

    finally:
        # 显式清理容器
        await asyncio.to_thread(docker_mgr.remove_container, container_name, force=True)
        # 异步关闭 Docker 客户端
        await asyncio.to_thread(docker_mgr.close)

        # 清理 Redis 热缓冲 List（任务结束后不再需要）
        if redis_manager.client:
            try:
                await redis_manager.client.delete(f"{settings.LOG_HOTBUF_PREFIX}{task_id}")
            except Exception:
                pass

        # 显式清理为了构建镜像临时下载的源码目录 (同步 IO 放入线程)
        if project_dir and os.path.exists(project_dir):
            try:
                await asyncio.to_thread(shutil.rmtree, project_dir)
                logger.info("Cleanup: Removed build context directory %s for task %s", project_dir, task_id)
            except Exception as e:
                logger.error("Cleanup Warning: Failed to fully remove build context directory %s: %s", project_dir, e)


async def execute_task(task_data: Dict[str, Any]) -> None:
    """
    任务执行入口：根据 payload 中是否包含 language 字段自动分流。
    - 有 language → DooD 容器模式（_execute_task_in_container）
    - 无 language → 原有子进程模式
    """
    # ── DooD 容器模式分流 ──
    if task_data.get("language"):
        await _execute_task_in_container(task_data)
        return

    # ── 以下为原有子进程模式 ──
    task_id = task_data.get("task_id", "unknown")
    project_id = task_data.get("project_id")
    source_type = task_data.get("source_type")
    source_url = task_data.get("source_url")
    script_path = task_data.get("script_path")
    timeout = task_data.get("timeout_seconds", 3600)

    if not script_path or not project_id or not source_url:
        logger.error("Task %s is missing required fields: project_id, source_url, or script_path.", task_id)
        return

    status_key = f"task:status:{task_id}:{NODE_ID}" if NODE_ID else f"task:status:{task_id}:public"
    channel = f"log:channel:{task_id}"
    project_dir: str | None = None
    process: subprocess.Popen | None = None
    log_buffer: List[str] = []
    last_flush_time = time.time()

    try:
        # 1. 记录 Running 状态
        await _update_task_status(task_id, "running", node_id=NODE_ID, started_at=timezone.now())

        if redis_manager.client:
            running_status = {"task_id": task_id, "status": "running", "node_id": NODE_ID, "start_time": time.time()}
            await redis_manager.client.set(status_key, json.dumps(running_status), ex=7 * 24 * 3600)
        await _emit_log(task_id, channel, f"[SYSTEM: Preparing environment for project {project_id}]")

        logger.info("Task %s loading project: %s", task_id, project_id)

        # 2. 动态加载代码
        try:
            project_dir = await load_project(task_id, source_type, source_url)
        except Exception as e:
            logger.error("Failed to load project %s for task %s: %s", project_id, task_id, e)
            await _emit_log(task_id, channel, f"[SYSTEM: Failed to load project code: {e}]")
            raise

        # 2.3 自动打平目录结构 (针对子进程模式)
        try:
            flattened_dir = await asyncio.to_thread(image_manager._flatten_directory, project_dir)
            if flattened_dir and script_path:
                # 修正 script_path
                parts = script_path.split()
                new_parts = []
                prefix = f"{flattened_dir}/"
                for p in parts:
                    if p.startswith(prefix):
                        new_parts.append(p[len(prefix):])
                    elif p == flattened_dir:
                        new_parts.append(".")
                    else:
                        new_parts.append(p)
                new_script_path = " ".join(new_parts)
                if new_script_path != script_path:
                    logger.info("Adjusted subprocess script_path from '%s' to '%s' due to flattening.", script_path, new_script_path)
                    script_path = new_script_path
        except Exception as e:
            logger.warning("Failed to flatten directory in subprocess mode: %s", e)

        # 2.5 校验脚本文件是否存在（防御空目录 / 下载失败的场景）
        # script_path 形如 "python main.py"，提取实际的文件名部分
        script_parts = script_path.strip().split()
        script_file = script_parts[-1] if script_parts else script_path
        full_script_path = os.path.join(project_dir, script_file)
        if not os.path.isfile(full_script_path):
            error_msg = (
                f"Script file '{script_file}' not found in project directory '{project_dir}'. "
                f"Available files: {os.listdir(project_dir) if os.path.isdir(project_dir) else 'dir not found'}"
            )
            logger.error("Task %s: %s", task_id, error_msg)
            await _emit_log(task_id, channel, f"[SYSTEM: {error_msg}]")
            raise FileNotFoundError(error_msg)

        logger.info("Task %s executing script: %s", task_id, script_path)

        # 3. 使用 subprocess.Popen 拉起进程（跨平台兼容）
        # 注入 PYTHONUNBUFFERED=1 禁用 Python 子进程的输出缓冲，确保日志实时到达 pipe
        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"

        process = subprocess.Popen(
            script_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=project_dir,
            shell=True,
            env=child_env,
        )

        # 通过 asyncio.Queue + 守护线程桥接同步 IO 和异步主循环
        log_queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        t_out = threading.Thread(target=_stream_reader, args=(process.stdout, log_queue, loop), daemon=True)
        t_err = threading.Thread(target=_stream_reader, args=(process.stderr, log_queue, loop), daemon=True)
        t_out.start()
        t_err.start()

        streams_done = 0       # 两个流各发一个 None 哨兵
        start_time = time.time()
        killed_by_user = False

        try:
            while streams_done < 2:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    raise asyncio.TimeoutError()

                # 检查 Redis 是否发来了 Kill Signal
                if redis_manager.client:
                    kill_key = f"task:kill:{task_id}"
                    try:
                        is_killed = await redis_manager.client.exists(kill_key)
                        if is_killed:
                            logger.warning(f"Task {task_id} received kill signal.")
                            if redis_manager.client:
                                await redis_manager.client.publish(channel, f"[SYSTEM: Task killed by user]")
                            process.terminate()
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                process.kill()

                            await redis_manager.client.delete(kill_key)
                            killed_by_user = True
                            final_status = "cancelled"
                            return_code = -1
                            # 提前退出执行循环
                            break
                    except Exception as e:
                        logger.error(f"Error checking kill signal for task {task_id}: {e}")

                try:
                    text = await asyncio.wait_for(log_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    # 队列暂时无数据，检查进程是否已退出
                    if process.poll() is not None and log_queue.empty():
                        break
                    continue

                if text is None:
                    streams_done += 1
                    continue

                # 推送到 Redis Pub/Sub + 热缓冲，业务日志仍走批量 flush 以提高性能
                hotbuf_key = f"{settings.LOG_HOTBUF_PREFIX}{task_id}"
                if redis_manager.client:
                    try:
                        pipe = redis_manager.client.pipeline()
                        pipe.publish(channel, text)
                        pipe.rpush(hotbuf_key, text)
                        pipe.ltrim(hotbuf_key, -settings.LOG_HOTBUF_MAX, -1)
                        pipe.expire(hotbuf_key, settings.LOG_HOTBUF_TTL)
                        await pipe.execute()
                    except Exception as pub_err:
                        logger.error("Failed to publish/hotbuf log: %s", pub_err)

                # 缓冲日志，批量写入 DB
                log_buffer.append(text)

                current_time = time.time()
                if len(log_buffer) >= settings.LOG_FLUSH_SIZE or (current_time - last_flush_time) >= settings.LOG_FLUSH_INTERVAL:
                    await _flush_logs(task_id, log_buffer.copy())
                    log_buffer.clear()
                    last_flush_time = current_time

            # 等待进程真正退出
            await asyncio.to_thread(process.wait, timeout=5)

            # 如果是用户主动终止，跳过状态重新判断，保留 cancelled 状态
            if not killed_by_user:
                return_code = process.returncode
                if return_code == 0:
                    logger.info("Task %s finished successfully.", task_id)
                    final_status = "success"
                    await _emit_log(task_id, channel, "[SYSTEM: Task Finished Successfully]")
                else:
                    logger.error("Task %s failed with code %s.", task_id, return_code)
                    final_status = "failed"
                    await _emit_log(task_id, channel, f"[SYSTEM: Task Failed with code {return_code}]")

        except asyncio.TimeoutError:
            logger.warning("Task %s timed out after %s seconds. Killing process...", task_id, timeout)
            await _emit_log(task_id, channel, f"[SYSTEM: Task Timed Out after {timeout} seconds]")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            final_status = "timeout"
            return_code = -1

        # Flush 剩余日志
        if log_buffer:
            await _flush_logs(task_id, log_buffer.copy())
            log_buffer.clear()

        # 写回最终状态到 Redis
        if redis_manager.client:
            end_status = {
                "task_id": task_id,
                "status": final_status,
                "node_id": NODE_ID,
                "return_code": return_code,
                "end_time": time.time(),
            }
            await redis_manager.client.set(status_key, json.dumps(end_status), ex=7 * 24 * 3600)

        # 写回最终状态到数据库
        await _update_task_status(
            task_id, final_status,
            finished_at=timezone.now(),
            error_detail=f"exit code: {return_code}" if return_code != 0 else None,
        )

    except Exception as e:
        logger.error("Unexpected error executing task %s: %s", task_id, e, exc_info=True)

        # Flush 剩余日志
        if log_buffer:
            await _flush_logs(task_id, log_buffer.copy())
            log_buffer.clear()

        if redis_manager.client:
            error_status = {
                "task_id": task_id,
                "status": "error",
                "node_id": NODE_ID,
                "error_detail": str(e),
            }
            await redis_manager.client.set(status_key, json.dumps(error_status), ex=7 * 24 * 3600)
        await _emit_log(task_id, channel, f"[SYSTEM: Task failed with unexpected error: {e}]")
        await _update_task_status(task_id, "error", error_detail=str(e), finished_at=timezone.now())

    finally:
        # 清理 Redis 热缓冲 List
        if redis_manager.client:
            try:
                await redis_manager.client.delete(f"{_LOG_HOTBUF_PREFIX}{task_id}")
            except Exception:
                pass

        # 4. 彻底清理临时代码目录，防止磁盘爆满
        if project_dir and os.path.exists(project_dir):
            try:
                await asyncio.to_thread(shutil.rmtree, project_dir)
                logger.info("Cleanup: Removed project directory %s for task %s", project_dir, task_id)
            except Exception as e:
                logger.error("Cleanup Warning: Failed to fully remove project directory %s: %s", project_dir, e)

        # 防止 process 处于悬挂状态
        if process and process.poll() is None:
            try:
                process.kill()
            except Exception:
                pass

async def listen_for_tasks() -> None:
    """监听专属队列和公共队列的主循环"""
    node_queue_key = f"{settings.NODE_QUEUE_PREFIX}{NODE_ID}"
    logger.info(f"Worker {NODE_ID} starting to listen for tasks on {node_queue_key} and {settings.PUBLIC_QUEUE_KEY}...")

    while True:
        try:
            if not redis_manager.client:
                await asyncio.sleep(1)
                continue

            # 使用 BLPOP 阻塞监听多个队列，优先监听专属队列
            result = await redis_manager.client.blpop([node_queue_key, settings.PUBLIC_QUEUE_KEY], timeout=5)

            if result:
                queue_name, task_json = result
                # 兼容 str/bytes 返回值
                queue_name_str = queue_name.decode("utf-8") if isinstance(queue_name, bytes) else queue_name
                task_str = task_json.decode("utf-8") if isinstance(task_json, bytes) else task_json

                logger.info(f"Received task from {queue_name_str}")

                try:
                    task_data = json.loads(task_str)
                    # 将任务执行放入后台，以免单个任务阻塞 Worker 继续消费队列
                    asyncio.create_task(execute_task(task_data))
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode task JSON data: {e}. Raw data: {task_str}")

        except TimeoutError:
            # BLPOP 超时属于正常轮询行为，不做错误处理
            continue
        except (ConnectionError, RedisError) as e:
            logger.error("Redis error while waiting for tasks: %s", e)
            await asyncio.sleep(2)

        except asyncio.CancelledError:
            logger.info("Task listener cancelled. Shutting down...")
            break

        except Exception as e:
            logger.error(f"Unexpected error in task listener: {e}")
            await asyncio.sleep(1)

async def start_task_listener() -> asyncio.Task:
    """启动任务监听器"""
    return asyncio.create_task(listen_for_tasks())
