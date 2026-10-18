from dataclasses import dataclass, field
from pathlib import Path
import os
import shutil


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    root: Path = ROOT
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve())
    clouds_dir: Path = field(default_factory=lambda: Path(os.getenv("POINTCLOUDS_DIR", str(ROOT / "pointclouds"))).resolve())
    runtime_dir: Path = field(default_factory=lambda: Path(os.getenv("RUNTIME_DIR", str(ROOT / ".runtime" / "python"))).resolve())
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    start_worker: bool = field(default_factory=lambda: os.getenv("START_WORKER", "1") == "1")
    secure_cookies: bool = field(default_factory=lambda: os.getenv("SECURE_COOKIES", "0") == "1")
    max_upload_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024**3))))
    session_days: int = 7

    def __post_init__(self):
        if not self.database_url:
            self.database_url = "sqlite:///" + (self.data_dir / "app.sqlite3").as_posix()

    @property
    def converter(self) -> Path:
        name = "PotreeConverter.exe" if os.name == "nt" else "PotreeConverter"
        return Path(os.getenv("POTREE_CONVERTER", str(self.root / "tools" / "PotreeConverter" / name)))

    @property
    def laszip(self) -> Path:
        return Path(os.getenv("LASZIP_LIBRARY", str(self.root / "tools" / "PotreeConverter" / "laszip.dll")))

    @property
    def pdal(self) -> Path:
        executable = Path("Library/bin/pdal.exe" if os.name == "nt" else "bin/pdal")
        local = self.root / ".runtime" / "pdal" / executable
        return Path(os.getenv("PDAL_PATH") or (str(local) if local.is_file() else shutil.which("pdal") or str(local)))

    def prepare(self):
        for directory in (self.data_dir, self.clouds_dir, self.runtime_dir):
            directory.mkdir(parents=True, exist_ok=True)
