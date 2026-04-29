# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for CanLab — AI-powered CAN bus workstation."""

import sys
from pathlib import Path

ROOT = Path("canlab")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=["canlab"],
    binaries=[],
    datas=[
        (str(ROOT / "canlab.png"),             "."           ),
        (str(ROOT / "sample_data"),            "sample_data" ),
    ],
    hiddenimports=[
        # PyQt6
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui",
        "PyQt6.QtNetwork", "PyQt6.QtTest", "PyQt6.sip",
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
        "flask", "flask_cors",
        "isotp",
        "pydantic", "pydantic.v1", "pydantic_core",
        "anthropic", "anthropic.types", "anthropic._models",
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
        "PIL", "Pillow",
        "zmq", "tornado",
        "pygments", "jedi", "parso",
        "fastapi", "uvicorn", "starlette",
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
