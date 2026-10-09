"""Compatibility shim for older pip versions that lack modern editable installs."""

from setuptools import find_packages, setup

setup(
    name="pdf2md-local",
    version="0.1.0",
    description="Convert PDF files to Markdown and keep embedded images in reading order.",
    packages=find_packages(include=("pdf2md", "pdf2md.*", "watermark_remover", "watermark_remover.*")),
    python_requires=">=3.10",
    install_requires=[
        "PyMuPDF>=1.24",
        "numpy>=1.24",
        "Pillow>=10",
        "fastapi>=0.110",
        "python-multipart>=0.0.9",
        "uvicorn>=0.29",
    ],
    entry_points={
        "console_scripts": [
            "pdf2md=pdf2md.cli:main",
            "pdf2md-web=pdf2md.web:main",
            "pdf-watermark-web=watermark_remover.web:main",
        ]
    },
)
