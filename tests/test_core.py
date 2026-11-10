"""Testes unitários para o motor de conciliação bancária (core.py)."""
from __future__ import annotations

import copy
import unittest

from conciliacao_bancaria.core import (
    NATUREZAS_AJUSTE,
    conciliar,
    conciliar_json,
    format_brl,
    load_example_data,
    reconcile,
    reconcile_example,
    reconcile_json,
)
from conciliacao_bancaria.errors import ConciliacaoError
from conciliacao_bancaria.jsoncodec import dumps


def _cenario_base() -> dict:
    return {
        "schema_version": 1,
        "conta": {
            "id": "CONTA-01",
            "titulo": "Conta Teste Operacional",
            "moeda": "BRL",
            "banco": "Banco Teste (001)",
            "agencia": "0001",
            "numero_conta": "12345-6",
            "data_inicial": "2026-03-01",
            "data_final": "2026-03-31",
            "saldo_inicial_razao_centavos": 100000,
            "saldo_final_razao_centavos": 150000,
            "saldo_inicial_extrato_centavos": 100000,
            "saldo_final_extrato_centavos": 150000,
        },
        "parametros": {
            "janela_dias": 3,
            "max_agrupamento": 4,
            "orcamento_combinacoes": 1000,
        },
        "razao": [
            {
                "id": "R-1",
                "data": "2026-03-05",
                "historico": "Recebimento PIX",
                "documento": "DOC-1",
                "valor_centavos": 50000,
                "categoria": "RECEITA",
            }
        ],
        "extrato": [
            {
                "id": "E-1",
                "data": "2026-03-05",
                "descricao": "CREDITO PIX",
                "documento": "DOC-1",
                "valor_centavos": 50000,
                "tipo": "PIX",
            }
        ],
        "ajustes_declarados": [],
    }


class TestCoreReconcile(unittest.TestCase):
    def test_cenario_exemplo_oficial_completo(self) -> None:
        res = reconcile_example()
        self.assertEqual(res.resumo.total_lancamentos_razao, 9)
        self.assertEqual(res.resumo.total_lancamentos_extrato, 13)
        self.assertEqual(res.resumo.total_grupos_conciliados, 8)
        self.assertEqual(res.resumo.total_exatos_1_1, 4)
        self.assertEqual(res.resumo.total_lotes_1_n, 1)
        self.assertEqual(res.resumo.total_lotes_n_1, 1)
        self.assertEqual(res.resumo.total_ajustes_declarados, 2)
        self.assertEqual(res.resumo.total_grupos_ambiguos, 1)
        self.assertEqual(res.resumo.total_pendentes_razao, 1)
        self.assertEqual(res.resumo.total_pendentes_extrato, 1)

        qs = res.quadro_saldos
        self.assertTrue(qs.conservacao_exata)
        self.assertEqual(qs.diferenca_nao_explicada_centavos, 0)
        self.assertEqual(qs.saldo_razao_ajustado_centavos, 2900000)
        self.assertEqual(qs.saldo_extrato_conciliado_centavos, 2900000)
        self.assertEqual(qs.soma_conciliados_razao_centavos, 847500)
        self.assertEqual(qs.soma_conciliados_extrato_centavos, 847500)
        self.assertEqual(qs.soma_ajustes_declarados_centavos, -12500)

        amb = res.ambiguidades[0]
        self.assertEqual(amb.id, "AMB-001")
        self.assertEqual(amb.razao_ids, ("R-008",))
        self.assertEqual(amb.extrato_ids, ("E-011", "E-012"))
        self.assertEqual(len(amb.candidatos), 2)

    def test_casamento_1_1_documento_diferente_dentro_da_janela(self) -> None:
        doc = _cenario_base()
        doc["extrato"][0]["documento"] = "OUTRO-DOC"
        doc["extrato"][0]["data"] = "2026-03-08"
        res = reconcile(doc)
        self.assertEqual(len(res.grupos_conciliados), 1)
        g = res.grupos_conciliados[0]
        self.assertEqual(g.tipo, "EXATO_1_1")
        self.assertEqual(g.regra, "VALOR_EXATO_JANELA")
        self.assertIsNone(g.documento_comum)
        self.assertEqual(g.diferenca_dias_max, 3)

    def test_casamento_1_1_fora_da_janela_vira_pendente(self) -> None:
        doc = _cenario_base()
        doc["extrato"][0]["data"] = "2026-03-09"  # 4 dias de diferença (> janela 3)
        res = reconcile(doc)
        self.assertEqual(len(res.grupos_conciliados), 0)
        self.assertEqual(len(res.pendentes_razao), 1)
        self.assertEqual(len(res.pendentes_extrato), 1)
        self.assertTrue(res.quadro_saldos.conservacao_exata)

    def test_override_janela_dias_zero(self) -> None:
        doc = _cenario_base()
        doc["extrato"][0]["data"] = "2026-03-06"  # 1 dia de diferença
        res = reconcile(doc, janela_dias=0)
        self.assertEqual(len(res.grupos_conciliados), 0)
        self.assertEqual(res.resumo.total_pendentes_razao, 1)

    def test_lote_1_n_tres_extratos_para_um_razao(self) -> None:
        doc = _cenario_base()
        doc["razao"] = [
            {
                "id": "R-LOTE",
                "data": "2026-03-10",
                "historico": "Borderô 3 boletos",
                "documento": "B-10",
                "valor_centavos": 60000,
                "categoria": "RECEITA",
            }
        ]
        doc["extrato"] = [
            {"id": "E-1", "data": "2026-03-10", "descricao": "B1", "documento": "D1", "valor_centavos": 15000, "tipo": "BOLETO"},
            {"id": "E-2", "data": "2026-03-11", "descricao": "B2", "documento": "D2", "valor_centavos": 20000, "tipo": "BOLETO"},
            {"id": "E-3", "data": "2026-03-12", "descricao": "B3", "documento": "D3", "valor_centavos": 25000, "tipo": "BOLETO"},
        ]
        doc["conta"]["saldo_final_razao_centavos"] = 160000
        doc["conta"]["saldo_final_extrato_centavos"] = 160000
        res = reconcile(doc)
        self.assertEqual(len(res.grupos_conciliados), 1)
        g = res.grupos_conciliados[0]
        self.assertEqual(g.tipo, "LOTE_1_N")
        self.assertEqual(g.razao_ids, ("R-LOTE",))
        self.assertEqual(g.extrato_ids, ("E-1", "E-2", "E-3"))
        self.assertEqual(g.diferenca_dias_max, 2)

    def test_lote_n_1_dois_razao_para_um_extrato(self) -> None:
        doc = _cenario_base()
        doc["razao"] = [
            {"id": "R-1", "data": "2026-03-10", "historico": "NF 1", "documento": "N1", "valor_centavos": -30000, "categoria": "FORNECEDOR"},
            {"id": "R-2", "data": "2026-03-11", "historico": "NF 2", "documento": "N2", "valor_centavos": -45000, "categoria": "FORNECEDOR"},
        ]
        doc["extrato"] = [
            {"id": "E-1", "data": "2026-03-11", "descricao": "Remessa", "documento": "REM", "valor_centavos": -75000, "tipo": "TED"}
        ]
        doc["conta"]["saldo_final_razao_centavos"] = 25000
        doc["conta"]["saldo_final_extrato_centavos"] = 25000
        res = reconcile(doc)
        self.assertEqual(len(res.grupos_conciliados), 1)
        g = res.grupos_conciliados[0]
        self.assertEqual(g.tipo, "LOTE_N_1")
        self.assertEqual(g.razao_ids, ("R-1", "R-2"))
        self.assertEqual(g.extrato_ids, ("E-1",))

    def test_max_agrupamento_igual_a_1_desativa_lotes(self) -> None:
        doc = _cenario_base()
        doc["razao"] = [
            {"id": "R-1", "data": "2026-03-10", "historico": "NF 1", "documento": "N1", "valor_centavos": -30000, "categoria": "FORNECEDOR"},
            {"id": "R-2", "data": "2026-03-10", "historico": "NF 2", "documento": "N2", "valor_centavos": -45000, "categoria": "FORNECEDOR"},
        ]
        doc["extrato"] = [
            {"id": "E-1", "data": "2026-03-10", "descricao": "Remessa", "documento": "REM", "valor_centavos": -75000, "tipo": "TED"}
        ]
        doc["conta"]["saldo_final_razao_centavos"] = 25000
        doc["conta"]["saldo_final_extrato_centavos"] = 25000
        res = reconcile(doc, max_agrupamento=1)
        self.assertEqual(len(res.grupos_conciliados), 0)
        self.assertEqual(res.resumo.total_pendentes_razao, 2)
        self.assertEqual(res.resumo.total_pendentes_extrato, 1)

    def test_preserva_ambiguidade_1_1_dois_razao_para_um_extrato(self) -> None:
        doc = _cenario_base()
        doc["razao"] = [
            {"id": "R-1", "data": "2026-03-10", "historico": "Pagto A", "documento": "D1", "valor_centavos": -20000, "categoria": "GERAL"},
            {"id": "R-2", "data": "2026-03-11", "historico": "Pagto B", "documento": "D2", "valor_centavos": -20000, "categoria": "GERAL"},
        ]
        doc["extrato"] = [
            {"id": "E-1", "data": "2026-03-11", "descricao": "PIX ENVIADO", "documento": "P1", "valor_centavos": -20000, "tipo": "PIX"}
        ]
        doc["conta"]["saldo_final_razao_centavos"] = 60000
        doc["conta"]["saldo_final_extrato_centavos"] = 80000
        res = reconcile(doc)
        self.assertEqual(len(res.grupos_conciliados), 0)
        self.assertEqual(len(res.ambiguidades), 1)
        amb = res.ambiguidades[0]
        self.assertEqual(amb.estagio, "EXATO_1_1")
        self.assertEqual(amb.razao_ids, ("R-1", "R-2"))
        self.assertEqual(amb.extrato_ids, ("E-1",))
        self.assertTrue(res.quadro_saldos.conservacao_exata)

    def test_preserva_ambiguidade_subconjunto_1_n_mesma_soma(self) -> None:
        doc = _cenario_base()
        doc["razao"] = [
            {"id": "R-1", "data": "2026-03-10", "historico": "Lote", "documento": "L1", "valor_centavos": 30000, "categoria": "RECEITA"}
        ]
        doc["extrato"] = [
            {"id": "E-1", "data": "2026-03-10", "descricao": "C1", "documento": "D1", "valor_centavos": 10000, "tipo": "PIX"},
            {"id": "E-2", "data": "2026-03-10", "descricao": "C2", "documento": "D2", "valor_centavos": 20000, "tipo": "PIX"},
            {"id": "E-3", "data": "2026-03-11", "descricao": "C3", "documento": "D3", "valor_centavos": 20000, "tipo": "PIX"},
        ]
        doc["conta"]["saldo_final_razao_centavos"] = 130000
        doc["conta"]["saldo_final_extrato_centavos"] = 150000
        res = reconcile(doc)
        self.assertEqual(len(res.grupos_conciliados), 0)
        self.assertEqual(len(res.ambiguidades), 1)
        amb = res.ambiguidades[0]
        self.assertEqual(amb.estagio, "AGRUPAMENTO_SUBCONJUNTOS")
        self.assertEqual(amb.razao_ids, ("R-1",))
        self.assertEqual(amb.extrato_ids, ("E-1", "E-2", "E-3"))
        self.assertEqual(len(amb.candidatos), 2)
        self.assertTrue(res.quadro_saldos.conservacao_exata)

    def test_orcamento_combinacoes_excedido_levanta_erro(self) -> None:
        doc = _cenario_base()
        doc["razao"] = [
            {"id": "R-1", "data": "2026-03-10", "historico": "Alvo", "documento": "D", "valor_centavos": 99999, "categoria": "RECEITA"}
        ]
        doc["extrato"] = [
            {"id": f"E-{i}", "data": "2026-03-10", "descricao": f"Item {i}", "documento": f"D{i}", "valor_centavos": 1000 * i, "tipo": "PIX"}
            for i in range(1, 8)
        ]
        doc["conta"]["saldo_final_razao_centavos"] = 100000 + 99999
        doc["conta"]["saldo_final_extrato_centavos"] = 100000 + sum(1000 * i for i in range(1, 8))
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc, orcamento_combinacoes=5)
        self.assertEqual(ctx.exception.code, "ORCAMENTO_COMBINACOES_EXCEDIDO")
        self.assertEqual(ctx.exception.detalhes["orcamento_combinacoes"], 5)

    def test_todas_as_naturezas_de_ajuste_declarado(self) -> None:
        for natureza in NATUREZAS_AJUSTE:
            with self.subTest(natureza=natureza):
                doc = _cenario_base()
                doc["razao"] = []
                doc["extrato"] = [
                    {"id": "E-AJ", "data": "2026-03-15", "descricao": "Ajuste", "documento": "DAJ", "valor_centavos": -1500, "tipo": "TARIFA"}
                ]
                doc["ajustes_declarados"] = [
                    {
                        "id": "AJ-1",
                        "extrato_id": "E-AJ",
                        "natureza": natureza,
                        "conta_contabil": "4.1.3.01-Despesas",
                        "justificativa": "Ajuste contratual.",
                    }
                ]
                doc["conta"]["saldo_final_razao_centavos"] = 100000
                doc["conta"]["saldo_final_extrato_centavos"] = 98500
                res = reconcile(doc)
                self.assertEqual(res.resumo.total_ajustes_declarados, 1)
                self.assertEqual(res.quadro_saldos.saldo_razao_ajustado_centavos, 98500)

    def test_rejeita_versao_esquema_invalida(self) -> None:
        doc = _cenario_base()
        doc["schema_version"] = 2
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "VERSAO_ESQUEMA_INVALIDA")

    def test_rejeita_campo_desconhecido(self) -> None:
        doc = _cenario_base()
        doc["campo_extra"] = 123
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "CAMPO_DESCONHECIDO")

    def test_rejeita_saldo_razao_inconsistente(self) -> None:
        doc = _cenario_base()
        doc["conta"]["saldo_final_razao_centavos"] = 999999
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "SALDO_INCONSISTENTE")

    def test_rejeita_saldo_extrato_inconsistente(self) -> None:
        doc = _cenario_base()
        doc["conta"]["saldo_final_extrato_centavos"] = 999999
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "SALDO_INCONSISTENTE")

    def test_rejeita_valor_zero_no_razao_ou_extrato(self) -> None:
        doc = _cenario_base()
        doc["razao"][0]["valor_centavos"] = 0
        doc["conta"]["saldo_final_razao_centavos"] = 100000
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "VALOR_ZERO_INVALIDO")

    def test_rejeita_id_duplicado_entre_razao_e_extrato(self) -> None:
        doc = _cenario_base()
        doc["extrato"][0]["id"] = "R-1"
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "ID_DUPLICADO")

    def test_rejeita_referencia_inexistente_em_ajuste(self) -> None:
        doc = _cenario_base()
        doc["ajustes_declarados"] = [
            {
                "id": "AJ-1",
                "extrato_id": "E-INEXISTENTE",
                "natureza": "TARIFA",
                "conta_contabil": "4.1.3",
                "justificativa": "Teste",
            }
        ]
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "REFERENCIA_INEXISTENTE")

    def test_rejeita_conflito_dois_ajustes_mesmo_extrato(self) -> None:
        doc = _cenario_base()
        doc["ajustes_declarados"] = [
            {"id": "AJ-1", "extrato_id": "E-1", "natureza": "TARIFA", "conta_contabil": "4.1", "justificativa": "J1"},
            {"id": "AJ-2", "extrato_id": "E-1", "natureza": "IOF", "conta_contabil": "4.2", "justificativa": "J2"},
        ]
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "CONFLITO_AJUSTE")

    def test_rejeita_natureza_ajuste_invalida(self) -> None:
        doc = _cenario_base()
        doc["ajustes_declarados"] = [
            {"id": "AJ-1", "extrato_id": "E-1", "natureza": "DESCONHECIDA", "conta_contabil": "4.1", "justificativa": "J1"}
        ]
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "AJUSTE_INVALIDO")

    def test_rejeita_datas_invalidas_ou_fora_do_periodo(self) -> None:
        doc1 = _cenario_base()
        doc1["razao"][0]["data"] = "2026-02-28"
        with self.assertRaises(ConciliacaoError) as ctx1:
            reconcile(doc1)
        self.assertEqual(ctx1.exception.code, "DATA_INVALIDA")

        doc2 = _cenario_base()
        doc2["extrato"][0]["data"] = "2026-02-30"
        with self.assertRaises(ConciliacaoError) as ctx2:
            reconcile(doc2)
        self.assertEqual(ctx2.exception.code, "DATA_INVALIDA")

        doc3 = _cenario_base()
        doc3["conta"]["data_inicial"] = "2026-04-01"
        with self.assertRaises(ConciliacaoError) as ctx3:
            reconcile(doc3)
        self.assertEqual(ctx3.exception.code, "DATA_INVALIDA")

    def test_rejeita_booleano_como_inteiro(self) -> None:
        doc = _cenario_base()
        doc["parametros"]["janela_dias"] = True
        with self.assertRaises(ConciliacaoError) as ctx:
            reconcile(doc)
        self.assertEqual(ctx.exception.code, "ESQUEMA_INVALIDO")

    def test_rejeita_moeda_invalida_e_texto_com_controle(self) -> None:
        doc1 = _cenario_base()
        doc1["conta"]["moeda"] = "brl"
        with self.assertRaises(ConciliacaoError) as ctx1:
            reconcile(doc1)
        self.assertEqual(ctx1.exception.code, "ESQUEMA_INVALIDO")

        doc2 = _cenario_base()
        doc2["razao"][0]["historico"] = "Texto\x07Inválido"
        with self.assertRaises(ConciliacaoError) as ctx2:
            reconcile(doc2)
        self.assertEqual(ctx2.exception.code, "ESQUEMA_INVALIDO")

    def test_format_brl_e_aliases_portugues(self) -> None:
        self.assertEqual(format_brl(0), "R$ 0,00")
        self.assertEqual(format_brl(2912500), "R$ 29.125,00")
        self.assertEqual(format_brl(-8900), "-R$ 89,00")
        self.assertEqual(format_brl(123456789), "R$ 1.234.567,89")

        dados = load_example_data()
        r1 = conciliar(dados)
        r2 = conciliar_json(dumps(dados))
        r3 = reconcile_json(dumps(dados).encode("utf-8"))
        self.assertEqual(r1.to_dict(), r2.to_dict())
        self.assertEqual(r1.to_dict(), r3.to_dict())

    def test_source_document_retorna_copia_independente(self) -> None:
        doc = _cenario_base()
        original = copy.deepcopy(doc)
        res = reconcile(doc)
        copia = res.source_document()
        copia["conta"]["titulo"] = "Alterado"
        self.assertEqual(res.source_document(), original)


if __name__ == "__main__":
    unittest.main()
