from enum import StrEnum


class ArchiveFormat(StrEnum):
    ZIP = "zip"
    SEVEN_Z = "7z"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    TAR_XZ = "tar.xz"
