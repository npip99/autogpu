"""Upload the macros_instanced 3D viewer + assets to Cloudflare R2.

Re-runnable: small uploads always overwrite; large `.glb` uploads + the
`cellfeol/` tree use ETag comparison so unchanged files are skipped.

Reads credentials from `.env` at repo root:
    CLOUDFLARE_R2_ACCESS_KEY_ID
    CLOUDFLARE_R2_SECRET_ACCESS_KEY
    CLOUDFLARE_R2_ENDPOINT
    R2_BUCKET (optional, default 'chip-tiles')
    R2_PREFIX (optional, default 'chip_top/v1/3d')

Run:
    uv run --with boto3 python3 tech/asap7/viz/upload_to_r2.py

Pipeline assumes the build artefacts already exist under
`tech/asap7/viz/out/` and `tech/asap7/viz/macros/`. Build them first per
the viz README — this script doesn't trigger any build.

Live viewer (after upload): https://gpu-pipitone-xyz.pages.dev/3d
(That Cloudflare Pages project proxies /3d/* into this R2 bucket prefix.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from botocore.client import Config

REPO = Path(__file__).resolve().parents[3]
VIZ  = REPO / "tech/asap7/viz"
ENV  = REPO / ".env"

# Headers.
CC_IMMUTABLE = "public, max-age=31536000, immutable"
CC_HTML      = "public, max-age=300, must-revalidate"

# What to upload, expressed as (local_path, key_under_prefix, content_type,
# cache_control). `None` for content_type lets boto3 infer.
ASSETS = [
    # The viewer itself (short-cache, revalidated on every visit).
    ("viewers/macros_instanced.html", "viewers/macros_instanced.html",
        "text/html; charset=utf-8", CC_HTML),
    # Macro placements (small, immutable per build).
    ("macros/placements.json",        "macros/placements.json",
        "application/json",            CC_IMMUTABLE),
    # Parent routing + parent-channel FEOL.
    ("out/base_routing.glb",          "out/base_routing.glb",
        "model/gltf-binary",           CC_IMMUTABLE),
    ("out/parent_feol_logic.glb",     "out/parent_feol_logic.glb",
        "model/gltf-binary",           CC_IMMUTABLE),
    # Per-macro routing meshes (one upload per macro type).
    ("out/macros/cmd_unit.glb",       "out/macros/cmd_unit.glb",
        "model/gltf-binary",           CC_IMMUTABLE),
    ("out/macros/mac_tmem_cell.glb",  "out/macros/mac_tmem_cell.glb",
        "model/gltf-binary",           CC_IMMUTABLE),
    ("out/macros/skew_lane_a.glb",    "out/macros/skew_lane_a.glb",
        "model/gltf-binary",           CC_IMMUTABLE),
    ("out/macros/skew_lane_b.glb",    "out/macros/skew_lane_b.glb",
        "model/gltf-binary",           CC_IMMUTABLE),
]

# Cell-FEOL tree: many small files, walk + upload. Per-macro:
# out/cellfeol/<macro_type>/<cell_type>.glb + instances.json
CELLFEOL_DIR = "out/cellfeol"


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        sys.exit(f"missing {path}")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def make_client(env: dict):
    needed = [
        "CLOUDFLARE_R2_ACCESS_KEY_ID",
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
        "CLOUDFLARE_R2_ENDPOINT",
    ]
    missing = [k for k in needed if not env.get(k)]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)}")
    return boto3.client(
        "s3",
        endpoint_url=env["CLOUDFLARE_R2_ENDPOINT"],
        aws_access_key_id=env["CLOUDFLARE_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["CLOUDFLARE_R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def needs_upload(s3, bucket: str, key: str, local_size: int) -> bool:
    """ETag-style skip: re-upload only if size differs or object missing."""
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except s3.exceptions.ClientError:
        return True
    return head.get("ContentLength") != local_size


def put(s3, bucket: str, key: str, local_path: Path,
        content_type: str | None, cache_control: str, *, skip_if_same_size: bool):
    size = local_path.stat().st_size
    if skip_if_same_size and not needs_upload(s3, bucket, key, size):
        print(f"  skip (same size) {key}  [{size:>10,} B]")
        return
    extra = {"CacheControl": cache_control}
    if content_type:
        extra["ContentType"] = content_type
    s3.upload_file(str(local_path), bucket, key, ExtraArgs=extra)
    print(f"  put           {key}  [{size:>10,} B]")


def upload_cellfeol(s3, bucket: str, prefix: str, cellfeol_root: Path):
    print(f"\n=== cellfeol/ tree (size-skip enabled) ===")
    if not cellfeol_root.exists():
        print(f"  (missing locally: {cellfeol_root}) — skipping")
        return
    n_put = n_skip = 0
    for path in sorted(cellfeol_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in (".glb", ".json"):
            continue
        rel = path.relative_to(cellfeol_root)
        key = f"{prefix}/{CELLFEOL_DIR}/{rel.as_posix()}"
        size = path.stat().st_size
        if not needs_upload(s3, bucket, key, size):
            n_skip += 1
            continue
        ctype = "model/gltf-binary" if path.suffix == ".glb" else "application/json"
        s3.upload_file(str(path), bucket, key,
                       ExtraArgs={"ContentType": ctype, "CacheControl": CC_IMMUTABLE})
        n_put += 1
    print(f"  {n_put} put, {n_skip} skipped (same size)")


def main():
    env = load_env(ENV)
    bucket = env.get("R2_BUCKET",  "chip-tiles")
    prefix = env.get("R2_PREFIX",  "chip_top/v1/3d")
    print(f"target: s3://{bucket}/{prefix}/  (via {env['CLOUDFLARE_R2_ENDPOINT']})")

    s3 = make_client(env)

    print("\n=== singletons ===")
    for rel, sub, ctype, cc in ASSETS:
        local = VIZ / rel
        if not local.exists():
            print(f"  MISSING locally: {rel}")
            continue
        key = f"{prefix}/{sub}"
        # HTML always re-uploads (cheap, short-cache); .glb skip when size matches.
        skip = ctype != "text/html; charset=utf-8"
        put(s3, bucket, key, local, ctype, cc, skip_if_same_size=skip)

    upload_cellfeol(s3, bucket, prefix, VIZ / CELLFEOL_DIR)

    print("\nDone. Live: https://gpu-pipitone-xyz.pages.dev/3d")


if __name__ == "__main__":
    main()
