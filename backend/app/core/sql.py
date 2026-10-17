def quote_identifier(identifier: str) -> str:
    """Quote a dynamic SQL identifier using PostgreSQL identifier escaping."""
    return '"' + identifier.replace('"', '""') + '"'
