"""Explicit model installation; never imported by the server."""

import argparse
import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path

ASSETS = (
    (
        "asr-models",
        "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
        "27ffbd9ee24ad186d99acc2f6354d7992b27bcab490812510665fa8f9389c5f8",
    ),
    (
        "tts-models",
        "vits-icefall-zh-aishell3",
        "ab468db3a3308cdd861495e0db2f25d79418a0c00639f74944c7cdf5dd8c6ec1",
    ),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(".models"))
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    receipts = []
    for release, name, expected_digest in ASSETS:
        url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/{release}/{name}.tar.bz2"
        archive = root / f"{name}.tar.bz2"
        if not archive.exists():
            temporary = archive.with_suffix(".part")
            print(f"Downloading {name}", flush=True)
            urllib.request.urlretrieve(url, temporary)
            temporary.replace(archive)
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != expected_digest:
            raise ValueError(f"Model archive checksum mismatch: {name}")
        if not (root / name).exists():
            with tarfile.open(archive) as bundle:
                # Reject links and paths outside the expected model directory, before extraction.
                for member in bundle.getmembers():
                    target = (root / member.name).resolve()
                    if not target.is_relative_to(root / name) or member.issym() or member.islnk():
                        raise ValueError("Unsafe model archive")
                bundle.extractall(root, filter="data")
        receipts.append({"model": name, "source": url, "sha256": digest})
    (root / "download-receipts.json").write_text(json.dumps(receipts, indent=2), encoding="utf-8")
    print(f"Models installed at {root}")


if __name__ == "__main__":
    main()
