"""
数据查看模块 —— 查询护栏单元测试

覆盖：行数上限收敛、表名白名单校验、SQL 参数化构造（防注入）、排序归一化、
防护参数对外暴露值，确保"查询受限"这一核心约束不会被绕过。
"""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.dataview import services


def test_clamp_limit_enforces_hard_ceiling():
    """limit 必须被收敛到 [1, MAX_LIMIT]，防止一次拉取整表。"""
    assert services.clamp_limit(None) == services.DEFAULT_LIMIT
    assert services.clamp_limit(0) == 1
    assert services.clamp_limit(-5) == 1
    assert services.clamp_limit(20) == 20
    assert services.clamp_limit(10_000) == services.MAX_LIMIT
    assert services.clamp_limit("not-a-number") == services.DEFAULT_LIMIT


def test_max_limit_stays_small():
    """硬上限本身必须保持在一个很小的量级（防止后续被调大而失去保护）。"""
    assert services.MAX_LIMIT <= 100
    assert services.DEFAULT_LIMIT <= services.MAX_LIMIT
    assert services.STATEMENT_TIMEOUT_MS <= 5000
    assert services.MAX_CONCURRENT_QUERIES <= 8


def test_validate_table_name_accepts_plain_identifiers():
    """合法标识符原样通过（含首尾空白裁剪）。"""
    assert services.validate_table_name("comments") == "comments"
    assert services.validate_table_name("  news_data  ") == "news_data"
    assert services.validate_table_name("_tmp1") == "_tmp1"


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "   ",
        "comments; DROP TABLE spiders; --",
        "comments'",
        'comments"',
        "public.comments",
        "1comments",
        "comments-name",
        "comments name",
        "a" * 64,
    ],
)
def test_validate_table_name_rejects_unsafe_identifiers(bad_name):
    """注入字符、跨 schema 名、超长名一律拒绝。"""
    with pytest.raises(HTTPException) as exc_info:
        services.validate_table_name(bad_name)
    assert exc_info.value.status_code == 400


def test_build_rows_sql_is_parameterized_and_has_no_offset():
    """取值全部参数化；不接受 OFFSET，避免深翻页全表扫描。"""
    sql = services.build_rows_sql("comments", has_task_filter=True, has_cursor=True)
    assert '"comments"' in sql
    assert '"_task_id" = :task_id' in sql
    assert '"_id" < :cursor' in sql
    assert "LIMIT :limit" in sql
    assert "OFFSET" not in sql.upper()
    assert "task-1" not in sql


def test_build_rows_sql_without_filters_has_no_where_clause():
    """无过滤条件时不生成 WHERE，且支持正序。"""
    sql = services.build_rows_sql(
        "comments", has_task_filter=False, has_cursor=False, descending=False
    )
    assert "WHERE" not in sql
    assert '"comments"' in sql
    assert 'ORDER BY "_id" ASC' in sql


def test_build_rows_sql_quotes_identifier_defensively():
    """即使表名带引号也会被正确转义（纵深防御，正常路径已由白名单拦截）。"""
    sql = services.build_rows_sql('weird"name', has_task_filter=False, has_cursor=False)
    assert '"weird""name"' in sql


def test_build_task_count_sql_binds_task_id():
    """精确统计仅用于按任务过滤（走索引）的场景。"""
    sql = services.build_task_count_sql("comments")
    assert "COUNT(*)" in sql
    assert 'WHERE "_task_id" = :task_id' in sql


def test_normalize_order_defaults_to_desc():
    assert services.normalize_order(None) is True
    assert services.normalize_order("desc") is True
    assert services.normalize_order("ASC") is False


def test_guard_info_exposes_effective_limits():
    """响应中对外暴露的限制信息应与实际生效常量一致。"""
    guard = services.guard_info()
    assert guard["max_limit"] == services.MAX_LIMIT
    assert guard["default_limit"] == services.DEFAULT_LIMIT
    assert guard["statement_timeout_ms"] == services.STATEMENT_TIMEOUT_MS
    assert guard["max_concurrent_queries"] == services.MAX_CONCURRENT_QUERIES
    assert guard["pagination"] == "cursor"
    assert "只读" in guard["note"]
