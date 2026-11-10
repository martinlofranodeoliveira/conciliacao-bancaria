"""Motor determinístico de conciliação bancária em centavos inteiros exatos."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from importlib import resources
from itertools import combinations
import re
from types import MappingProxyType
from typing import Any, Mapping

from . import __version__
from .errors import ConciliacaoError
from .jsoncodec import loads

MAX_LANCAMENTOS = 5_000
MAX_AJUSTES = 1_000
MAX_VALOR_ABS_CENTAVOS = 999_999_999_999
MAX_JANELA_DIAS = 30
PADRAO_JANELA_DIAS = 3
MAX_AGRUPAMENTO_LIMITE = 8
PADRAO_MAX_AGRUPAMENTO = 4
MAX_ORCAMENTO_COMBINACOES = 50_000
PADRAO_ORCAMENTO_COMBINACOES = 5_000

NATUREZAS_AJUSTE = (
    "TARIFA",
    "IOF",
    "JUROS",
    "ENCARGO",
    "RENDIMENTO",
    "ESTORNO",
    "AJUSTE_OPERACIONAL",
)

_ID_ASCII = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z", re.ASCII)
_MOEDA_ISO = re.compile(r"[A-Z]{3}\Z", re.ASCII)
_DATA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}\Z", re.ASCII)

_CAMPOS_RAIZ = frozenset(
    {"schema_version", "conta", "parametros", "razao", "extrato", "ajustes_declarados"}
)
_CAMPOS_RAIZ_OBRIGATORIOS = frozenset({"schema_version", "conta", "razao", "extrato"})
_CAMPOS_CONTA = frozenset(
    {
        "id",
        "titulo",
        "moeda",
        "banco",
        "agencia",
        "numero_conta",
        "data_inicial",
        "data_final",
        "saldo_inicial_razao_centavos",
        "saldo_final_razao_centavos",
        "saldo_inicial_extrato_centavos",
        "saldo_final_extrato_centavos",
    }
)
_CAMPOS_PARAMETROS = frozenset({"janela_dias", "max_agrupamento", "orcamento_combinacoes"})
_CAMPOS_RAZAO = frozenset({"id", "data", "historico", "documento", "valor_centavos", "categoria"})
_CAMPOS_RAZAO_OBRIGATORIOS = frozenset({"id", "data", "historico", "documento", "valor_centavos"})
_CAMPOS_EXTRATO = frozenset({"id", "data", "descricao", "documento", "valor_centavos", "tipo"})
_CAMPOS_EXTRATO_OBRIGATORIOS = frozenset({"id", "data", "descricao", "documento", "valor_centavos"})
_CAMPOS_AJUSTE = frozenset({"id", "extrato_id", "natureza", "conta_contabil", "justificativa"})


def format_brl(centavos: int) -> str:
    """Formata um inteiro em centavos no padrão monetário brasileiro (ex.: R$ 1.234,56 ou -R$ 89,00)."""
    if type(centavos) is not int:
        raise TypeError("O valor em centavos deve ser int.")
    negativo = centavos < 0
    absoluto = abs(centavos)
    reais, cents = divmod(absoluto, 100)
    reais_txt = f"{reais:,}".replace(",", ".")
    prefixo = "-R$ " if negativo else "R$ "
    return f"{prefixo}{reais_txt},{cents:02d}"


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, (dict, MappingProxyType)):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class ContaBancaria:
    id: str
    titulo: str
    moeda: str
    banco: str
    agencia: str
    numero_conta: str
    data_inicial: str
    data_final: str
    saldo_inicial_razao_centavos: int
    saldo_final_razao_centavos: int
    saldo_inicial_extrato_centavos: int
    saldo_final_extrato_centavos: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "titulo": self.titulo,
            "moeda": self.moeda,
            "banco": self.banco,
            "agencia": self.agencia,
            "numero_conta": self.numero_conta,
            "data_inicial": self.data_inicial,
            "data_final": self.data_final,
            "saldo_inicial_razao_centavos": self.saldo_inicial_razao_centavos,
            "saldo_final_razao_centavos": self.saldo_final_razao_centavos,
            "saldo_inicial_extrato_centavos": self.saldo_inicial_extrato_centavos,
            "saldo_final_extrato_centavos": self.saldo_final_extrato_centavos,
        }


@dataclass(frozen=True)
class ParametrosConciliacao:
    janela_dias: int
    max_agrupamento: int
    orcamento_combinacoes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "janela_dias": self.janela_dias,
            "max_agrupamento": self.max_agrupamento,
            "orcamento_combinacoes": self.orcamento_combinacoes,
        }


@dataclass(frozen=True)
class LancamentoRazao:
    id: str
    data: str
    historico: str
    documento: str
    valor_centavos: int
    categoria: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "data": self.data,
            "historico": self.historico,
            "documento": self.documento,
            "valor_centavos": self.valor_centavos,
            "categoria": self.categoria,
        }


@dataclass(frozen=True)
class LancamentoExtrato:
    id: str
    data: str
    descricao: str
    documento: str
    valor_centavos: int
    tipo: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "data": self.data,
            "descricao": self.descricao,
            "documento": self.documento,
            "valor_centavos": self.valor_centavos,
            "tipo": self.tipo,
        }


@dataclass(frozen=True)
class AjusteDeclarado:
    id: str
    extrato_id: str
    natureza: str
    conta_contabil: str
    justificativa: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "extrato_id": self.extrato_id,
            "natureza": self.natureza,
            "conta_contabil": self.conta_contabil,
            "justificativa": self.justificativa,
        }


@dataclass(frozen=True)
class EntradaConciliacao:
    schema_version: int
    conta: ContaBancaria
    parametros: ParametrosConciliacao
    razao: tuple[LancamentoRazao, ...]
    extrato: tuple[LancamentoExtrato, ...]
    ajustes_declarados: tuple[AjusteDeclarado, ...]


@dataclass(frozen=True)
class CandidatoAgrupamento:
    tipo: str
    razao_ids: tuple[str, ...]
    extrato_ids: tuple[str, ...]
    soma_centavos: int
    diferenca_dias_max: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tipo": self.tipo,
            "razao_ids": list(self.razao_ids),
            "extrato_ids": list(self.extrato_ids),
            "soma_centavos": self.soma_centavos,
            "diferenca_dias_max": self.diferenca_dias_max,
        }


@dataclass(frozen=True)
class GrupoConciliado:
    id: str
    tipo: str
    regra: str
    razao_ids: tuple[str, ...]
    extrato_ids: tuple[str, ...]
    ajuste_id: str | None
    valor_razao_centavos: int
    valor_extrato_centavos: int
    diferenca_dias_max: int
    documento_comum: str | None
    natureza_ajuste: str | None
    conta_contabil: str | None
    justificativa: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tipo": self.tipo,
            "regra": self.regra,
            "razao_ids": list(self.razao_ids),
            "extrato_ids": list(self.extrato_ids),
            "ajuste_id": self.ajuste_id,
            "valor_razao_centavos": self.valor_razao_centavos,
            "valor_extrato_centavos": self.valor_extrato_centavos,
            "diferenca_dias_max": self.diferenca_dias_max,
            "documento_comum": self.documento_comum,
            "natureza_ajuste": self.natureza_ajuste,
            "conta_contabil": self.conta_contabil,
            "justificativa": self.justificativa,
        }


@dataclass(frozen=True)
class AmbiguidadeDetectada:
    id: str
    estagio: str
    motivo: str
    razao_ids: tuple[str, ...]
    extrato_ids: tuple[str, ...]
    soma_razao_centavos: int
    soma_extrato_centavos: int
    valor_alvo_centavos: int
    candidatos: tuple[CandidatoAgrupamento, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "estagio": self.estagio,
            "motivo": self.motivo,
            "razao_ids": list(self.razao_ids),
            "extrato_ids": list(self.extrato_ids),
            "soma_razao_centavos": self.soma_razao_centavos,
            "soma_extrato_centavos": self.soma_extrato_centavos,
            "valor_alvo_centavos": self.valor_alvo_centavos,
            "candidatos": [c.to_dict() for c in self.candidatos],
        }


@dataclass(frozen=True)
class ItemPendente:
    origem: str
    status: str
    id: str
    data: str
    descricao: str
    documento: str
    valor_centavos: int
    classificacao: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "origem": self.origem,
            "status": self.status,
            "id": self.id,
            "data": self.data,
            "descricao": self.descricao,
            "documento": self.documento,
            "valor_centavos": self.valor_centavos,
            "classificacao": self.classificacao,
        }


@dataclass(frozen=True)
class StatusLancamento:
    origem: str
    id: str
    data: str
    descricao: str
    documento: str
    classificacao: str
    valor_centavos: int
    status: str
    referencia_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "origem": self.origem,
            "id": self.id,
            "data": self.data,
            "descricao": self.descricao,
            "documento": self.documento,
            "classificacao": self.classificacao,
            "valor_centavos": self.valor_centavos,
            "status": self.status,
            "referencia_id": self.referencia_id,
        }


@dataclass(frozen=True)
class QuadroSaldos:
    saldo_inicial_razao_centavos: int
    soma_movimentos_razao_centavos: int
    saldo_final_razao_centavos: int
    saldo_inicial_extrato_centavos: int
    soma_movimentos_extrato_centavos: int
    saldo_final_extrato_centavos: int
    diferenca_saldo_inicial_centavos: int
    divergencia_bruta_saldos_centavos: int
    soma_conciliados_razao_centavos: int
    soma_conciliados_extrato_centavos: int
    soma_ajustes_declarados_centavos: int
    soma_ambiguos_razao_centavos: int
    soma_ambiguos_extrato_centavos: int
    efeito_liquido_ambiguos_centavos: int
    soma_pendentes_razao_centavos: int
    soma_pendentes_extrato_centavos: int
    efeito_liquido_pendentes_centavos: int
    saldo_razao_ajustado_centavos: int
    saldo_extrato_conciliado_centavos: int
    diferenca_nao_explicada_centavos: int
    conservacao_exata: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "saldo_inicial_razao_centavos": self.saldo_inicial_razao_centavos,
            "soma_movimentos_razao_centavos": self.soma_movimentos_razao_centavos,
            "saldo_final_razao_centavos": self.saldo_final_razao_centavos,
            "saldo_inicial_extrato_centavos": self.saldo_inicial_extrato_centavos,
            "soma_movimentos_extrato_centavos": self.soma_movimentos_extrato_centavos,
            "saldo_final_extrato_centavos": self.saldo_final_extrato_centavos,
            "diferenca_saldo_inicial_centavos": self.diferenca_saldo_inicial_centavos,
            "divergencia_bruta_saldos_centavos": self.divergencia_bruta_saldos_centavos,
            "soma_conciliados_razao_centavos": self.soma_conciliados_razao_centavos,
            "soma_conciliados_extrato_centavos": self.soma_conciliados_extrato_centavos,
            "soma_ajustes_declarados_centavos": self.soma_ajustes_declarados_centavos,
            "soma_ambiguos_razao_centavos": self.soma_ambiguos_razao_centavos,
            "soma_ambiguos_extrato_centavos": self.soma_ambiguos_extrato_centavos,
            "efeito_liquido_ambiguos_centavos": self.efeito_liquido_ambiguos_centavos,
            "soma_pendentes_razao_centavos": self.soma_pendentes_razao_centavos,
            "soma_pendentes_extrato_centavos": self.soma_pendentes_extrato_centavos,
            "efeito_liquido_pendentes_centavos": self.efeito_liquido_pendentes_centavos,
            "saldo_razao_ajustado_centavos": self.saldo_razao_ajustado_centavos,
            "saldo_extrato_conciliado_centavos": self.saldo_extrato_conciliado_centavos,
            "diferenca_nao_explicada_centavos": self.diferenca_nao_explicada_centavos,
            "conservacao_exata": self.conservacao_exata,
        }


@dataclass(frozen=True)
class ResumoConciliacao:
    total_lancamentos_razao: int
    total_lancamentos_extrato: int
    total_grupos_conciliados: int
    total_exatos_1_1: int
    total_lotes_1_n: int
    total_lotes_n_1: int
    total_ajustes_declarados: int
    total_grupos_ambiguos: int
    total_lancamentos_razao_conciliados: int
    total_lancamentos_extrato_conciliados: int
    total_lancamentos_extrato_ajustados: int
    total_lancamentos_razao_ambiguos: int
    total_lancamentos_extrato_ambiguos: int
    total_pendentes_razao: int
    total_pendentes_extrato: int
    combinacoes_avaliadas: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_lancamentos_razao": self.total_lancamentos_razao,
            "total_lancamentos_extrato": self.total_lancamentos_extrato,
            "total_grupos_conciliados": self.total_grupos_conciliados,
            "total_exatos_1_1": self.total_exatos_1_1,
            "total_lotes_1_n": self.total_lotes_1_n,
            "total_lotes_n_1": self.total_lotes_n_1,
            "total_ajustes_declarados": self.total_ajustes_declarados,
            "total_grupos_ambiguos": self.total_grupos_ambiguos,
            "total_lancamentos_razao_conciliados": self.total_lancamentos_razao_conciliados,
            "total_lancamentos_extrato_conciliados": self.total_lancamentos_extrato_conciliados,
            "total_lancamentos_extrato_ajustados": self.total_lancamentos_extrato_ajustados,
            "total_lancamentos_razao_ambiguos": self.total_lancamentos_razao_ambiguos,
            "total_lancamentos_extrato_ambiguos": self.total_lancamentos_extrato_ambiguos,
            "total_pendentes_razao": self.total_pendentes_razao,
            "total_pendentes_extrato": self.total_pendentes_extrato,
            "combinacoes_avaliadas": self.combinacoes_avaliadas,
        }


@dataclass(frozen=True)
class ResultadoConciliacao:
    entrada: EntradaConciliacao
    resumo: ResumoConciliacao
    quadro_saldos: QuadroSaldos
    grupos_conciliados: tuple[GrupoConciliado, ...]
    ambiguidades: tuple[AmbiguidadeDetectada, ...]
    pendentes_razao: tuple[ItemPendente, ...]
    pendentes_extrato: tuple[ItemPendente, ...]
    lancamentos_status: tuple[StatusLancamento, ...]
    _fonte_original: Mapping[str, Any]

    def source_document(self) -> dict[str, Any]:
        """Retorna cópia mutável do documento de entrada validado."""
        return _thaw(self._fonte_original)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "ferramenta": {
                "nome": "conciliacao-bancaria",
                "versao": __version__,
            },
            "conta": self.entrada.conta.to_dict(),
            "parametros": self.entrada.parametros.to_dict(),
            "resumo": self.resumo.to_dict(),
            "quadro_saldos": self.quadro_saldos.to_dict(),
            "grupos_conciliados": [g.to_dict() for g in self.grupos_conciliados],
            "ambiguidades": [a.to_dict() for a in self.ambiguidades],
            "pendentes_razao": [p.to_dict() for p in self.pendentes_razao],
            "pendentes_extrato": [p.to_dict() for p in self.pendentes_extrato],
            "lancamentos_status": [s.to_dict() for s in self.lancamentos_status],
        }


def _require_mapping(
    obj: Any,
    path: str,
    permitidos: frozenset[str],
    obrigatorios: frozenset[str],
) -> Mapping[str, Any]:
    if not isinstance(obj, (dict, MappingProxyType)):
        raise ConciliacaoError("ESQUEMA_INVALIDO", f"Esperado objeto JSON em {path}.", path=path)
    chaves = set(obj.keys())
    extras = sorted(chaves - permitidos)
    if extras:
        raise ConciliacaoError(
            "CAMPO_DESCONHECIDO",
            f"Campo desconhecido em {path}: {extras[0]!r}.",
            path=f"{path}.{extras[0]}",
        )
    faltantes = sorted(obrigatorios - chaves)
    if faltantes:
        raise ConciliacaoError(
            "ESQUEMA_INVALIDO",
            f"Campo obrigatório ausente em {path}: {faltantes[0]!r}.",
            path=f"{path}.{faltantes[0]}",
        )
    return obj


def _require_id(value: Any, path: str) -> str:
    if type(value) is not str or _ID_ASCII.fullmatch(value) is None:
        raise ConciliacaoError(
            "ESQUEMA_INVALIDO",
            f"Identificador inválido em {path}: use apenas ASCII [A-Za-z0-9._-] (1 a 64 caracteres).",
            path=path,
        )
    return value


def _require_text(value: Any, path: str, max_len: int = 200) -> str:
    if type(value) is not str:
        raise ConciliacaoError("ESQUEMA_INVALIDO", f"Texto obrigatório em {path}.", path=path)
    if any(ord(c) < 32 or ord(c) == 127 or (0xD800 <= ord(c) <= 0xDFFF) for c in value):
        raise ConciliacaoError(
            "ESQUEMA_INVALIDO",
            f"Texto em {path} contém caracteres de controle ou substitutos Unicode inválidos.",
            path=path,
        )
    limpo = value.strip()
    if not limpo or len(value) > max_len:
        raise ConciliacaoError(
            "ESQUEMA_INVALIDO",
            f"Texto em {path} deve ser não vazio e ter até {max_len} caracteres.",
            path=path,
        )
    return value


def _require_int(
    value: Any,
    path: str,
    minimo: int,
    maximo: int,
    *,
    permitir_zero: bool = True,
    code: str = "ESQUEMA_INVALIDO",
) -> int:
    if type(value) is bool:
        raise ConciliacaoError("ESQUEMA_INVALIDO", f"Booleano não é permitido como inteiro em {path}.", path=path)
    if type(value) is float:
        raise ConciliacaoError("JSON_FLOAT", f"Float não é permitido em {path}.", path=path)
    if type(value) is not int:
        raise ConciliacaoError("ESQUEMA_INVALIDO", f"Esperado inteiro exato em {path}.", path=path)
    if not permitir_zero and value == 0:
        raise ConciliacaoError(
            "VALOR_ZERO_INVALIDO",
            f"Lançamento com valor zero não é permitido em {path}.",
            path=path,
        )
    if not (minimo <= value <= maximo):
        raise ConciliacaoError(
            code,
            f"Inteiro em {path} fora do intervalo permitido [{minimo}, {maximo}]: {value}.",
            path=path,
        )
    return value


def _parse_iso_date(value: Any, path: str) -> date:
    if type(value) is not str or _DATA_ISO.fullmatch(value) is None:
        raise ConciliacaoError("DATA_INVALIDA", f"Data inválida em {path}; use formato YYYY-MM-DD.", path=path)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ConciliacaoError("DATA_INVALIDA", f"Data civil inexistente em {path}: {value}.", path=path) from None
    if not (1970 <= parsed.year <= 2099):
        raise ConciliacaoError(
            "DATA_INVALIDA",
            f"Ano fora da faixa suportada (1970..2099) em {path}: {value}.",
            path=path,
        )
    return parsed


def parse_input(
    documento: Any,
    *,
    janela_dias_override: int | None = None,
    max_agrupamento_override: int | None = None,
    orcamento_combinacoes_override: int | None = None,
) -> EntradaConciliacao:
    """Valida o documento de entrada e constrói estruturas imutáveis de conciliação."""
    raiz = _require_mapping(documento, "raiz", _CAMPOS_RAIZ, _CAMPOS_RAIZ_OBRIGATORIOS)
    versao = raiz["schema_version"]
    if type(versao) is not int or versao != 1:
        raise ConciliacaoError(
            "VERSAO_ESQUEMA_INVALIDA",
            "Versão de esquema não suportada; esperado schema_version = 1.",
            path="raiz.schema_version",
        )

    params_raw = raiz.get("parametros")
    if params_raw is None:
        janela_raw = PADRAO_JANELA_DIAS
        max_agrup_raw = PADRAO_MAX_AGRUPAMENTO
        orcamento_raw = PADRAO_ORCAMENTO_COMBINACOES
    else:
        params_obj = _require_mapping(params_raw, "parametros", _CAMPOS_PARAMETROS, frozenset())
        janela_raw = params_obj.get("janela_dias", PADRAO_JANELA_DIAS)
        max_agrup_raw = params_obj.get("max_agrupamento", PADRAO_MAX_AGRUPAMENTO)
        orcamento_raw = params_obj.get("orcamento_combinacoes", PADRAO_ORCAMENTO_COMBINACOES)

    janela_dias = _require_int(janela_raw, "parametros.janela_dias", 0, MAX_JANELA_DIAS)
    max_agrupamento = _require_int(max_agrup_raw, "parametros.max_agrupamento", 1, MAX_AGRUPAMENTO_LIMITE)
    orcamento_combinacoes = _require_int(
        orcamento_raw, "parametros.orcamento_combinacoes", 1, MAX_ORCAMENTO_COMBINACOES
    )

    if janela_dias_override is not None:
        janela_dias = _require_int(janela_dias_override, "janela_dias", 0, MAX_JANELA_DIAS)
    if max_agrupamento_override is not None:
        max_agrupamento = _require_int(
            max_agrupamento_override, "max_agrupamento", 1, MAX_AGRUPAMENTO_LIMITE
        )
    if orcamento_combinacoes_override is not None:
        orcamento_combinacoes = _require_int(
            orcamento_combinacoes_override, "orcamento_combinacoes", 1, MAX_ORCAMENTO_COMBINACOES
        )

    parametros = ParametrosConciliacao(
        janela_dias=janela_dias,
        max_agrupamento=max_agrupamento,
        orcamento_combinacoes=orcamento_combinacoes,
    )

    conta_obj = _require_mapping(raiz["conta"], "conta", _CAMPOS_CONTA, _CAMPOS_CONTA)
    conta_id = _require_id(conta_obj["id"], "conta.id")
    conta_titulo = _require_text(conta_obj["titulo"], "conta.titulo", max_len=160)
    moeda = conta_obj["moeda"]
    if type(moeda) is not str or _MOEDA_ISO.fullmatch(moeda) is None:
        raise ConciliacaoError(
            "ESQUEMA_INVALIDO",
            "Código de moeda inválido em conta.moeda; esperado código ISO de 3 letras maiúsculas (ex.: BRL).",
            path="conta.moeda",
        )
    banco = _require_text(conta_obj["banco"], "conta.banco", max_len=120)
    agencia = _require_text(conta_obj["agencia"], "conta.agencia", max_len=32)
    numero_conta = _require_text(conta_obj["numero_conta"], "conta.numero_conta", max_len=32)
    dt_ini = _parse_iso_date(conta_obj["data_inicial"], "conta.data_inicial")
    dt_fim = _parse_iso_date(conta_obj["data_final"], "conta.data_final")
    if dt_ini > dt_fim:
        raise ConciliacaoError(
            "DATA_INVALIDA",
            "conta.data_inicial não pode ser posterior a conta.data_final.",
            path="conta.data_inicial",
        )
    saldo_ini_razao = _require_int(
        conta_obj["saldo_inicial_razao_centavos"],
        "conta.saldo_inicial_razao_centavos",
        -MAX_VALOR_ABS_CENTAVOS,
        MAX_VALOR_ABS_CENTAVOS,
    )
    saldo_fim_razao = _require_int(
        conta_obj["saldo_final_razao_centavos"],
        "conta.saldo_final_razao_centavos",
        -MAX_VALOR_ABS_CENTAVOS,
        MAX_VALOR_ABS_CENTAVOS,
    )
    saldo_ini_extrato = _require_int(
        conta_obj["saldo_inicial_extrato_centavos"],
        "conta.saldo_inicial_extrato_centavos",
        -MAX_VALOR_ABS_CENTAVOS,
        MAX_VALOR_ABS_CENTAVOS,
    )
    saldo_fim_extrato = _require_int(
        conta_obj["saldo_final_extrato_centavos"],
        "conta.saldo_final_extrato_centavos",
        -MAX_VALOR_ABS_CENTAVOS,
        MAX_VALOR_ABS_CENTAVOS,
    )

    conta = ContaBancaria(
        id=conta_id,
        titulo=conta_titulo,
        moeda=moeda,
        banco=banco,
        agencia=agencia,
        numero_conta=numero_conta,
        data_inicial=conta_obj["data_inicial"],
        data_final=conta_obj["data_final"],
        saldo_inicial_razao_centavos=saldo_ini_razao,
        saldo_final_razao_centavos=saldo_fim_razao,
        saldo_inicial_extrato_centavos=saldo_ini_extrato,
        saldo_final_extrato_centavos=saldo_fim_extrato,
    )

    razao_raw = raiz["razao"]
    if not isinstance(razao_raw, (list, tuple)) or len(razao_raw) > MAX_LANCAMENTOS:
        raise ConciliacaoError(
            "ESQUEMA_INVALIDO",
            f"razao deve ser uma lista com até {MAX_LANCAMENTOS} lançamentos.",
            path="razao",
        )
    ids_razao: set[str] = set()
    razao_lista: list[LancamentoRazao] = []
    for idx, item in enumerate(razao_raw):
        r_path = f"razao[{idx}]"
        r_obj = _require_mapping(item, r_path, _CAMPOS_RAZAO, _CAMPOS_RAZAO_OBRIGATORIOS)
        r_id = _require_id(r_obj["id"], f"{r_path}.id")
        if r_id in ids_razao:
            raise ConciliacaoError(
                "ID_DUPLICADO",
                f"Identificador de lançamento do razão duplicado: {r_id}.",
                path=f"{r_path}.id",
            )
        ids_razao.add(r_id)
        dt_r = _parse_iso_date(r_obj["data"], f"{r_path}.data")
        if not (dt_ini <= dt_r <= dt_fim):
            raise ConciliacaoError(
                "DATA_INVALIDA",
                f"Data do lançamento {r_id} ({r_obj['data']}) fora do período da conta [{conta.data_inicial}, {conta.data_final}].",
                path=f"{r_path}.data",
            )
        historico = _require_text(r_obj["historico"], f"{r_path}.historico", max_len=200)
        documento_r = _require_text(r_obj["documento"], f"{r_path}.documento", max_len=64)
        valor_r = _require_int(
            r_obj["valor_centavos"],
            f"{r_path}.valor_centavos",
            -MAX_VALOR_ABS_CENTAVOS,
            MAX_VALOR_ABS_CENTAVOS,
            permitir_zero=False,
        )
        cat_raw = r_obj.get("categoria", "GERAL")
        categoria = _require_text(cat_raw, f"{r_path}.categoria", max_len=80)
        razao_lista.append(
            LancamentoRazao(
                id=r_id,
                data=r_obj["data"],
                historico=historico,
                documento=documento_r,
                valor_centavos=valor_r,
                categoria=categoria,
            )
        )

    extrato_raw = raiz["extrato"]
    if not isinstance(extrato_raw, (list, tuple)) or len(extrato_raw) > MAX_LANCAMENTOS:
        raise ConciliacaoError(
            "ESQUEMA_INVALIDO",
            f"extrato deve ser uma lista com até {MAX_LANCAMENTOS} lançamentos.",
            path="extrato",
        )
    ids_extrato: set[str] = set()
    extrato_lista: list[LancamentoExtrato] = []
    for idx, item in enumerate(extrato_raw):
        e_path = f"extrato[{idx}]"
        e_obj = _require_mapping(item, e_path, _CAMPOS_EXTRATO, _CAMPOS_EXTRATO_OBRIGATORIOS)
        e_id = _require_id(e_obj["id"], f"{e_path}.id")
        if e_id in ids_extrato or e_id in ids_razao:
            raise ConciliacaoError(
                "ID_DUPLICADO",
                f"Identificador de lançamento do extrato duplicado ou colidente com razão: {e_id}.",
                path=f"{e_path}.id",
            )
        ids_extrato.add(e_id)
        dt_e = _parse_iso_date(e_obj["data"], f"{e_path}.data")
        if not (dt_ini <= dt_e <= dt_fim):
            raise ConciliacaoError(
                "DATA_INVALIDA",
                f"Data do lançamento {e_id} ({e_obj['data']}) fora do período da conta [{conta.data_inicial}, {conta.data_final}].",
                path=f"{e_path}.data",
            )
        descricao = _require_text(e_obj["descricao"], f"{e_path}.descricao", max_len=200)
        documento_e = _require_text(e_obj["documento"], f"{e_path}.documento", max_len=64)
        valor_e = _require_int(
            e_obj["valor_centavos"],
            f"{e_path}.valor_centavos",
            -MAX_VALOR_ABS_CENTAVOS,
            MAX_VALOR_ABS_CENTAVOS,
            permitir_zero=False,
        )
        tipo_raw = e_obj.get("tipo", "OUTROS")
        tipo = _require_text(tipo_raw, f"{e_path}.tipo", max_len=64)
        extrato_lista.append(
            LancamentoExtrato(
                id=e_id,
                data=e_obj["data"],
                descricao=descricao,
                documento=documento_e,
                valor_centavos=valor_e,
                tipo=tipo,
            )
        )

    soma_razao = sum(r.valor_centavos for r in razao_lista)
    if saldo_ini_razao + soma_razao != saldo_fim_razao:
        raise ConciliacaoError(
            "SALDO_INCONSISTENTE",
            f"Saldo final do razão declarado ({saldo_fim_razao}) diverge de saldo_inicial ({saldo_ini_razao}) + soma dos lançamentos ({soma_razao}) = {saldo_ini_razao + soma_razao}.",
            path="conta.saldo_final_razao_centavos",
            detalhes={
                "esperado_centavos": saldo_ini_razao + soma_razao,
                "declarado_centavos": saldo_fim_razao,
            },
        )

    soma_extrato = sum(e.valor_centavos for e in extrato_lista)
    if saldo_ini_extrato + soma_extrato != saldo_fim_extrato:
        raise ConciliacaoError(
            "SALDO_INCONSISTENTE",
            f"Saldo final do extrato declarado ({saldo_fim_extrato}) diverge de saldo_inicial ({saldo_ini_extrato}) + soma dos lançamentos ({soma_extrato}) = {saldo_ini_extrato + soma_extrato}.",
            path="conta.saldo_final_extrato_centavos",
            detalhes={
                "esperado_centavos": saldo_ini_extrato + soma_extrato,
                "declarado_centavos": saldo_fim_extrato,
            },
        )

    ajustes_raw = raiz.get("ajustes_declarados", [])
    if not isinstance(ajustes_raw, (list, tuple)) or len(ajustes_raw) > MAX_AJUSTES:
        raise ConciliacaoError(
            "ESQUEMA_INVALIDO",
            f"ajustes_declarados deve ser uma lista com até {MAX_AJUSTES} ajustes.",
            path="ajustes_declarados",
        )
    ids_ajuste: set[str] = set()
    extratos_ajustados: set[str] = set()
    ajustes_lista: list[AjusteDeclarado] = []
    for idx, item in enumerate(ajustes_raw):
        a_path = f"ajustes_declarados[{idx}]"
        a_obj = _require_mapping(item, a_path, _CAMPOS_AJUSTE, _CAMPOS_AJUSTE)
        a_id = _require_id(a_obj["id"], f"{a_path}.id")
        if a_id in ids_ajuste or a_id in ids_razao or a_id in ids_extrato:
            raise ConciliacaoError(
                "ID_DUPLICADO",
                f"Identificador de ajuste declarado duplicado ou colidente: {a_id}.",
                path=f"{a_path}.id",
            )
        ids_ajuste.add(a_id)
        ext_id = _require_id(a_obj["extrato_id"], f"{a_path}.extrato_id")
        if ext_id not in ids_extrato:
            raise ConciliacaoError(
                "REFERENCIA_INEXISTENTE",
                f"Ajuste {a_id} referencia lançamento de extrato inexistente: {ext_id}.",
                path=f"{a_path}.extrato_id",
            )
        if ext_id in extratos_ajustados:
            raise ConciliacaoError(
                "CONFLITO_AJUSTE",
                f"Lançamento de extrato {ext_id} já possui ajuste declarado associado.",
                path=f"{a_path}.extrato_id",
            )
        extratos_ajustados.add(ext_id)
        natureza = a_obj["natureza"]
        if type(natureza) is not str or natureza not in NATUREZAS_AJUSTE:
            raise ConciliacaoError(
                "AJUSTE_INVALIDO",
                f"Natureza de ajuste inválida em {a_id}: {natureza!r}; valores aceitos: {', '.join(NATUREZAS_AJUSTE)}.",
                path=f"{a_path}.natureza",
            )
        conta_contabil = _require_text(a_obj["conta_contabil"], f"{a_path}.conta_contabil", max_len=80)
        justificativa = _require_text(a_obj["justificativa"], f"{a_path}.justificativa", max_len=240)
        ajustes_lista.append(
            AjusteDeclarado(
                id=a_id,
                extrato_id=ext_id,
                natureza=natureza,
                conta_contabil=conta_contabil,
                justificativa=justificativa,
            )
        )

    return EntradaConciliacao(
        schema_version=1,
        conta=conta,
        parametros=parametros,
        razao=tuple(razao_lista),
        extrato=tuple(extrato_lista),
        ajustes_declarados=tuple(ajustes_lista),
    )


def _componentes_bipartidos(
    adj_r: Mapping[str, list[str]],
    adj_e: Mapping[str, list[str]],
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    visitados_r: set[str] = set()
    visitados_e: set[str] = set()
    componentes: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    for r_inicio in sorted(adj_r):
        if r_inicio in visitados_r or not adj_r[r_inicio]:
            continue
        comp_r: list[str] = []
        comp_e: list[str] = []
        fila: list[tuple[str, str]] = [("R", r_inicio)]
        visitados_r.add(r_inicio)
        idx = 0
        while idx < len(fila):
            lado, atual = fila[idx]
            idx += 1
            if lado == "R":
                comp_r.append(atual)
                for viz_e in adj_r[atual]:
                    if viz_e not in visitados_e:
                        visitados_e.add(viz_e)
                        fila.append(("E", viz_e))
            else:
                comp_e.append(atual)
                for viz_r in adj_e[atual]:
                    if viz_r not in visitados_r:
                        visitados_r.add(viz_r)
                        fila.append(("R", viz_r))
        componentes.append((tuple(sorted(comp_r)), tuple(sorted(comp_e))))

    return componentes


def reconcile(
    documento: Any,
    *,
    janela_dias: int | None = None,
    max_agrupamento: int | None = None,
    orcamento_combinacoes: int | None = None,
) -> ResultadoConciliacao:
    """Executa a conciliação bancária determinística em centavos exatos."""
    fonte_congelada = _freeze(documento)
    entrada = parse_input(
        documento,
        janela_dias_override=janela_dias,
        max_agrupamento_override=max_agrupamento,
        orcamento_combinacoes_override=orcamento_combinacoes,
    )

    razao_por_id = {r.id: r for r in entrada.razao}
    extrato_por_id = {e.id: e for e in entrada.extrato}
    datas_razao = {r.id: date.fromisoformat(r.data) for r in entrada.razao}
    datas_extrato = {e.id: date.fromisoformat(e.data) for e in entrada.extrato}

    razao_usados: set[str] = set()
    extrato_usados: set[str] = set()
    status_razao: dict[str, tuple[str, str | None]] = {}
    status_extrato: dict[str, tuple[str, str | None]] = {}

    grupos_brutos: list[dict[str, Any]] = []
    ambiguidades_brutas: list[dict[str, Any]] = []

    # Estágio 1: Ajustes declarados pelo operador
    for ajuste in sorted(entrada.ajustes_declarados, key=lambda a: (extrato_por_id[a.extrato_id].data, a.id)):
        ext = extrato_por_id[ajuste.extrato_id]
        extrato_usados.add(ext.id)
        grupos_brutos.append(
            {
                "ordem_data": ext.data,
                "ordem_chave": (3, ext.data, ajuste.id),
                "tipo": "AJUSTE_DECLARADO",
                "regra": "AJUSTE_OPERADOR",
                "razao_ids": (),
                "extrato_ids": (ext.id,),
                "ajuste_id": ajuste.id,
                "valor_razao_centavos": 0,
                "valor_extrato_centavos": ext.valor_centavos,
                "diferenca_dias_max": 0,
                "documento_comum": ext.documento,
                "natureza_ajuste": ajuste.natureza,
                "conta_contabil": ajuste.conta_contabil,
                "justificativa": ajuste.justificativa,
            }
        )

    # Estágio 2: Casamento 1:1 exato em centavos dentro de janela_dias
    r_livres_1 = [r for r in entrada.razao if r.id not in razao_usados]
    e_livres_1 = [e for e in entrada.extrato if e.id not in extrato_usados]

    adj_r: dict[str, list[str]] = {r.id: [] for r in r_livres_1}
    adj_e: dict[str, list[str]] = {e.id: [] for e in e_livres_1}

    janela = entrada.parametros.janela_dias
    for r in r_livres_1:
        dt_r = datas_razao[r.id]
        for e in e_livres_1:
            if r.valor_centavos == e.valor_centavos:
                diff = abs((datas_extrato[e.id] - dt_r).days)
                if diff <= janela:
                    adj_r[r.id].append(e.id)
                    adj_e[e.id].append(r.id)

    for r_id in adj_r:
        adj_r[r_id].sort()
    for e_id in adj_e:
        adj_e[e_id].sort()

    for comp_r, comp_e in _componentes_bipartidos(adj_r, adj_e):
        if len(comp_r) == 1 and len(comp_e) == 1:
            r_obj = razao_por_id[comp_r[0]]
            e_obj = extrato_por_id[comp_e[0]]
            diff = abs((datas_extrato[e_obj.id] - datas_razao[r_obj.id]).days)
            doc_comum = r_obj.documento if r_obj.documento == e_obj.documento else None
            regra = "VALOR_E_DOCUMENTO_EXATOS" if doc_comum is not None else "VALOR_EXATO_JANELA"
            razao_usados.add(r_obj.id)
            extrato_usados.add(e_obj.id)
            grupos_brutos.append(
                {
                    "ordem_data": min(r_obj.data, e_obj.data),
                    "ordem_chave": (0, min(r_obj.data, e_obj.data), r_obj.id, e_obj.id),
                    "tipo": "EXATO_1_1",
                    "regra": regra,
                    "razao_ids": (r_obj.id,),
                    "extrato_ids": (e_obj.id,),
                    "ajuste_id": None,
                    "valor_razao_centavos": r_obj.valor_centavos,
                    "valor_extrato_centavos": e_obj.valor_centavos,
                    "diferenca_dias_max": diff,
                    "documento_comum": doc_comum,
                    "natureza_ajuste": None,
                    "conta_contabil": None,
                    "justificativa": None,
                }
            )
        else:
            for r_id in comp_r:
                razao_usados.add(r_id)
            for e_id in comp_e:
                extrato_usados.add(e_id)
            candidatos_1_1: list[CandidatoAgrupamento] = []
            for r_id in comp_r:
                for e_id in adj_r[r_id]:
                    diff = abs((datas_extrato[e_id] - datas_razao[r_id]).days)
                    candidatos_1_1.append(
                        CandidatoAgrupamento(
                            tipo="EXATO_1_1",
                            razao_ids=(r_id,),
                            extrato_ids=(e_id,),
                            soma_centavos=razao_por_id[r_id].valor_centavos,
                            diferenca_dias_max=diff,
                        )
                    )
            ambiguidades_brutas.append(
                {
                    "estagio": "EXATO_1_1",
                    "motivo": "MULTIPLOS_CANDIDATOS_MESMO_VALOR_NA_JANELA",
                    "razao_ids": comp_r,
                    "extrato_ids": comp_e,
                    "valor_alvo_centavos": razao_por_id[comp_r[0]].valor_centavos,
                    "candidatos": tuple(candidatos_1_1),
                }
            )

    # Estágio 3: Agrupamento limitado de subconjuntos 1:N e N:1
    combinacoes_avaliadas = 0
    max_k = entrada.parametros.max_agrupamento
    orcamento = entrada.parametros.orcamento_combinacoes

    if max_k >= 2:
        r_livres_2 = [r for r in entrada.razao if r.id not in razao_usados]
        e_livres_2 = [e for e in entrada.extrato if e.id not in extrato_usados]
        candidatos_sub: list[CandidatoAgrupamento] = []

        # 3a: Candidatos 1:N (1 lançamento do razão para 2..max_k lançamentos do extrato)
        for r in sorted(r_livres_2, key=lambda x: (x.data, x.id)):
            dt_r = datas_razao[r.id]
            sinal_r = r.valor_centavos > 0
            abs_r = abs(r.valor_centavos)
            elegiveis_e = [
                e
                for e in sorted(e_livres_2, key=lambda x: (x.data, x.id))
                if (e.valor_centavos > 0) == sinal_r
                and abs(e.valor_centavos) < abs_r
                and abs((datas_extrato[e.id] - dt_r).days) <= janela
            ]
            limite_k = min(max_k, len(elegiveis_e))
            for k in range(2, limite_k + 1):
                for combo_e in combinations(elegiveis_e, k):
                    combinacoes_avaliadas += 1
                    if combinacoes_avaliadas > orcamento:
                        raise ConciliacaoError(
                            "ORCAMENTO_COMBINACOES_EXCEDIDO",
                            f"A busca de agrupamentos excedeu o orçamento de {orcamento} combinações avaliadas.",
                            path="parametros.orcamento_combinacoes",
                            detalhes={
                                "combinacoes_avaliadas": combinacoes_avaliadas,
                                "orcamento_combinacoes": orcamento,
                            },
                        )
                    soma_e = sum(item.valor_centavos for item in combo_e)
                    if soma_e == r.valor_centavos:
                        max_diff = max(abs((datas_extrato[item.id] - dt_r).days) for item in combo_e)
                        candidatos_sub.append(
                            CandidatoAgrupamento(
                                tipo="LOTE_1_N",
                                razao_ids=tuple( sorted((r.id,)) ),
                                extrato_ids=tuple(sorted(item.id for item in combo_e)),
                                soma_centavos=r.valor_centavos,
                                diferenca_dias_max=max_diff,
                            )
                        )

        # 3b: Candidatos N:1 (2..max_k lançamentos do razão para 1 lançamento do extrato)
        for e in sorted(e_livres_2, key=lambda x: (x.data, x.id)):
            dt_e = datas_extrato[e.id]
            sinal_e = e.valor_centavos > 0
            abs_e = abs(e.valor_centavos)
            elegiveis_r = [
                r
                for r in sorted(r_livres_2, key=lambda x: (x.data, x.id))
                if (r.valor_centavos > 0) == sinal_e
                and abs(r.valor_centavos) < abs_e
                and abs((dt_e - datas_razao[r.id]).days) <= janela
            ]
            limite_k = min(max_k, len(elegiveis_r))
            for k in range(2, limite_k + 1):
                for combo_r in combinations(elegiveis_r, k):
                    combinacoes_avaliadas += 1
                    if combinacoes_avaliadas > orcamento:
                        raise ConciliacaoError(
                            "ORCAMENTO_COMBINACOES_EXCEDIDO",
                            f"A busca de agrupamentos excedeu o orçamento de {orcamento} combinações avaliadas.",
                            path="parametros.orcamento_combinacoes",
                            detalhes={
                                "combinacoes_avaliadas": combinacoes_avaliadas,
                                "orcamento_combinacoes": orcamento,
                            },
                        )
                    soma_r = sum(item.valor_centavos for item in combo_r)
                    if soma_r == e.valor_centavos:
                        max_diff = max(abs((dt_e - datas_razao[item.id]).days) for item in combo_r)
                        candidatos_sub.append(
                            CandidatoAgrupamento(
                                tipo="LOTE_N_1",
                                razao_ids=tuple(sorted(item.id for item in combo_r)),
                                extrato_ids=tuple(sorted((e.id,))),
                                soma_centavos=e.valor_centavos,
                                diferenca_dias_max=max_diff,
                            )
                        )

        # Deduplicação canônica de candidatos e análise de conflitos por sobreposição de itens
        unicos_map: dict[tuple[str, tuple[str, ...], tuple[str, ...]], CandidatoAgrupamento] = {}
        for cand in candidatos_sub:
            chave = (cand.tipo, cand.razao_ids, cand.extrato_ids)
            if chave not in unicos_map:
                unicos_map[chave] = cand
        lista_cands = [unicos_map[k] for k in sorted(unicos_map)]

        n_cands = len(lista_cands)
        adj_cands: dict[int, list[int]] = {i: [] for i in range(n_cands)}
        for i in range(n_cands):
            set_r_i = set(lista_cands[i].razao_ids)
            set_e_i = set(lista_cands[i].extrato_ids)
            for j in range(i + 1, n_cands):
                if set_r_i.intersection(lista_cands[j].razao_ids) or set_e_i.intersection(
                    lista_cands[j].extrato_ids
                ):
                    adj_cands[i].append(j)
                    adj_cands[j].append(i)

        visitados_cand: set[int] = set()
        for inicio in range(n_cands):
            if inicio in visitados_cand:
                continue
            comp_indices: list[int] = []
            fila_c = [inicio]
            visitados_cand.add(inicio)
            pos = 0
            while pos < len(fila_c):
                atual = fila_c[pos]
                pos += 1
                comp_indices.append(atual)
                for viz in adj_cands[atual]:
                    if viz not in visitados_cand:
                        visitados_cand.add(viz)
                        fila_c.append(viz)
            comp_indices.sort()
            if len(comp_indices) == 1:
                cand = lista_cands[comp_indices[0]]
                for r_id in cand.razao_ids:
                    razao_usados.add(r_id)
                for e_id in cand.extrato_ids:
                    extrato_usados.add(e_id)
                datas_grupo = [razao_por_id[r_id].data for r_id in cand.razao_ids] + [
                    extrato_por_id[e_id].data for e_id in cand.extrato_ids
                ]
                menor_data = min(datas_grupo)
                ordem_tipo = 1 if cand.tipo == "LOTE_1_N" else 2
                regra_lote = "SOMA_EXATA_1_PARA_N" if cand.tipo == "LOTE_1_N" else "SOMA_EXATA_N_PARA_1"
                grupos_brutos.append(
                    {
                        "ordem_data": menor_data,
                        "ordem_chave": (ordem_tipo, menor_data, cand.razao_ids, cand.extrato_ids),
                        "tipo": cand.tipo,
                        "regra": regra_lote,
                        "razao_ids": cand.razao_ids,
                        "extrato_ids": cand.extrato_ids,
                        "ajuste_id": None,
                        "valor_razao_centavos": cand.soma_centavos,
                        "valor_extrato_centavos": cand.soma_centavos,
                        "diferenca_dias_max": cand.diferenca_dias_max,
                        "documento_comum": None,
                        "natureza_ajuste": None,
                        "conta_contabil": None,
                        "justificativa": None,
                    }
                )
            else:
                cands_comp = tuple(lista_cands[idx] for idx in comp_indices)
                r_ids_union = tuple(sorted({r_id for c in cands_comp for r_id in c.razao_ids}))
                e_ids_union = tuple(sorted({e_id for c in cands_comp for e_id in c.extrato_ids}))
                for r_id in r_ids_union:
                    razao_usados.add(r_id)
                for e_id in e_ids_union:
                    extrato_usados.add(e_id)
                ambiguidades_brutas.append(
                    {
                        "estagio": "AGRUPAMENTO_SUBCONJUNTOS",
                        "motivo": "MULTIPLOS_SUBCONJUNTOS_CONFLITANTES_MESMA_SOMA",
                        "razao_ids": r_ids_union,
                        "extrato_ids": e_ids_union,
                        "valor_alvo_centavos": cands_comp[0].soma_centavos,
                        "candidatos": cands_comp,
                    }
                )

    # Numeração canônica dos grupos conciliados
    grupos_brutos.sort(key=lambda g: g["ordem_chave"])
    grupos_conciliados: list[GrupoConciliado] = []
    for indice, g in enumerate(grupos_brutos, start=1):
        grupo_id = f"CONC-{indice:03d}"
        grupos_conciliados.append(
            GrupoConciliado(
                id=grupo_id,
                tipo=g["tipo"],
                regra=g["regra"],
                razao_ids=g["razao_ids"],
                extrato_ids=g["extrato_ids"],
                ajuste_id=g["ajuste_id"],
                valor_razao_centavos=g["valor_razao_centavos"],
                valor_extrato_centavos=g["valor_extrato_centavos"],
                diferenca_dias_max=g["diferenca_dias_max"],
                documento_comum=g["documento_comum"],
                natureza_ajuste=g["natureza_ajuste"],
                conta_contabil=g["conta_contabil"],
                justificativa=g["justificativa"],
            )
        )
        status_nome = "AJUSTE_DECLARADO" if g["tipo"] == "AJUSTE_DECLARADO" else "CONCILIADO"
        for r_id in g["razao_ids"]:
            status_razao[r_id] = (status_nome, grupo_id)
        for e_id in g["extrato_ids"]:
            status_extrato[e_id] = (status_nome, grupo_id)

    # Numeração canônica das ambiguidades preservadas
    ambiguidades_brutas.sort(key=lambda a: (a["razao_ids"], a["extrato_ids"]))
    ambiguidades: list[AmbiguidadeDetectada] = []
    for indice, a in enumerate(ambiguidades_brutas, start=1):
        amb_id = f"AMB-{indice:03d}"
        soma_r = sum(razao_por_id[r_id].valor_centavos for r_id in a["razao_ids"])
        soma_e = sum(extrato_por_id[e_id].valor_centavos for e_id in a["extrato_ids"])
        ambiguidades.append(
            AmbiguidadeDetectada(
                id=amb_id,
                estagio=a["estagio"],
                motivo=a["motivo"],
                razao_ids=a["razao_ids"],
                extrato_ids=a["extrato_ids"],
                soma_razao_centavos=soma_r,
                soma_extrato_centavos=soma_e,
                valor_alvo_centavos=a["valor_alvo_centavos"],
                candidatos=a["candidatos"],
            )
        )
        for r_id in a["razao_ids"]:
            status_razao[r_id] = ("AMBIGUO", amb_id)
        for e_id in a["extrato_ids"]:
            status_extrato[e_id] = ("AMBIGUO", amb_id)

    # Estágio 4: Pendências remanescentes de razão e de extrato
    pendentes_razao: list[ItemPendente] = []
    for r in entrada.razao:
        if r.id not in razao_usados:
            pendentes_razao.append(
                ItemPendente(
                    origem="RAZAO",
                    status="PENDENTE_RAZAO",
                    id=r.id,
                    data=r.data,
                    descricao=r.historico,
                    documento=r.documento,
                    valor_centavos=r.valor_centavos,
                    classificacao=r.categoria,
                )
            )
            status_razao[r.id] = ("PENDENTE_RAZAO", None)

    pendentes_extrato: list[ItemPendente] = []
    for e in entrada.extrato:
        if e.id not in extrato_usados:
            pendentes_extrato.append(
                ItemPendente(
                    origem="EXTRATO",
                    status="PENDENTE_EXTRATO",
                    id=e.id,
                    data=e.data,
                    descricao=e.descricao,
                    documento=e.documento,
                    valor_centavos=e.valor_centavos,
                    classificacao=e.tipo,
                )
            )
            status_extrato[e.id] = ("PENDENTE_EXTRATO", None)

    # Lista completa de status de cada lançamento na ordem de entrada
    lancamentos_status: list[StatusLancamento] = []
    for r in entrada.razao:
        st, ref = status_razao[r.id]
        lancamentos_status.append(
            StatusLancamento(
                origem="RAZAO",
                id=r.id,
                data=r.data,
                descricao=r.historico,
                documento=r.documento,
                classificacao=r.categoria,
                valor_centavos=r.valor_centavos,
                status=st,
                referencia_id=ref,
            )
        )
    for e in entrada.extrato:
        st, ref = status_extrato[e.id]
        lancamentos_status.append(
            StatusLancamento(
                origem="EXTRATO",
                id=e.id,
                data=e.data,
                descricao=e.descricao,
                documento=e.documento,
                classificacao=e.tipo,
                valor_centavos=e.valor_centavos,
                status=st,
                referencia_id=ref,
            )
        )

    # Estágio 5: Quadro de conservação exata de saldos em centavos
    soma_mov_razao = sum(r.valor_centavos for r in entrada.razao)
    soma_mov_extrato = sum(e.valor_centavos for e in entrada.extrato)

    grupos_pareados = [g for g in grupos_conciliados if g.tipo != "AJUSTE_DECLARADO"]
    grupos_ajuste = [g for g in grupos_conciliados if g.tipo == "AJUSTE_DECLARADO"]

    soma_conc_razao = sum(g.valor_razao_centavos for g in grupos_pareados)
    soma_conc_extrato = sum(g.valor_extrato_centavos for g in grupos_pareados)
    soma_ajustes = sum(g.valor_extrato_centavos for g in grupos_ajuste)

    soma_amb_razao = sum(a.soma_razao_centavos for a in ambiguidades)
    soma_amb_extrato = sum(a.soma_extrato_centavos for a in ambiguidades)
    efeito_amb = soma_amb_extrato - soma_amb_razao

    soma_pend_razao = sum(p.valor_centavos for p in pendentes_razao)
    soma_pend_extrato = sum(p.valor_centavos for p in pendentes_extrato)
    efeito_pend = soma_pend_extrato - soma_pend_razao

    dif_saldo_inicial = (
        entrada.conta.saldo_inicial_extrato_centavos - entrada.conta.saldo_inicial_razao_centavos
    )
    divergencia_bruta = (
        entrada.conta.saldo_final_extrato_centavos - entrada.conta.saldo_final_razao_centavos
    )

    saldo_razao_ajustado = entrada.conta.saldo_final_razao_centavos + soma_ajustes
    saldo_extrato_conciliado = (
        entrada.conta.saldo_final_extrato_centavos
        - dif_saldo_inicial
        - efeito_pend
        - efeito_amb
    )
    diferenca_nao_explicada = divergencia_bruta - (
        dif_saldo_inicial + soma_ajustes + efeito_pend + efeito_amb
    )
    conservacao_exata = (
        soma_conc_razao == soma_conc_extrato
        and saldo_razao_ajustado == saldo_extrato_conciliado
        and diferenca_nao_explicada == 0
        and (soma_conc_razao + soma_amb_razao + soma_pend_razao == soma_mov_razao)
        and (soma_conc_extrato + soma_ajustes + soma_amb_extrato + soma_pend_extrato == soma_mov_extrato)
    )

    quadro_saldos = QuadroSaldos(
        saldo_inicial_razao_centavos=entrada.conta.saldo_inicial_razao_centavos,
        soma_movimentos_razao_centavos=soma_mov_razao,
        saldo_final_razao_centavos=entrada.conta.saldo_final_razao_centavos,
        saldo_inicial_extrato_centavos=entrada.conta.saldo_inicial_extrato_centavos,
        soma_movimentos_extrato_centavos=soma_mov_extrato,
        saldo_final_extrato_centavos=entrada.conta.saldo_final_extrato_centavos,
        diferenca_saldo_inicial_centavos=dif_saldo_inicial,
        divergencia_bruta_saldos_centavos=divergencia_bruta,
        soma_conciliados_razao_centavos=soma_conc_razao,
        soma_conciliados_extrato_centavos=soma_conc_extrato,
        soma_ajustes_declarados_centavos=soma_ajustes,
        soma_ambiguos_razao_centavos=soma_amb_razao,
        soma_ambiguos_extrato_centavos=soma_amb_extrato,
        efeito_liquido_ambiguos_centavos=efeito_amb,
        soma_pendentes_razao_centavos=soma_pend_razao,
        soma_pendentes_extrato_centavos=soma_pend_extrato,
        efeito_liquido_pendentes_centavos=efeito_pend,
        saldo_razao_ajustado_centavos=saldo_razao_ajustado,
        saldo_extrato_conciliado_centavos=saldo_extrato_conciliado,
        diferenca_nao_explicada_centavos=diferenca_nao_explicada,
        conservacao_exata=conservacao_exata,
    )

    total_1_1 = sum(1 for g in grupos_conciliados if g.tipo == "EXATO_1_1")
    total_1_n = sum(1 for g in grupos_conciliados if g.tipo == "LOTE_1_N")
    total_n_1 = sum(1 for g in grupos_conciliados if g.tipo == "LOTE_N_1")
    total_ajustes = len(grupos_ajuste)

    resumo = ResumoConciliacao(
        total_lancamentos_razao=len(entrada.razao),
        total_lancamentos_extrato=len(entrada.extrato),
        total_grupos_conciliados=len(grupos_conciliados),
        total_exatos_1_1=total_1_1,
        total_lotes_1_n=total_1_n,
        total_lotes_n_1=total_n_1,
        total_ajustes_declarados=total_ajustes,
        total_grupos_ambiguos=len(ambiguidades),
        total_lancamentos_razao_conciliados=sum(len(g.razao_ids) for g in grupos_pareados),
        total_lancamentos_extrato_conciliados=sum(len(g.extrato_ids) for g in grupos_pareados),
        total_lancamentos_extrato_ajustados=total_ajustes,
        total_lancamentos_razao_ambiguos=sum(len(a.razao_ids) for a in ambiguidades),
        total_lancamentos_extrato_ambiguos=sum(len(a.extrato_ids) for a in ambiguidades),
        total_pendentes_razao=len(pendentes_razao),
        total_pendentes_extrato=len(pendentes_extrato),
        combinacoes_avaliadas=combinacoes_avaliadas,
    )

    return ResultadoConciliacao(
        entrada=entrada,
        resumo=resumo,
        quadro_saldos=quadro_saldos,
        grupos_conciliados=tuple(grupos_conciliados),
        ambiguidades=tuple(ambiguidades),
        pendentes_razao=tuple(pendentes_razao),
        pendentes_extrato=tuple(pendentes_extrato),
        lancamentos_status=tuple(lancamentos_status),
        _fonte_original=fonte_congelada,
    )


def reconcile_json(
    dados: bytes | str,
    *,
    janela_dias: int | None = None,
    max_agrupamento: int | None = None,
    orcamento_combinacoes: int | None = None,
) -> ResultadoConciliacao:
    """Decodifica JSON UTF-8 estrito e executa a conciliação bancária."""
    return reconcile(
        loads(dados),
        janela_dias=janela_dias,
        max_agrupamento=max_agrupamento,
        orcamento_combinacoes=orcamento_combinacoes,
    )


conciliar = reconcile
conciliar_json = reconcile_json


def load_example_data() -> dict[str, Any]:
    """Carrega o cenário sintético empacotado em data/exemplo.json."""
    recurso = resources.files("conciliacao_bancaria").joinpath("data/exemplo.json")
    return loads(recurso.read_bytes())


def reconcile_example() -> ResultadoConciliacao:
    """Executa a conciliação sobre o cenário sintético oficial empacotado."""
    return reconcile(load_example_data())
