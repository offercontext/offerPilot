"""Offline Linux CPU-only model probe. No product imports or inference API calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import threading
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"


def main():
    started = time.perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--family", choices=["bge", "e5"], required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    locks = json.loads(Path(__file__).with_name("semantic-models.lock.json").read_text())
    lock = next(m for m in locks if ("bge-" in m["repo"]) == (args.family == "bge"))
    if args.model_dir.exists() and {p.name for p in args.model_dir.iterdir()} - {f["name"] for f in lock["files"]}:
        raise ValueError("unexpected_model_directory_entry")
    # Missing or tampered files never trigger download or model execution.
    verified = []
    for entry in lock["files"]:
        path = args.model_dir / entry["name"]
        if not path.is_file():
            args.output.write_text(json.dumps({"status": "unavailable", "reason": "model_file_missing", "runtime_integration": False}))
            return
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        valid = digest == entry["sha256"] if entry["sha256"] else hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == entry["git_blob"]
        if not valid or len(raw) != entry["size"]:
            raise ValueError("model_file_integrity_failed")
        verified.append({"name": entry["name"], "bytes": len(raw), "sha256": digest})
        del raw
    verify_seconds = time.perf_counter() - started
    import psutil
    process = psutil.Process()
    peak = [process.memory_info().rss]
    stop = threading.Event()

    def sample():
        while not stop.wait(0.01):
            peak[0] = max(peak[0], process.memory_info().rss)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    import torch
    from transformers import AutoModel, AutoTokenizer
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir, local_files_only=True, trust_remote_code=False)
    model = AutoModel.from_pretrained(args.model_dir, local_files_only=True, trust_remote_code=False, use_safetensors=True)
    model.eval()
    loaded_seconds = time.perf_counter() - started
    fixture = json.loads(args.fixture.read_text())
    passages = [item["content"] for item in fixture["memories"]]
    queries = [item["query"] for item in fixture["queries"]]

    def encode(texts, query):
        if args.family == "bge":
            texts = [("为这个句子生成表示以用于检索相关文章：" if query else "") + text for text in texts]
        else:
            texts = [("query: " if query else "passage: ") + text for text in texts]
        results = []
        for index in range(0, len(texts), 8):
            batch = tokenizer(texts[index:index + 8], max_length=512, padding=True, truncation=True, return_tensors="pt")
            with torch.inference_mode():
                hidden = model(**batch).last_hidden_state
                if args.family == "bge":
                    vectors = hidden[:, 0]
                else:
                    masked = hidden.masked_fill(~batch["attention_mask"][..., None].bool(), 0.0)
                    vectors = masked.sum(dim=1) / batch["attention_mask"].sum(dim=1)[..., None]
                results.append(torch.nn.functional.normalize(vectors, p=2, dim=1))
        return torch.cat(results)

    cpu_start = sum(process.cpu_times()[:2])
    encode_start = time.perf_counter()
    p = encode(passages, False)
    q = encode(queries, True)
    batch_seconds = time.perf_counter() - encode_start
    inference_cpu_seconds = sum(process.cpu_times()[:2]) - cpu_start
    scores = (q @ p.T).tolist()
    warm = []
    for _ in range(20):
        tick = time.perf_counter()
        encode([queries[0]], True)
        warm.append(time.perf_counter() - tick)
    stop.set()
    sampler.join()
    peak[0] = max(peak[0], process.memory_info().rss)
    result = {"status": "completed", "repo": lock["repo"], "revision": lock["revision"],
              "files": verified, "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "lock_sha256": hashlib.sha256(Path(__file__).with_name("semantic-models.lock.json").read_bytes()).hexdigest(),
              "dependency_manifest_sha256": hashlib.sha256(Path(__file__).with_name("semantic-python-freeze.txt").read_bytes()).hexdigest(), "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              "platform": platform.platform(), "python": platform.python_version(), "torch": torch.__version__,
              "cpu_threads": 2, "logical_cpus": os.cpu_count(), "dimension": int(p.shape[1]),
              "verification_seconds": verify_seconds, "fresh_process_ready_seconds": loaded_seconds,
              "batch_seconds": batch_seconds, "inference_cpu_seconds": inference_cpu_seconds,
              "peak_sampled_rss_bytes": peak[0], "linux_process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024, "repeated_first_query_median_seconds": statistics.median(warm),
              "repeated_first_query_p95_seconds": sorted(warm)[18], "query_count": len(queries), "passage_count": len(passages),
              "scores": scores, "notes": "CPU float32, local_files_only, 512 token truncation; fresh process not cold OS cache; no Windows/package inference"}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in {"scores", "files"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
