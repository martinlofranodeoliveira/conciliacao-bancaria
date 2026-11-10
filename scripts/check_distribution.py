#!/usr/bin/env python3
"""Verifica integridade de wheel (.whl), sdist (.tar.gz), RECORD PEP 427 e instalação isolada."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile

FORBIDDEN_FRAGMENTS = (
    "local/",
    ".openai",
    ".impeccable",
    ".venv",
    "__pycache__",
    ".pyc",
    ".pyo",
    ".env",
    "CONTRACT.md",
)


def _urlsafe_sha256(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def check_wheel(wheel_path: Path) -> None:
    with zipfile.ZipFile(wheel_path, "r") as zf:
        names = zf.namelist()
        for name in names:
            for frag in FORBIDDEN_FRAGMENTS:
                if frag in name:
                    raise AssertionError(f"Artefato proibido encontrado na wheel: {name}")

        required_wheel = {
            "conciliacao_bancaria/__init__.py",
            "conciliacao_bancaria/__main__.py",
            "conciliacao_bancaria/cli.py",
            "conciliacao_bancaria/core.py",
            "conciliacao_bancaria/errors.py",
            "conciliacao_bancaria/jsoncodec.py",
            "conciliacao_bancaria/reports.py",
            "conciliacao_bancaria/data/exemplo.json",
        }
        missing = required_wheel - set(names)
        if missing:
            raise AssertionError(f"Arquivos obrigatórios ausentes na wheel: {sorted(missing)}")

        record_names = [n for n in names if n.endswith(".dist-info/RECORD")]
        if len(record_names) != 1:
            raise AssertionError(f"Esperado exatamente 1 RECORD na wheel, encontrado: {record_names}")
        record_name = record_names[0]
        record_text = zf.read(record_name).decode("utf-8", "strict")
        rows = list(csv.reader(io.StringIO(record_text)))
        recorded_files: set[str] = set()
        for row in rows:
            if not row:
                continue
            fname, hash_spec, size_spec = row
            recorded_files.add(fname)
            if fname == record_name:
                continue
            raw = zf.read(fname)
            if not hash_spec.startswith("sha256="):
                raise AssertionError(f"Hash RECORD inválido para {fname}: {hash_spec}")
            expected_hash = hash_spec.split("=", 1)[1]
            actual_hash = _urlsafe_sha256(raw)
            if actual_hash != expected_hash:
                raise AssertionError(f"Hash RECORD divergente para {fname}: {actual_hash} != {expected_hash}")
            if int(size_spec) != len(raw):
                raise AssertionError(f"Tamanho RECORD divergente para {fname}: {len(raw)} != {size_spec}")

        if recorded_files != set(names):
            raise AssertionError("RECORD não cobre exatamente todos os arquivos da wheel.")


def check_sdist(sdist_path: Path) -> None:
    with tarfile.open(sdist_path, "r:gz") as tf:
        names = tf.getnames()
        for name in names:
            for frag in FORBIDDEN_FRAGMENTS:
                if frag in name:
                    raise AssertionError(f"Artefato proibido encontrado na sdist: {name}")

        required_suffixes = (
            "/README.md",
            "/LICENSE",
            "/pyproject.toml",
            "/MANIFEST.in",
            "/src/conciliacao_bancaria/core.py",
            "/src/conciliacao_bancaria/data/exemplo.json",
            "/tools/verify_independent.py",
            "/scripts/check_distribution.py",
            "/docs/exemplo.png",
            "/docs/FORMATO.md",
            "/docs/API.md",
            "/docs/VERIFICACAO.md",
            "/docs/INTEROPERABILIDADE.md",
        )
        for suffix in required_suffixes:
            if not any(n.endswith(suffix) for n in names):
                raise AssertionError(f"Arquivo esperado ausente na sdist: *{suffix}")


def check_isolated_install(wheel_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="conciliacao-venv-") as tmpdir:
        tmp = Path(tmpdir)
        venv_dir = tmp / "env"
        work_dir = tmp / "work"
        work_dir.mkdir()

        venv.create(venv_dir, with_pip=True)
        bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        py_bin = bin_dir / ("python.exe" if os.name == "nt" else "python")
        cli_bin = bin_dir / ("conciliacao-bancaria.exe" if os.name == "nt" else "conciliacao-bancaria")

        subprocess.run(
            [str(py_bin), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel_path)],
            cwd=work_dir,
            check=True,
            capture_output=True,
            text=True,
        )

        zip_out = work_dir / "pacote-instalado.zip"
        res_ex = subprocess.run(
            [str(cli_bin), "exemplo", "--saida", str(zip_out)],
            cwd=work_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(res_ex.stdout)
        if payload.get("status") != "OK" or not payload.get("conservacao_exata"):
            raise AssertionError(f"Saída inesperada da CLI instalada: {payload}")

        res_ver = subprocess.run(
            [str(cli_bin), "verificar", str(zip_out)],
            cwd=work_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        ver_payload = json.loads(res_ver.stdout)
        if ver_payload.get("arquivos_verificados") != 7:
            raise AssertionError(f"Verificação da CLI instalada falhou: {ver_payload}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita artefatos de distribuição em dist/.")
    parser.add_argument("--dist", default="dist", help="Diretório contendo .whl e .tar.gz")
    args = parser.parse_args()

    dist_dir = Path(args.dist).resolve()
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise AssertionError(
            f"Esperados exatamente 1 .whl e 1 .tar.gz em {dist_dir}; encontrados: {wheels}, {sdists}"
        )

    check_wheel(wheels[0])
    check_sdist(sdists[0])
    check_isolated_install(wheels[0])

    print(
        json.dumps(
            {
                "status": "OK",
                "wheel": wheels[0].name,
                "sdist": sdists[0].name,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
