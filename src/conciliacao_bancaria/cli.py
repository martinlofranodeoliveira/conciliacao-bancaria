"""Interface de linha de comando (CLI) em português para conciliação bancária."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from . import __version__
from .core import load_example_data, reconcile_example, reconcile_json
from .errors import ConciliacaoError
from .jsoncodec import MAX_BYTES, dumps, dumps_output
from .reports import (
    publish_file_bytes,
    render_markdown_report,
    render_svg_panel,
    verify_bundle_zip,
    write_bundle_directory,
    write_bundle_zip,
)


class _CLIParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ConciliacaoError(
            "CLI_ARGUMENTO_INVALIDO",
            f"Argumentos inválidos na linha de comando: {message}",
            path="cli",
        )


def _read_input_bytes(caminho_str: str) -> bytes:
    if not caminho_str.strip() or "\x00" in caminho_str:
        raise ConciliacaoError("CAMINHO_INVALIDO", "Caminho de arquivo de entrada inválido.", path="entrada")
    caminho = Path(caminho_str)
    if not caminho.is_file():
        raise ConciliacaoError(
            "ARQUIVO_NAO_ENCONTRADO",
            f"Arquivo de entrada não encontrado: {caminho.name}.",
            path="entrada",
        )
    try:
        tamanho = caminho.stat().st_size
        if tamanho > MAX_BYTES:
            raise ConciliacaoError(
                "JSON_LIMITE",
                "Arquivo de entrada excede o limite máximo de 8 MiB.",
                path="entrada",
            )
        return caminho.read_bytes()
    except ConciliacaoError:
        raise
    except OSError:
        raise ConciliacaoError(
            "ERRO_LEITURA",
            f"Não foi possível ler o arquivo de entrada: {caminho.name}.",
            path="entrada",
        ) from None


def _build_parser() -> argparse.ArgumentParser:
    parser = _CLIParser(
        prog="conciliacao-bancaria",
        description="Concilie razão interno e extrato bancário em centavos exatos: 1:1, lotes, tarifas, ambiguidades e pendências.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"conciliacao-bancaria {__version__}",
    )
    subparsers = parser.add_subparsers(dest="comando")

    p_exemplo = subparsers.add_parser(
        "exemplo",
        help="Exporta o cenário sintético oficial (.json, .zip, .svg ou diretório).",
    )
    p_exemplo.add_argument(
        "--saida",
        required=True,
        help="Caminho de saída (.json para entrada sintética, .zip para pacote completo ou pasta nova).",
    )

    p_conciliar = subparsers.add_parser(
        "conciliar",
        help="Executa a conciliação de um arquivo JSON de entrada e publica o resultado.",
    )
    p_conciliar.add_argument(
        "entrada",
        help="Caminho do arquivo JSON de entrada (UTF-8 estrito sem BOM e sem floats).",
    )
    p_conciliar.add_argument(
        "--saida",
        required=True,
        help="Caminho de saída (.zip para pacote auditável, .json para resultado, .svg, .md ou pasta nova).",
    )
    p_conciliar.add_argument(
        "--janela-dias",
        type=int,
        default=None,
        help="Sobrescreve a janela máxima em dias corridos (0..30).",
    )
    p_conciliar.add_argument(
        "--max-agrupamento",
        type=int,
        default=None,
        help="Sobrescreve a cardinalidade máxima de agrupamento 1:N e N:1 (1..8).",
    )
    p_conciliar.add_argument(
        "--orcamento-combinacoes",
        type=int,
        default=None,
        help="Sobrescreve o orçamento máximo de combinações avaliadas (1..50000).",
    )

    p_verificar = subparsers.add_parser(
        "verificar",
        help="Verifica a integridade e o manifesto SHA256SUMS de um pacote ZIP gerado.",
    )
    p_verificar.add_argument(
        "pacote",
        help="Caminho do arquivo .zip a verificar.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada principal da CLI."""
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        if not args.comando:
            raise ConciliacaoError(
                "CLI_ARGUMENTO_INVALIDO",
                "Informe um subcomando: 'exemplo', 'conciliar' ou 'verificar'.",
                path="cli",
            )

        if args.comando == "exemplo":
            saida = str(args.saida)
            sufixo = saida.lower()
            if sufixo.endswith(".json"):
                dados_exemplo = load_example_data()
                caminho = publish_file_bytes(saida, dumps(dados_exemplo).encode("utf-8"))
                sys.stdout.write(
                    dumps(
                        {
                            "status": "OK",
                            "comando": "exemplo",
                            "formato": "json",
                            "saida": str(caminho),
                        }
                    )
                )
                return 0
            resultado = reconcile_example()
            if sufixo.endswith(".zip"):
                caminho = write_bundle_zip(resultado, saida)
                formato = "zip"
            elif sufixo.endswith(".svg"):
                caminho = publish_file_bytes(saida, render_svg_panel(resultado).encode("utf-8"))
                formato = "svg"
            elif sufixo.endswith(".md"):
                caminho = publish_file_bytes(saida, render_markdown_report(resultado).encode("utf-8"))
                formato = "md"
            else:
                caminho = write_bundle_directory(resultado, saida)
                formato = "diretorio"
            sys.stdout.write(
                dumps(
                    {
                        "status": "OK",
                        "comando": "exemplo",
                        "formato": formato,
                        "saida": str(caminho),
                        "resumo": resultado.resumo.to_dict(),
                        "conservacao_exata": resultado.quadro_saldos.conservacao_exata,
                    }
                )
            )
            return 0

        if args.comando == "conciliar":
            bruto = _read_input_bytes(str(args.entrada))
            resultado = reconcile_json(
                bruto,
                janela_dias=args.janela_dias,
                max_agrupamento=args.max_agrupamento,
                orcamento_combinacoes=args.orcamento_combinacoes,
            )
            saida = str(args.saida)
            sufixo = saida.lower()
            if sufixo.endswith(".zip"):
                caminho = write_bundle_zip(resultado, saida)
                formato = "zip"
            elif sufixo.endswith(".json"):
                caminho = publish_file_bytes(
                    saida, dumps_output(resultado.to_dict()).encode("utf-8")
                )
                formato = "json"
            elif sufixo.endswith(".svg"):
                caminho = publish_file_bytes(saida, render_svg_panel(resultado).encode("utf-8"))
                formato = "svg"
            elif sufixo.endswith(".md"):
                caminho = publish_file_bytes(
                    saida, render_markdown_report(resultado).encode("utf-8")
                )
                formato = "md"
            else:
                caminho = write_bundle_directory(resultado, saida)
                formato = "diretorio"

            sys.stdout.write(
                dumps(
                    {
                        "status": "OK",
                        "comando": "conciliar",
                        "formato": formato,
                        "saida": str(caminho),
                        "resumo": resultado.resumo.to_dict(),
                        "conservacao_exata": resultado.quadro_saldos.conservacao_exata,
                    }
                )
            )
            return 0

        if args.comando == "verificar":
            hashes = verify_bundle_zip(str(args.pacote))
            sys.stdout.write(
                dumps(
                    {
                        "status": "OK",
                        "comando": "verificar",
                        "arquivos_verificados": len(hashes),
                        "hashes": hashes,
                    }
                )
            )
            return 0

        raise ConciliacaoError("CLI_ARGUMENTO_INVALIDO", "Subcomando desconhecido.", path="cli")
    except ConciliacaoError as exc:
        sys.stderr.write(f"[{exc.code}] {exc.message} (caminho: {exc.path})\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
