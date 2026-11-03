import json
import os
from pathlib import Path
import shutil
import tempfile
import tarfile
import zipfile

from verify_package import main as verify_package


def write_archive(destination, output):
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as zipped:
            for source in sorted(destination.rglob("*")):
                if source.is_file():
                    info = zipfile.ZipInfo(source.relative_to(destination).as_posix())
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    with source.open("rb") as stream, zipped.open(info, "w") as target:
                        shutil.copyfileobj(stream, target)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def build(root):
    root = Path(root).resolve()
    version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    archive = root / f"broader-vids-{version}.tgz"
    verify_package(archive)
    destination = root / "dist/plugins/broader-vids"
    if destination.resolve() != destination:
        raise ValueError("The plugin output must not contain symbolic links.")
    if not destination.is_relative_to(root / "dist") or destination == root / "dist":
        raise ValueError("The plugin output must remain inside the build directory.")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(archive) as package:
        for member in package.getmembers():
            if not member.isfile():
                continue
            relative = Path(member.name).relative_to("package")
            target = destination / relative
            if not target.resolve().is_relative_to(destination):
                raise ValueError("The package contains an unsafe path.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.extractfile(member).read())
    local = root / "dist/local-marketplace"
    if local.resolve() != local:
        raise ValueError("The local marketplace must not contain symbolic links.")
    payload = local / "plugins/broader-vids"
    if payload.resolve() != payload:
        raise ValueError("The local plugin payload must not contain symbolic links.")
    if payload.exists():
        shutil.rmtree(payload)
    shutil.copytree(destination, payload)
    catalog = json.loads(
        (root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    catalog["name"] = "broader-vids-local"
    catalog["interface"]["displayName"] = "Broader Vids (local)"
    catalog["plugins"][0]["source"] = {
        "source": "local",
        "path": "./plugins/broader-vids",
    }
    catalog_path = local / ".agents/plugins/marketplace.json"
    if catalog_path.resolve() != catalog_path:
        raise ValueError("The local catalog must not contain symbolic links.")
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    output = root / "dist" / f"broader-vids-{version}.zip"
    write_archive(destination, output)
    print(
        json.dumps(
            {
                "plugin_directory": str(destination),
                "zip": str(output),
                "local_marketplace": str(local),
                "version": version,
            },
            indent=2,
        )
    )
    return destination


if __name__ == "__main__":
    build(Path(__file__).resolve().parent.parent)
