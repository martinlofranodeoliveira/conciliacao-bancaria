"""Testes unitários para o codec JSON estrito (jsoncodec.py)."""
from __future__ import annotations

from types import MappingProxyType
import unittest

from conciliacao_bancaria.errors import ConciliacaoError
from conciliacao_bancaria.jsoncodec import MAX_BYTES, dumps, dumps_output, loads


class TestJsonCodec(unittest.TestCase):
    def test_roundtrip_valido_bytes_e_str(self) -> None:
        doc = {
            "schema_version": 1,
            "ativo": True,
            "nulo": None,
            "valores": [-1500, 0, 250000],
            "texto": "Conciliação Bancária — Março",
        }
        serializado = dumps(doc)
        self.assertTrue(serializado.endswith("\n"))
        self.assertEqual(loads(serializado), doc)
        self.assertEqual(loads(serializado.encode("utf-8")), doc)

    def test_rejeita_bom_utf8_em_bytes_e_str(self) -> None:
        with self.assertRaises(ConciliacaoError) as ctx1:
            loads(b'\xef\xbb\xbf{"a": 1}')
        self.assertEqual(ctx1.exception.code, "JSON_BOM")

        with self.assertRaises(ConciliacaoError) as ctx2:
            loads('\ufeff{"a": 1}')
        self.assertEqual(ctx2.exception.code, "JSON_BOM")

    def test_rejeita_utf8_invalido_e_surrogates(self) -> None:
        with self.assertRaises(ConciliacaoError) as ctx1:
            loads(b'{"a": "\xff\xfe"}')
        self.assertEqual(ctx1.exception.code, "JSON_UTF8")

        with self.assertRaises(ConciliacaoError) as ctx2:
            loads('{"a": "\\ud800"}')
        self.assertEqual(ctx2.exception.code, "JSON_UTF8")

    def test_rejeita_float_ponto_e_expoente(self) -> None:
        for invalido in ('{"v": 10.5}', '{"v": 0.0}', '{"v": 1e3}', '{"v": -2.5e-1}'):
            with self.subTest(invalido=invalido):
                with self.assertRaises(ConciliacaoError) as ctx:
                    loads(invalido)
                self.assertEqual(ctx.exception.code, "JSON_FLOAT")

    def test_rejeita_nan_e_infinity(self) -> None:
        for invalido in ('{"v": NaN}', '{"v": Infinity}', '{"v": -Infinity}'):
            with self.subTest(invalido=invalido):
                with self.assertRaises(ConciliacaoError) as ctx:
                    loads(invalido)
                self.assertEqual(ctx.exception.code, "JSON_INVALIDO")

    def test_rejeita_chave_duplicada(self) -> None:
        with self.assertRaises(ConciliacaoError) as ctx:
            loads('{"a": 1, "b": {"x": 2, "x": 3}}')
        self.assertEqual(ctx.exception.code, "JSON_DUPLICADO")

    def test_rejeita_inteiro_acima_de_18_digitos(self) -> None:
        with self.assertRaises(ConciliacaoError) as ctx:
            loads('{"v": 1000000000000000000}')
        self.assertEqual(ctx.exception.code, "JSON_LIMITE")

    def test_rejeita_profundidade_excessiva(self) -> None:
        profundo = "[" * 18 + "1" + "]" * 18
        with self.assertRaises(ConciliacaoError) as ctx:
            loads(profundo)
        self.assertEqual(ctx.exception.code, "JSON_LIMITE")

    def test_rejeita_excesso_de_bytes(self) -> None:
        grande = b" " * (MAX_BYTES + 1)
        with self.assertRaises(ConciliacaoError) as ctx:
            loads(grande)
        self.assertEqual(ctx.exception.code, "JSON_LIMITE")

    def test_rejeita_float_na_serializacao(self) -> None:
        with self.assertRaises(ConciliacaoError) as ctx:
            dumps({"v": 1.25})
        self.assertEqual(ctx.exception.code, "JSON_FLOAT")

    def test_rejeita_tipo_nao_suportado_na_serializacao(self) -> None:
        with self.assertRaises(ConciliacaoError) as ctx:
            dumps({"v": {1, 2, 3}})
        self.assertEqual(ctx.exception.code, "JSON_INVALIDO")

    def test_suporta_mappingproxy_e_tupla_em_dumps_output(self) -> None:
        congelado = MappingProxyType({"lista": (1, 2, 3), "vazio": (), "mapa_vazio": MappingProxyType({})})
        texto = dumps_output(congelado)
        self.assertEqual(loads(texto), {"lista": [1, 2, 3], "vazio": [], "mapa_vazio": {}})

    def test_rejeita_entrada_tipo_incorreto_em_loads(self) -> None:
        with self.assertRaises(ConciliacaoError) as ctx:
            loads(12345)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "JSON_INVALIDO")

    def test_rejeita_sintaxe_json_invalida(self) -> None:
        for invalido in ("}", '{"a": 1,}', "[1, 2"):
            with self.subTest(invalido=invalido):
                with self.assertRaises(ConciliacaoError) as ctx:
                    loads(invalido)
                self.assertEqual(ctx.exception.code, "JSON_INVALIDO")

    def test_rejeita_surrogate_em_dumps(self) -> None:
        with self.assertRaises(ConciliacaoError) as ctx:
            dumps({"texto": "\ud800"})
        self.assertEqual(ctx.exception.code, "JSON_UTF8")


if __name__ == "__main__":
    unittest.main()
