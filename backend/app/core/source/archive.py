import shutil
import stat
import zipfile
from pathlib import Path


def extract_zip_safely(archive: zipfile.ZipFile, destination: str) -> None:
    """Extract an archive without allowing paths or symlinks to escape destination."""
    root = Path(destination).resolve()

    for member in archive.infolist():
        target = (root / member.filename).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"ZIP member escapes extraction directory: {member.filename}") from exc

        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"ZIP symlinks are not allowed: {member.filename}")

        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
