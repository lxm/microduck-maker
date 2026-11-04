"""Recover exactly the two tested ARM64 WebRTC plugins from the kissqy public R1 image."""
import lzma
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

board = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(board/"scripts"))
from fetch_inputs import download, require_hash

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
download("https://huggingface.co/pollen-robotics/microduck-duck-detector/resolve/eeda8d200e4e91490d09b55c94648773406cc416/duck_detect.onnx",
         out/"duck_detect.onnx", "2e34657a655a15221f93f6149730875274da4fb1b1c67dc883cde769fec35c30")
download("https://deb.debian.org/debian/pool/main/v/v4l2loopback/v4l2loopback-dkms_0.15.0-2_all.deb",
         out/"loop.deb", "d9b768037e28852070d31af3376d90c8b448f9807de29d664587082bb7784cd4")
plugins = {"libgstrsrtp.so": "42b104ab8cb632c1a8332c83e5a0a1e14804e97d1baa12a8a36c5a97188c6954",
           "libgstrswebrtc.so": "44444e1a45bcee8f7109ce4323fa0391304c6a6b0de98a0530e439978fea293b"}
if not all((out/name).is_file() for name in plugins):
    archive = out/"microduck-zero3w-0.15.1-R1.img.xz"
    download("https://github.com/kissqy/Microduck-kissqy/releases/download/public-image-r1/"+archive.name,
             archive, "75da978b5d56acd9e4146256431732381de84523bb683e4ab7ac9f0580a7bce5")
    with tempfile.TemporaryDirectory(dir=out) as temporary:
        fs = Path(temporary)/"r1.ext4"
        with lzma.open(archive) as src, fs.open("wb") as dst:
            src.seek(32768*512)  # First ext4 partition in the pinned GPT image.
            shutil.copyfileobj(src, dst, 4<<20)
        for name, checksum in plugins.items():
            dest = fs.parent/name
            subprocess.run(["debugfs", "-R", f'dump /usr/local/lib/gstreamer-1.0/{name} "{dest}"', fs], check=True)
            require_hash(dest, checksum)
            dest.replace(out/name)
for name, checksum in plugins.items():
    require_hash(out/name, checksum)
