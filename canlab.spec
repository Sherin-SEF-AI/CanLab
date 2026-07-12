# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for CanLab — AI-powered CAN bus workstation."""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path("canlab")

# Collect PIL (Pillow) including its native C extensions and bundled shared libs.
# Simply listing PIL in hiddenimports is not enough — PyInstaller won't pick up
# _imaging.so or the manylinux-bundled libtiff/libjpeg/etc. without collect_all.
pil_datas, pil_binaries, pil_hiddenimports = collect_all("PIL")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=["canlab"],
    binaries=pil_binaries,
    datas=[
        (str(ROOT / "canlab.png"),             "."           ),
        (str(ROOT / "sample_data"),            "sample_data" ),
        *pil_datas,
    ],
    hiddenimports=[
        # PyQt6
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui",
        "PyQt6.QtNetwork", "PyQt6.QtTest", "PyQt6.sip",
        # QtMultimedia — used by the TIMELINE tab's video sync. TimelineTab is
        # imported at startup, so omitting these crashes the bundled binary.
        "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets",
        # cantools / can
        "cantools", "cantools.database", "cantools.database.can",
        "can", "can.interfaces", "can.interfaces.socketcan",
        "can.interfaces.pcan", "can.interfaces.kvaser",
        "can.interfaces.virtual", "can.interfaces.usb2can",
        "can.interfaces.serial",
        # data / ML
        "pandas", "numpy", "scipy", "scipy.stats", "scipy.signal",
        "scipy.sparse", "scipy._lib", "scipy._lib._array_api",
        "scipy._lib.array_api_compat", "scipy._lib.array_api_compat.numpy",
        "numpy.testing",
        "sklearn", "sklearn.ensemble", "sklearn.preprocessing",
        "sklearn.neighbors",
        "matplotlib", "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_agg",
        # AI
        "groq", "anthropic",
        # stdlib extras
        "xml.etree.ElementTree", "json", "csv", "pathlib",
        "hashlib", "struct", "threading", "queue",
        # misc
        "dpkt", "dpkt.pcap", "dpkt.pcapng",
        "keyring", "keyring.backends",
        "requests", "urllib3",
        "fastapi", "fastapi.responses",
        "uvicorn", "uvicorn.main", "uvicorn.config",
        "starlette", "starlette.routing", "starlette.responses",
        "isotp",
        "pydantic", "pydantic.v1", "pydantic_core",
        "anthropic", "anthropic.types", "anthropic._models",
        *pil_hiddenimports,
    ],
    excludes=[
        "tkinter",
        "IPython", "jupyter",
        # Bloat — not used by CanLab
        "torch", "torchvision", "torchaudio",
        "nvidia", "triton",
        "onnxruntime", "onnx",
        "cv2", "opencv",
        "transformers", "tokenizers", "huggingface_hub",
        "pyarrow", "pyarrow.lib",
        "llvmlite", "numba",
        "tensorflow", "keras",
        "zmq", "tornado",
        "pygments", "jedi", "parso",
        "flask",
        "boto3", "botocore", "s3transfer",
        "google", "grpc",
        "sqlalchemy", "alembic",
        "aiohttp", "aiofiles",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CanLab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon="canlab/canlab.png",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CanLab",
)
