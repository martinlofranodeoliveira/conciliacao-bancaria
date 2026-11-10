"""Testes unitários e de integração para a CLI (cli.py) e verificador independente."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from conciliacao_bancaria.cli import main
from conciliacao_bancaria.jsoncodec import loads


class TestCLI(unittest.TestCase):
    def _run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_cli_exemplo_json_e_conciliar_para_zip_e_verificar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrada_path = Path(tmp) / "entrada.json"
            zip_path = Path(tmp) / "saida.zip"

            c1, out1, err1 = self._run_cli(["exemplo", "--saida", str(entrada_path)])
            self.assertEqual(c1, 0, err1)
            self.assertTrue(entrada_path.is_file())
            self.assertEqual(loads(out1)["formato"], "json")

            c2, out2, err2 = self._run_cli(["conciliar", str(entrada_path), "--saida", str(zip_path)])
            self.assertEqual(c2, 0, err2)
            self.assertTrue(zip_path.is_file())
            payload2 = loads(out2)
            self.assertTrue(payload2["conservacao_exata"])
            self.assertEqual(payload2["resumo"]["total_grupos_conciliados"], 8)

            c3, out3, err3 = self._run_cli(["verificar", str(zip_path)])
            self.assertEqual(c3, 0, err3)
            self.assertEqual(loads(out3)["arquivos_verificados"], 7)

    def test_cli_exemplo_exporta_zip_svg_md_e_diretorio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p_zip = Path(tmp) / "ex.zip"
            p_svg = Path(tmp) / "ex.svg"
            p_md = Path(tmp) / "ex.md"
            p_dir = Path(tmp) / "ex_dir"

            self.assertEqual(self._run_cli(["exemplo", "--saida", str(p_zip)])[0], 0)
            self.assertEqual(self._run_cli(["exemplo", "--saida", str(p_svg)])[0], 0)
            self.assertEqual(self._run_cli(["exemplo", "--saida", str(p_md)])[0], 0)
            self.assertEqual(self._run_cli(["exemplo", "--saida", str(p_dir)])[0], 0)

            self.assertTrue(p_zip.is_file())
            self.assertTrue(p_svg.is_file())
            self.assertTrue(p_md.is_file())
            self.assertTrue((p_dir / "resultado.json").is_file())

    def test_cli_conciliar_exporta_json_svg_md_e_diretorio_com_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrada = Path(tmp) / "in.json"
            self._run_cli(["exemplo", "--saida", str(entrada)])

            out_json = Path(tmp) / "res.json"
            out_svg = Path(tmp) / "res.svg"
            out_md = Path(tmp) / "res.md"
            out_dir = Path(tmp) / "res_dir"

            code_j, _, err_j = self._run_cli(
                [
                    "conciliar",
                    str(entrada),
                    "--saida",
                    str(out_json),
                    "--janela-dias",
                    "1",
                    "--max-agrupamento",
                    "3",
                    "--orcamento-combinacoes",
                    "500",
                ]
            )
            self.assertEqual(code_j, 0, err_j)
            res_doc = loads(out_json.read_bytes())
            self.assertEqual(res_doc["parametros"]["janela_dias"], 1)
            self.assertEqual(res_doc["parametros"]["max_agrupamento"], 3)

            self.assertEqual(self._run_cli(["conciliar", str(entrada), "--saida", str(out_svg)])[0], 0)
            self.assertEqual(self._run_cli(["conciliar", str(entrada), "--saida", str(out_md)])[0], 0)
            self.assertEqual(self._run_cli(["conciliar", str(entrada), "--saida", str(out_dir)])[0], 0)

    def test_cli_sem_subcomando_retorna_codigo_2(self) -> None:
        code, _out, err = self._run_cli([])
        self.assertEqual(code, 2)
        self.assertIn("[CLI_ARGUMENTO_INVALIDO]", err)

    def test_cli_argumento_desconhecido_retorna_codigo_2(self) -> None:
        code, _out, err = self._run_cli(["comando-invalido"])
        self.assertEqual(code, 2)
        self.assertIn("[CLI_ARGUMENTO_INVALIDO]", err)

    def test_cli_arquivo_entrada_inexistente_retorna_codigo_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, err = self._run_cli(
                ["conciliar", str(Path(tmp) / "nao-existe.json"), "--saida", str(Path(tmp) / "out.zip")]
            )
            self.assertEqual(code, 2)
            self.assertIn("[ARQUIVO_NAO_ENCONTRADO]", err)

    def test_cli_recusa_sobrescrever_saida_existente_retornando_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alvo = Path(tmp) / "existente.json"
            alvo.write_text('{"preservado": 1}\n', encoding="utf-8")
            code, _out, err = self._run_cli(["exemplo", "--saida", str(alvo)])
            self.assertEqual(code, 2)
            self.assertIn("[SAIDA_EXISTENTE]", err)
            self.assertEqual(alvo.read_text(encoding="utf-8"), '{"preservado": 1}\n')

    def test_cli_entrada_com_float_retorna_codigo_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            invalido = Path(tmp) / "float.json"
            invalido.write_text('{"schema_version": 1, "taxa": 1.5}\n', encoding="utf-8")
            code, _out, err = self._run_cli(
                ["conciliar", str(invalido), "--saida", str(Path(tmp) / "out.zip")]
            )
            self.assertEqual(code, 2)
            self.assertIn("[JSON_FLOAT]", err)

    def test_modulo_main_version_via_subprocess(self) -> None:
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "src")
        proc = subprocess.run(
            [sys.executable, "-m", "conciliacao_bancaria", "--version"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("conciliacao-bancaria 1.0.0", proc.stdout)

    def test_verificador_independente_executa_com_sucesso(self) -> None:
        root = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, str(root / "tools" / "verify_independent.py")],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        dados = json.loads(proc.stdout)
        self.assertEqual(dados["status"], "OK")
        self.assertEqual(dados["saldo_conciliado_brl"], "29000.00")


if __name__ == "__main__":
    unittest.main()
