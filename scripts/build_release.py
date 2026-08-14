from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
FULL_PATHS = (
    ".dockerignore",
    ".gitattributes",
    ".gitignore",
    "LICENSE",
    "README.md",
    "VERSION",
    "app",
    "deploy",
    "docs",
    "scripts",
)
FORBIDDEN_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "backup",
    "backups",
    "data",
    "dist",
    "plugins",
    "sessions",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".log",
    ".pyc",
    ".pyo",
    ".rdb",
    ".session",
    ".sqlite",
    ".sqlite3",
    ".zip",
}


def validate_version(version: str) -> str:
    version = version.strip().removeprefix("v")
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid version: {version!r}")
    return version


def file_version(version: str) -> str:
    return validate_version(version).replace(".", "_")


def expected_asset_names(version: str) -> tuple[str, str, str, str]:
    normalized = validate_version(version)
    suffix = file_version(normalized)
    return (
        f"slowlink_app_v{suffix}.zip",
        f"slowlink_v{suffix}_full.zip",
        f"slowlink_v{suffix}_update_log.txt",
        "SHA256SUMS.txt",
    )


def extract_changelog(version: str, changelog: Path = ROOT / "docs" / "CHANGELOG.md") -> str:
    normalized = validate_version(version)
    text = changelog.read_text(encoding="utf-8")
    marker = f"## [{normalized}]"
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"CHANGELOG missing {marker}")
    end = text.find("\n## [", start + len(marker))
    section = text[start:] if end < 0 else text[start:end]
    return section.strip() + "\n"


def _is_forbidden(relative: Path) -> bool:
    lowered_parts = {part.lower() for part in relative.parts}
    if lowered_parts & FORBIDDEN_PARTS:
        return True
    if relative.name.lower().startswith(".env") and relative.name != ".env.example":
        return True
    return relative.suffix.lower() in FORBIDDEN_SUFFIXES


def _collect(path: Path) -> list[Path]:
    if not path.exists():
        raise FileNotFoundError(f"required release path is missing: {path.relative_to(ROOT)}")
    candidates = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
    files = []
    for candidate in candidates:
        relative = candidate.relative_to(ROOT)
        if not _is_forbidden(relative):
            files.append(candidate)
    return sorted(files, key=lambda item: item.as_posix())


def _write_zip(output: Path, files: list[Path]) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in files:
            relative = source.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if source.suffix == ".sh" else 0o644) << 16
            archive.writestr(info, source.read_bytes())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(version: str, output_dir: Path) -> list[Path]:
    normalized = validate_version(version)
    repository_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if repository_version != normalized:
        raise ValueError(f"VERSION is {repository_version}, requested {normalized}")

    output_dir = output_dir.resolve()
    if output_dir == ROOT or ROOT in output_dir.parents:
        if output_dir == ROOT:
            raise ValueError("output directory cannot be repository root")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    app_name, full_name, log_name, checksum_name = expected_asset_names(normalized)
    app_path = output_dir / app_name
    full_path = output_dir / full_name
    log_path = output_dir / log_name
    checksum_path = output_dir / checksum_name

    app_files = _collect(ROOT / "app") + _collect(ROOT / "LICENSE")
    _write_zip(app_path, sorted(app_files, key=lambda item: item.as_posix()))
    full_files: list[Path] = []
    for relative in FULL_PATHS:
        full_files.extend(_collect(ROOT / relative))
    unique_full_files = sorted(set(full_files), key=lambda item: item.as_posix())
    _write_zip(full_path, unique_full_files)
    log_path.write_text(extract_changelog(normalized), encoding="utf-8", newline="\n")

    checksum_lines = [
        f"{_sha256(path)}  {path.name}"
        for path in (app_path, full_path)
    ]
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8", newline="\n")
    for line in checksum_lines:
        expected, name = line.split("  ", 1)
        if _sha256(output_dir / name) != expected:
            raise RuntimeError(f"checksum verification failed: {name}")

    return [app_path, full_path, log_path, checksum_path]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build verified SlowLink release assets")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for asset in build(args.version, args.output):
        print(asset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
