"""Testes unitários para geração de relatórios, SVG, CSV e pacote ZIP (reports.py)."""
from __future__ import annotations

import csv
import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from conciliacao_bancaria.core import reconcile, reconcile_example
from conciliacao_bancaria.errors import ConciliacaoError
from conciliacao_bancaria.reports import (
    BUNDLE_ALL_FILES,
    BUNDLE_DATA_FILES,
    build_bundle_files,
    build_bundle_zip_bytes,
    publish_file_bytes,
    render_conciliados_csv,
    render_lancamentos_csv,
    render_markdown_report,
    render_pendencias_csv,
    render_svg_panel,
    sanitize_csv_text,
    verify_bundle_zip,
    write_bundle_directory,
    write_bundle_zip,
)


class TestReports(unittest.TestCase):
    def test_sanitize_csv_text_protege_contra_formula_injection(self) -> None:
        self.assertEqual(sanitize_csv_text("=SUM(A1:A5)"), "'=SUM(A1:A5)")
        self.assertEqual(sanitize_csv_text("+1+2"), "'+1+2")
        self.assertEqual(sanitize_csv_text("-cmd|' /C calc'!A0"), "'-cmd|' /C calc'!A0")
        self.assertEqual(sanitize_csv_text("@IMPORTXML()"), "'@IMPORTXML()")
        self.assertEqual(sanitize_csv_text("   =EVIL()"), "'   =EVIL()")
        self.assertEqual(sanitize_csv_text("\t=TAB"), "'\t=TAB")
        self.assertEqual(sanitize_csv_text("Texto normal"), "Texto normal")
        self.assertEqual(sanitize_csv_text(""), "")
        self.assertEqual(sanitize_csv_text(None), "")

    def test_csv_conciliados_estrutura_e_linhas(self) -> None:
        res = reconcile_example()
        texto = render_conciliados_csv(res)
        self.assertNotIn("\r\n", texto)
        linhas = list(csv.DictReader(io.StringIO(texto)))
        self.assertEqual(len(linhas), 8)
        self.assertEqual(linhas[0]["grupo_id"], "CONC-001")
        self.assertEqual(linhas[0]["tipo"], "EXATO_1_1")
        self.assertEqual(int(linhas[0]["valor_razao_centavos"]), 680000)

    def test_csv_pendencias_inclui_ambiguos_e_pendentes(self) -> None:
        res = reconcile_example()
        texto = render_pendencias_csv(res)
        linhas = list(csv.DictReader(io.StringIO(texto)))
        # 3 lançamentos em AMB-001 (R-008, E-011, E-012) + 2 pendentes (R-009, E-013) = 5 linhas
        self.assertEqual(len(linhas), 5)
        categorias = [row["categoria_pendencia"] for row in linhas]
        self.assertEqual(categorias.count("AMBIGUO"), 3)
        self.assertEqual(categorias.count("PENDENTE_RAZAO"), 1)
        self.assertEqual(categorias.count("PENDENTE_EXTRATO"), 1)

    def test_csv_lancamentos_cobre_todos_os_itens(self) -> None:
        res = reconcile_example()
        texto = render_lancamentos_csv(res)
        linhas = list(csv.DictReader(io.StringIO(texto)))
        self.assertEqual(len(linhas), 22)

    def test_csv_neutraliza_formulas_em_descricoes_e_documentos(self) -> None:
        doc = {
            "schema_version": 1,
            "conta": {
                "id": "CONTA-SEC",
                "titulo": "Conta | Segurança",
                "moeda": "BRL",
                "banco": "Banco",
                "agencia": "1",
                "numero_conta": "1",
                "data_inicial": "2026-03-01",
                "data_final": "2026-03-31",
                "saldo_inicial_razao_centavos": 0,
                "saldo_final_razao_centavos": 1000,
                "saldo_inicial_extrato_centavos": 0,
                "saldo_final_extrato_centavos": 1000,
            },
            "razao": [
                {
                    "id": "R-1",
                    "data": "2026-03-02",
                    "historico": "=CMD()",
                    "documento": "+DOC",
                    "valor_centavos": 1000,
                    "categoria": "@CAT",
                }
            ],
            "extrato": [
                {
                    "id": "E-1",
                    "data": "2026-03-20",
                    "descricao": "-EXEC()",
                    "documento": "=EXT",
                    "valor_centavos": 1000,
                    "tipo": "PIX",
                }
            ],
        }
        res = reconcile(doc)
        pend_csv = list(csv.DictReader(io.StringIO(render_pendencias_csv(res))))
        self.assertTrue(pend_csv[0]["descricao"].startswith("'="))
        self.assertTrue(pend_csv[0]["documento"].startswith("'+"))
        self.assertTrue(pend_csv[1]["descricao"].startswith("'-"))

    def test_markdown_report_completo_e_escapa_pipes(self) -> None:
        res = reconcile_example()
        md = render_markdown_report(res)
        self.assertIn("# Relatório de Conciliação Bancária", md)
        self.assertIn("## 2. Quadro de Conciliação de Saldos", md)
        self.assertIn("## 5. Ambiguidades Preservadas", md)
        self.assertIn("`AMB-001`", md)
        self.assertIn("R$ 29.000,00", md)

    def test_svg_panel_xml_valido_e_deterministico(self) -> None:
        res = reconcile_example()
        svg1 = render_svg_panel(res)
        svg2 = render_svg_panel(res)
        self.assertEqual(svg1, svg2)
        root = ET.fromstring(svg1)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.attrib["width"], "1280")
        self.assertEqual(root.attrib["height"], "860")

    def test_build_bundle_files_e_sha256sums(self) -> None:
        res = reconcile_example()
        arquivos = build_bundle_files(res)
        self.assertEqual(set(arquivos.keys()), set(BUNDLE_ALL_FILES))
        linhas_sha = arquivos["SHA256SUMS"].decode("utf-8").strip().splitlines()
        self.assertEqual(len(linhas_sha), len(BUNDLE_DATA_FILES))

    def test_zip_deterministico_e_verificavel(self) -> None:
        res = reconcile_example()
        b1 = build_bundle_zip_bytes(res)
        b2 = build_bundle_zip_bytes(res)
        self.assertEqual(b1, b2)

        with tempfile.TemporaryDirectory() as tmp:
            zpath = write_bundle_zip(res, Path(tmp) / "pacote.zip")
            hashes = verify_bundle_zip(zpath)
            self.assertEqual(len(hashes), 7)

    def test_verify_bundle_zip_detecta_adulteracao(self) -> None:
        res = reconcile_example()
        with tempfile.TemporaryDirectory() as tmp:
            zorig = write_bundle_zip(res, Path(tmp) / "orig.zip")
            zbad = Path(tmp) / "adulterado.zip"
            with zipfile.ZipFile(zorig, "r") as zin, zipfile.ZipFile(zbad, "w") as zout:
                for item in zin.infolist():
                    dados = zin.read(item.filename)
                    if item.filename == "RELATORIO.md":
                        dados += b"\nADULTERADO\n"
                    zout.writestr(item, dados)

            with self.assertRaises(ConciliacaoError) as ctx:
                verify_bundle_zip(zbad)
            self.assertEqual(ctx.exception.code, "ZIP_INVALIDO")

    def test_verify_bundle_zip_rejeita_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zbad = Path(tmp) / "traversal.zip"
            with zipfile.ZipFile(zbad, "w") as zout:
                zout.writestr("../escape.txt", b"x")
            with self.assertRaises(ConciliacaoError) as ctx:
                verify_bundle_zip(zbad)
            self.assertEqual(ctx.exception.code, "ZIP_INVALIDO")

    def test_publish_file_bytes_recusa_sobrescrever_existente(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alvo = Path(tmp) / "saida.json"
            publish_file_bytes(alvo, b'{"ok": 1}\n')
            with self.assertRaises(ConciliacaoError) as ctx:
                publish_file_bytes(alvo, b'{"ok": 2}\n')
            self.assertEqual(ctx.exception.code, "SAIDA_EXISTENTE")
            self.assertEqual(alvo.read_bytes(), b'{"ok": 1}\n')

    def test_write_bundle_directory_cria_e_recusa_sobrescrever(self) -> None:
        res = reconcile_example()
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp) / "pacote-dir"
            write_bundle_directory(res, pasta)
            for nome in BUNDLE_ALL_FILES:
                self.assertTrue((pasta / nome).is_file())
            with self.assertRaises(ConciliacaoError) as ctx:
                write_bundle_directory(res, pasta)
            self.assertEqual(ctx.exception.code, "SAIDA_EXISTENTE")

    def test_rejeita_caminho_vazio_nulo_ou_pai_inexistente(self) -> None:
        for invalido in ("", "   ", "abc\x00def", "/caminho/nao/existe/saida.zip"):
            with self.subTest(invalido=invalido):
                with self.assertRaises(ConciliacaoError) as ctx:
                    publish_file_bytes(invalido, b"teste")
                self.assertEqual(ctx.exception.code, "CAMINHO_INVALIDO")

    def test_verify_bundle_zip_arquivo_inexistente(self) -> None:
        with self.assertRaises(ConciliacaoError) as ctx:
            verify_bundle_zip("/tmp/nao-existe-123456789.zip")
        self.assertEqual(ctx.exception.code, "ZIP_INVALIDO")


if __name__ == "__main__":
    unittest.main()
