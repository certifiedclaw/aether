MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024


def archive_size_error(entries) -> str | None:
    if len(entries) > MAX_ARCHIVE_MEMBERS:
        return f"archive contains too many members (limit {MAX_ARCHIVE_MEMBERS})"

    total_size = 0
    for entry in entries:
        size = getattr(entry, "file_size", getattr(entry, "size", 0))
        if not isinstance(size, int) or size < 0:
            return "archive contains an invalid member size"
        if size > MAX_ARCHIVE_MEMBER_BYTES:
            return f"archive member exceeds the {MAX_ARCHIVE_MEMBER_BYTES}-byte limit"
        total_size += size
        if total_size > MAX_ARCHIVE_TOTAL_BYTES:
            return f"archive expands beyond the {MAX_ARCHIVE_TOTAL_BYTES}-byte total limit"
    return None
