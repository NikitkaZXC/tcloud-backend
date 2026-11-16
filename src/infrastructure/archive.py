import lzma
import shutil
import tarfile
import time
import zipfile
from pathlib import Path
from typing import BinaryIO

from src.infrastructure.enums import ArchiveFormat

CHUNK = 1024 * 1024
MEDIA_TYPES = {
    ArchiveFormat.ZIP: "application/zip",
    ArchiveFormat.TAR_GZ: "application/gzip",
    ArchiveFormat.TAR_BZ2: "application/x-bzip2",
    ArchiveFormat.TAR_XZ: "application/x-xz",
}
TAR_MODES = {
    ArchiveFormat.TAR_GZ: ("w|gz", {"compresslevel": 9}),
    ArchiveFormat.TAR_BZ2: ("w|bz2", {"compresslevel": 9}),
    ArchiveFormat.TAR_XZ: ("w|xz", {"preset": 9 | lzma.PRESET_EXTREME}),
}


class AppendOnly:
    def __init__(self, target: BinaryIO) -> None:
        self.write, self.flush = target.write, target.flush


class ArchiveWriter:
    def __init__(self, archive_format: ArchiveFormat, target: BinaryIO) -> None:
        self._target = target
        self._zip: zipfile.ZipFile | None = None
        self._tar: tarfile.TarFile | None = None
        if archive_format == ArchiveFormat.ZIP:
            self._zip = zipfile.ZipFile(AppendOnly(target), "w")
        else:
            mode, options = TAR_MODES[archive_format]
            self._tar = tarfile.open(
                fileobj=AppendOnly(target),
                mode=mode,
                format=tarfile.PAX_FORMAT,
                **options,
            )

    def add(self, source: Path, name: str, mtime: float) -> None:
        size = source.stat().st_size
        with source.open("rb") as data:
            if self._zip:
                info = zipfile.ZipInfo(name, time.localtime(mtime)[:6])
                info.file_size = size
                info.compress_type = zipfile.ZIP_DEFLATED
                info.compress_level = 9
                info.external_attr = 0o644 << 16
                with self._zip.open(info, "w") as entry:
                    shutil.copyfileobj(data, entry, CHUNK)
            else:
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = size, mtime, 0o644
                self._tar.addfile(info, data)
        self._target.flush()

    def close(self) -> None:
        (self._zip or self._tar).close()
        self._target.flush()
