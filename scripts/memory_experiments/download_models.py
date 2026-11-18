"""Explicit provisioning only. Inference never calls this downloader."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
LOCK = Path(__file__).with_name("semantic-models.lock.json")


def main():
    root = ROOT / "artifacts/semantic-feasibility/models"
    manifest = []
    for model in json.loads(LOCK.read_text()):
        folder = root / model["repo"].split("/")[-1]
        folder.mkdir(parents=True, exist_ok=True)
        for file in model["files"]:
            target = folder / file["name"]
            url = f'https://huggingface.co/{model["repo"]}/resolve/{model["revision"]}/{file["name"]}'
            downloaded = not target.exists()
            if downloaded:
                temporary = target.with_suffix(target.suffix + ".partial")
                with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            data = (temporary if downloaded else target).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if len(data) != file["size"]:
                raise ValueError("model_file_size_mismatch")
            if file["sha256"]:
                if digest != file["sha256"]:
                    raise ValueError("model_file_sha256_mismatch")
            elif hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != file["git_blob"]:
                raise ValueError("model_file_git_blob_mismatch")
            if downloaded:
                temporary.replace(target)
            manifest.append({"repo": model["repo"], "revision": model["revision"], "file": file["name"], "bytes": len(data), "sha256": digest})
            print(model["repo"], file["name"], len(data), "verified", flush=True)
    (root.parent / "download-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
