"""Geração determinística de CSV, Markdown, painel SVG e pacote ZIP auditável com SHA256SUMS."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import os
from pathlib import Path
import secrets
import zipfile

from .core import ResultadoConciliacao, format_brl
from .errors import ConciliacaoError
from .jsoncodec import dumps, dumps_output

BUNDLE_DATA_FILES = (
    "entrada.json",
    "resultado.json",
    "conciliados.csv",
    "pendencias.csv",
    "lancamentos.csv",
    "RELATORIO.md",
    "painel-conciliacao.svg",
)
BUNDLE_ALL_FILES = BUNDLE_DATA_FILES + ("SHA256SUMS",)
_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
_CSV_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_text(valor: str | None) -> str:
    """Protege campos textuais contra CSV/Formula Injection em planilhas."""
    if not valor:
        return ""
    stripped = valor.lstrip(" ")
    if stripped.startswith(_CSV_DANGEROUS_PREFIXES) or valor.startswith(("\t", "\r", "\n")):
        return "'" + valor
    return valor


def _escape_md(valor: str | None) -> str:
    if not valor:
        return "—"
    return valor.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_conciliados_csv(resultado: ResultadoConciliacao) -> str:
    """Renderiza CSV UTF-8 (LF) com todos os grupos conciliados e ajustes declarados."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "grupo_id",
            "tipo",
            "regra",
            "razao_ids",
            "extrato_ids",
            "ajuste_id",
            "valor_razao_centavos",
            "valor_extrato_centavos",
            "diferenca_dias_max",
            "documento_comum",
            "natureza_ajuste",
            "conta_contabil",
            "justificativa",
        ]
    )
    for g in resultado.grupos_conciliados:
        writer.writerow(
            [
                sanitize_csv_text(g.id),
                sanitize_csv_text(g.tipo),
                sanitize_csv_text(g.regra),
                sanitize_csv_text(";".join(g.razao_ids)),
                sanitize_csv_text(";".join(g.extrato_ids)),
                sanitize_csv_text(g.ajuste_id or ""),
                g.valor_razao_centavos,
                g.valor_extrato_centavos,
                g.diferenca_dias_max,
                sanitize_csv_text(g.documento_comum or ""),
                sanitize_csv_text(g.natureza_ajuste or ""),
                sanitize_csv_text(g.conta_contabil or ""),
                sanitize_csv_text(g.justificativa or ""),
            ]
        )
    return buf.getvalue()


def render_pendencias_csv(resultado: ResultadoConciliacao) -> str:
    """Renderiza CSV UTF-8 (LF) com ambiguidades preservadas e lançamentos pendentes."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "registro_id",
            "categoria_pendencia",
            "origem",
            "lancamento_id",
            "data",
            "documento",
            "classificacao",
            "valor_centavos",
            "descricao",
            "detalhe",
        ]
    )
    razao_map = {r.id: r for r in resultado.entrada.razao}
    extrato_map = {e.id: e for e in resultado.entrada.extrato}

    for amb in resultado.ambiguidades:
        detalhe_cands = "; ".join(
            f"{'+'.join(c.razao_ids)}<->{'+'.join(c.extrato_ids)} ({c.soma_centavos}c, {c.diferenca_dias_max}d)"
            for c in amb.candidatos
        )
        for r_id in amb.razao_ids:
            r = razao_map[r_id]
            writer.writerow(
                [
                    sanitize_csv_text(amb.id),
                    "AMBIGUO",
                    "RAZAO",
                    sanitize_csv_text(r.id),
                    r.data,
                    sanitize_csv_text(r.documento),
                    sanitize_csv_text(r.categoria),
                    r.valor_centavos,
                    sanitize_csv_text(r.historico),
                    sanitize_csv_text(f"{amb.motivo}: {detalhe_cands}"),
                ]
            )
        for e_id in amb.extrato_ids:
            e = extrato_map[e_id]
            writer.writerow(
                [
                    sanitize_csv_text(amb.id),
                    "AMBIGUO",
                    "EXTRATO",
                    sanitize_csv_text(e.id),
                    e.data,
                    sanitize_csv_text(e.documento),
                    sanitize_csv_text(e.tipo),
                    e.valor_centavos,
                    sanitize_csv_text(e.descricao),
                    sanitize_csv_text(f"{amb.motivo}: {detalhe_cands}"),
                ]
            )

    for p in resultado.pendentes_razao:
        writer.writerow(
            [
                sanitize_csv_text(f"PEND-{p.id}"),
                "PENDENTE_RAZAO",
                "RAZAO",
                sanitize_csv_text(p.id),
                p.data,
                sanitize_csv_text(p.documento),
                sanitize_csv_text(p.classificacao),
                p.valor_centavos,
                sanitize_csv_text(p.descricao),
                "SEM_CONTRAPARTIDA_NO_EXTRATO",
            ]
        )

    for p in resultado.pendentes_extrato:
        writer.writerow(
            [
                sanitize_csv_text(f"PEND-{p.id}"),
                "PENDENTE_EXTRATO",
                "EXTRATO",
                sanitize_csv_text(p.id),
                p.data,
                sanitize_csv_text(p.documento),
                sanitize_csv_text(p.classificacao),
                p.valor_centavos,
                sanitize_csv_text(p.descricao),
                "SEM_CONTRAPARTIDA_NO_RAZAO",
            ]
        )

    return buf.getvalue()


def render_lancamentos_csv(resultado: ResultadoConciliacao) -> str:
    """Renderiza CSV UTF-8 (LF) com todos os lançamentos de razão e extrato e seus status finais."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "origem",
            "id",
            "data",
            "documento",
            "classificacao",
            "valor_centavos",
            "status",
            "referencia_id",
            "descricao",
        ]
    )
    for item in resultado.lancamentos_status:
        writer.writerow(
            [
                item.origem,
                sanitize_csv_text(item.id),
                item.data,
                sanitize_csv_text(item.documento),
                sanitize_csv_text(item.classificacao),
                item.valor_centavos,
                item.status,
                sanitize_csv_text(item.referencia_id or ""),
                sanitize_csv_text(item.descricao),
            ]
        )
    return buf.getvalue()


def render_markdown_report(resultado: ResultadoConciliacao) -> str:
    """Gera o relatório executivo e contábil em Markdown (RELATORIO.md)."""
    c = resultado.entrada.conta
    p = resultado.entrada.parametros
    r = resultado.resumo
    q = resultado.quadro_saldos

    linhas: list[str] = [
        f"# Relatório de Conciliação Bancária — {_escape_md(c.titulo)}",
        "",
        "## 1. Identificação da Conta e Parâmetros",
        "",
        "| Campo | Valor |",
        "| :--- | :--- |",
        f"| **ID da Conta** | `{_escape_md(c.id)}` |",
        f"| **Banco** | {_escape_md(c.banco)} |",
        f"| **Agência / Conta** | `{_escape_md(c.agencia)}` / `{_escape_md(c.numero_conta)}` |",
        f"| **Moeda** | `{c.moeda}` |",
        f"| **Período** | `{c.data_inicial}` a `{c.data_final}` |",
        f"| **Janela de Compensação** | `{p.janela_dias}` dia(s) corridos |",
        f"| **Agrupamento Máximo (1:N / N:1)** | até `{p.max_agrupamento}` lançamentos |",
        f"| **Orçamento de Combinações** | `{p.orcamento_combinacoes}` (avaliadas: `{r.combinacoes_avaliadas}`) |",
        "",
        "## 2. Quadro de Conciliação de Saldos (Conservação Exata em Centavos)",
        "",
        "| Componente Contábil | Centavos Exatos | Valor Formatado (BRL) |",
        "| :--- | ---: | ---: |",
        f"| Saldo Inicial do Razão | `{q.saldo_inicial_razao_centavos}` | {format_brl(q.saldo_inicial_razao_centavos)} |",
        f"| (+) Movimentos do Razão no Período | `{q.soma_movimentos_razao_centavos}` | {format_brl(q.soma_movimentos_razao_centavos)} |",
        f"| **(=) Saldo Final do Razão** | **`{q.saldo_final_razao_centavos}`** | **{format_brl(q.saldo_final_razao_centavos)}** |",
        f"| Saldo Inicial do Extrato | `{q.saldo_inicial_extrato_centavos}` | {format_brl(q.saldo_inicial_extrato_centavos)} |",
        f"| (+) Movimentos do Extrato no Período | `{q.soma_movimentos_extrato_centavos}` | {format_brl(q.soma_movimentos_extrato_centavos)} |",
        f"| **(=) Saldo Final do Extrato** | **`{q.saldo_final_extrato_centavos}`** | **{format_brl(q.saldo_final_extrato_centavos)}** |",
        f"| Divergência Bruta (Extrato − Razão) | `{q.divergencia_bruta_saldos_centavos}` | {format_brl(q.divergencia_bruta_saldos_centavos)} |",
        f"| Parcela 1: Diferença de Saldo Inicial | `{q.diferenca_saldo_inicial_centavos}` | {format_brl(q.diferenca_saldo_inicial_centavos)} |",
        f"| Parcela 2: Ajustes Declarados no Extrato | `{q.soma_ajustes_declarados_centavos}` | {format_brl(q.soma_ajustes_declarados_centavos)} |",
        f"| Parcela 3: Efeito Líquido de Pendentes (Extrato − Razão) | `{q.efeito_liquido_pendentes_centavos}` | {format_brl(q.efeito_liquido_pendentes_centavos)} |",
        f"| Parcela 4: Efeito Líquido de Ambíguos (Extrato − Razão) | `{q.efeito_liquido_ambiguos_centavos}` | {format_brl(q.efeito_liquido_ambiguos_centavos)} |",
        f"| **Saldo do Razão Ajustado** | **`{q.saldo_razao_ajustado_centavos}`** | **{format_brl(q.saldo_razao_ajustado_centavos)}** |",
        f"| **Saldo do Extrato Conciliado** | **`{q.saldo_extrato_conciliado_centavos}`** | **{format_brl(q.saldo_extrato_conciliado_centavos)}** |",
        f"| **Diferença Não Explicada** | **`{q.diferenca_nao_explicada_centavos}`** | **{format_brl(q.diferenca_nao_explicada_centavos)}** |",
        f"| **Conservação Exata Verificada** | `{'SIM' if q.conservacao_exata else 'NAO'}` | `{'OK' if q.conservacao_exata else 'FALHA'}` |",
        "",
        "## 3. Resumo de Classificação",
        "",
        "| Indicador | Quantidade |",
        "| :--- | ---: |",
        f"| Lançamentos no Razão | `{r.total_lancamentos_razao}` |",
        f"| Lançamentos no Extrato | `{r.total_lancamentos_extrato}` |",
        f"| Grupos Conciliados (Total) | `{r.total_grupos_conciliados}` |",
        f"| — Casamentos Exatos 1:1 (`EXATO_1_1`) | `{r.total_exatos_1_1}` |",
        f"| — Lotes 1 Razão : N Extrato (`LOTE_1_N`) | `{r.total_lotes_1_n}` |",
        f"| — Consolidações N Razão : 1 Extrato (`LOTE_N_1`) | `{r.total_lotes_n_1}` |",
        f"| — Ajustes Declarados (`AJUSTE_DECLARADO`) | `{r.total_ajustes_declarados}` |",
        f"| Grupos Ambíguos Preservados (`AMBIGUO`) | `{r.total_grupos_ambiguos}` |",
        f"| Pendências do Razão (`PENDENTE_RAZAO`) | `{r.total_pendentes_razao}` |",
        f"| Pendências do Extrato (`PENDENTE_EXTRATO`) | `{r.total_pendentes_extrato}` |",
        "",
        "## 4. Grupos Conciliados e Ajustes Declarados",
        "",
        "| Grupo | Tipo | Regra | Razão IDs | Extrato IDs | Valor Razão | Valor Extrato | Defasagem Máx. | Conta Contábil / Observação |",
        "| :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | :--- |",
    ]

    for g in resultado.grupos_conciliados:
        r_ids = ", ".join(f"`{x}`" for x in g.razao_ids) if g.razao_ids else "—"
        e_ids = ", ".join(f"`{x}`" for x in g.extrato_ids) if g.extrato_ids else "—"
        obs = (
            f"{_escape_md(g.natureza_ajuste)} • {_escape_md(g.conta_contabil)}"
            if g.tipo == "AJUSTE_DECLARADO"
            else (_escape_md(g.documento_comum) if g.documento_comum else "—")
        )
        linhas.append(
            f"| `{g.id}` | `{g.tipo}` | `{g.regra}` | {r_ids} | {e_ids} | "
            f"{format_brl(g.valor_razao_centavos)} | {format_brl(g.valor_extrato_centavos)} | "
            f"`{g.diferenca_dias_max}d` | {obs} |"
        )

    linhas.extend(
        [
            "",
            "## 5. Ambiguidades Preservadas (Sem Escolha Silenciosa)",
            "",
        ]
    )
    if not resultado.ambiguidades:
        linhas.append("Nenhuma ambiguidade detectada no período.")
    else:
        linhas.extend(
            [
                "| ID | Estágio | Motivo | Razão Envolvidos | Extrato Envolvidos | Valor Alvo | Candidatos Empatados |",
                "| :--- | :--- | :--- | :--- | :--- | ---: | :--- |",
            ]
        )
        for amb in resultado.ambiguidades:
            r_ids = ", ".join(f"`{x}`" for x in amb.razao_ids)
            e_ids = ", ".join(f"`{x}`" for x in amb.extrato_ids)
            cands_txt = "; ".join(
                f"`{'+'.join(c.razao_ids)} ↔ {'+'.join(c.extrato_ids)}` ({format_brl(c.soma_centavos)}, {c.diferenca_dias_max}d)"
                for c in amb.candidatos
            )
            linhas.append(
                f"| `{amb.id}` | `{amb.estagio}` | `{amb.motivo}` | {r_ids} | {e_ids} | "
                f"{format_brl(amb.valor_alvo_centavos)} | {cands_txt} |"
            )

    linhas.extend(
        [
            "",
            "## 6. Pendências em Aberto (Razão e Extrato)",
            "",
        ]
    )
    todas_pends = list(resultado.pendentes_razao) + list(resultado.pendentes_extrato)
    if not todas_pends:
        linhas.append("Nenhuma pendência em aberto no período.")
    else:
        linhas.extend(
            [
                "| Status | Origem | ID | Data | Documento | Classificação | Valor (BRL) | Centavos | Descrição |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | :--- |",
            ]
        )
        for item in todas_pends:
            linhas.append(
                f"| `{item.status}` | `{item.origem}` | `{item.id}` | `{item.data}` | "
                f"`{_escape_md(item.documento)}` | `{_escape_md(item.classificacao)}` | "
                f"{format_brl(item.valor_centavos)} | `{item.valor_centavos}` | {_escape_md(item.descricao)} |"
            )

    linhas.append("")
    return "\n".join(linhas)


def _xml(texto: str) -> str:
    return html.escape(texto, quote=True)


def _trunc(texto: str, limite: int) -> str:
    if len(texto) <= limite:
        return texto
    return texto[: limite - 1].rstrip() + "…"


def render_svg_panel(resultado: ResultadoConciliacao) -> str:
    """Renderiza painel visual SVG determinístico e autocontido (painel-conciliacao.svg)."""
    c = resultado.entrada.conta
    p = resultado.entrada.parametros
    r = resultado.resumo
    q = resultado.quadro_saldos

    width = 1280
    height = 860

    badge_cor = "#065f46" if q.conservacao_exata else "#991b1b"
    badge_bg = "#d1fae5" if q.conservacao_exata else "#fee2e2"
    badge_txt = (
        "CONSERVAÇÃO EXATA • 0 CENTAVOS NÃO EXPLICADOS"
        if q.conservacao_exata
        else "ATENÇÃO: DIVERGÊNCIA NÃO EXPLICADA"
    )

    partes: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Painel de Conciliação Bancária">',
        "  <defs>",
        '    <linearGradient id="headerGrad" x1="0%" y1="0%" x2="100%" y2="0%">',
        '      <stop offset="0%" stop-color="#0f172a" />',
        '      <stop offset="100%" stop-color="#1e293b" />',
        "    </linearGradient>",
        '    <filter id="cardShadow" x="-2%" y="-2%" width="104%" height="108%">',
        '      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.07" />',
        "    </filter>",
        "  </defs>",
        f'  <rect width="{width}" height="{height}" fill="#f8fafc" />',
        '  <g font-family="Inter, Segoe UI, Roboto, Helvetica, Arial, sans-serif">',
        # Header
        '    <rect x="0" y="0" width="1280" height="96" fill="url(#headerGrad)" />',
        f'    <text x="36" y="40" fill="#f8fafc" font-size="22" font-weight="700">{_xml(_trunc(c.titulo, 68))}</text>',
        f'    <text x="36" y="68" fill="#94a3b8" font-size="13">{_xml(c.banco)} • Ag. {_xml(c.agencia)} • Conta {_xml(c.numero_conta)} • Período {_xml(c.data_inicial)} a {_xml(c.data_final)} • Janela {p.janela_dias}d (máx. lote {p.max_agrupamento})</text>',
        f'    <rect x="854" y="26" width="390" height="42" rx="21" fill="{badge_bg}" />',
        f'    <text x="1049" y="52" text-anchor="middle" fill="{badge_cor}" font-size="12" font-weight="700">{_xml(badge_txt)}</text>',
    ]

    # 4 KPI Cards (y=116, height=112)
    kpis = [
        (
            "SALDO FINAL DO RAZÃO",
            format_brl(q.saldo_final_razao_centavos),
            f"Inicial: {format_brl(q.saldo_inicial_razao_centavos)} | Ajustado: {format_brl(q.saldo_razao_ajustado_centavos)}",
            "#0f172a",
            "#2563eb",
        ),
        (
            "SALDO FINAL DO EXTRATO",
            format_brl(q.saldo_final_extrato_centavos),
            f"Dif. bruta: {format_brl(q.divergencia_bruta_saldos_centavos)} | Conc.: {format_brl(q.saldo_extrato_conciliado_centavos)}",
            "#0f172a",
            "#0891b2",
        ),
        (
            "CONCILIADOS E AJUSTES",
            f"{r.total_grupos_conciliados} grupos",
            f"1:1 ({r.total_exatos_1_1}) • 1:N ({r.total_lotes_1_n}) • N:1 ({r.total_lotes_n_1}) • Ajustes ({r.total_ajustes_declarados})",
            "#065f46",
            "#059669",
        ),
        (
            "EXCEÇÕES OPERACIONAIS",
            f"{r.total_grupos_ambiguos} amb. / {r.total_pendentes_razao + r.total_pendentes_extrato} pend.",
            f"Razão: {format_brl(q.soma_pendentes_razao_centavos)} | Extrato: {format_brl(q.soma_pendentes_extrato_centavos)}",
            "#9a3412" if (r.total_grupos_ambiguos or r.total_pendentes_razao or r.total_pendentes_extrato) else "#065f46",
            "#d97706",
        ),
    ]

    for idx, (titulo, valor, sub, cor_valor, cor_barra) in enumerate(kpis):
        x = 36 + idx * 306
        partes.extend(
            [
                f'    <g filter="url(#cardShadow)">',
                f'      <rect x="{x}" y="116" width="290" height="112" rx="10" fill="#ffffff" stroke="#e2e8f0" />',
                f'      <rect x="{x}" y="116" width="6" height="112" rx="3" fill="{cor_barra}" />',
                f'      <text x="{x + 20}" y="142" fill="#64748b" font-size="11" font-weight="700" letter-spacing="0.5">{_xml(titulo)}</text>',
                f'      <text x="{x + 20}" y="180" fill="{cor_valor}" font-size="24" font-weight="700">{_xml(valor)}</text>',
                f'      <text x="{x + 20}" y="208" fill="#475569" font-size="11">{_xml(_trunc(sub, 46))}</text>',
                "    </g>",
            ]
        )

    # Barra de distribuição de lançamentos (y=244, height=92)
    total_itens = max(1, r.total_lancamentos_razao + r.total_lancamentos_extrato)
    qtd_conc = r.total_lancamentos_razao_conciliados + r.total_lancamentos_extrato_conciliados
    qtd_aj = r.total_lancamentos_extrato_ajustados
    qtd_amb = r.total_lancamentos_razao_ambiguos + r.total_lancamentos_extrato_ambiguos
    qtd_pend = r.total_pendentes_razao + r.total_pendentes_extrato

    largura_util = 1160
    w_conc = round(largura_util * qtd_conc / total_itens)
    w_aj = round(largura_util * qtd_aj / total_itens)
    w_amb = round(largura_util * qtd_amb / total_itens)
    w_pend = max(0, largura_util - w_conc - w_aj - w_amb)

    x_cur = 60
    partes.extend(
        [
            '    <g filter="url(#cardShadow)">',
            '      <rect x="36" y="244" width="1208" height="92" rx="10" fill="#ffffff" stroke="#e2e8f0" />',
            f'      <text x="60" y="270" fill="#0f172a" font-size="13" font-weight="700">DISTRIBUIÇÃO DOS {r.total_lancamentos_razao + r.total_lancamentos_extrato} LANÇAMENTOS ({r.total_lancamentos_razao} RAZÃO + {r.total_lancamentos_extrato} EXTRATO)</text>',
            f'      <text x="1220" y="270" text-anchor="end" fill="#475569" font-size="12">Volume conciliado: {_xml(format_brl(q.soma_conciliados_extrato_centavos))} • Combinações avaliadas: {r.combinacoes_avaliadas}/{p.orcamento_combinacoes}</text>',
        ]
    )
    segmentos = [
        (w_conc, "#10b981", f"Conciliados ({qtd_conc})"),
        (w_aj, "#3b82f6", f"Ajustes Declarados ({qtd_aj})"),
        (w_amb, "#f59e0b", f"Ambíguos ({qtd_amb})"),
        (w_pend, "#ef4444", f"Pendentes ({qtd_pend})"),
    ]
    for w_seg, cor_seg, _rotulo in segmentos:
        if w_seg > 0:
            partes.append(
                f'      <rect x="{x_cur}" y="282" width="{w_seg}" height="20" fill="{cor_seg}" />'
            )
            x_cur += w_seg

    lx = 60
    for _w_seg, cor_seg, rotulo in segmentos:
        partes.extend(
            [
                f'      <rect x="{lx}" y="312" width="10" height="10" rx="2" fill="{cor_seg}" />',
                f'      <text x="{lx + 16}" y="321" fill="#334155" font-size="11" font-weight="600">{_xml(rotulo)}</text>',
            ]
        )
        lx += 240
    partes.append("    </g>")

    # Coluna Esquerda: Grupos Conciliados e Ajustes (x=36, y=352, width=710, height=472)
    partes.extend(
        [
            '    <g filter="url(#cardShadow)">',
            '      <rect x="36" y="352" width="710" height="472" rx="10" fill="#ffffff" stroke="#e2e8f0" />',
            '      <text x="60" y="382" fill="#0f172a" font-size="15" font-weight="700">GRUPOS CONCILIADOS E AJUSTES DECLARADOS</text>',
            '      <text x="722" y="382" text-anchor="end" fill="#64748b" font-size="11">Casamento 1:1, Lotes 1:N, Consolidações N:1 e Tarifas</text>',
            '      <rect x="52" y="396" width="678" height="28" rx="4" fill="#f1f5f9" />',
            '      <text x="64" y="414" fill="#475569" font-size="11" font-weight="700">GRUPO</text>',
            '      <text x="150" y="414" fill="#475569" font-size="11" font-weight="700">TIPO</text>',
            '      <text x="285" y="414" fill="#475569" font-size="11" font-weight="700">VÍNCULO RAZÃO ↔ EXTRATO</text>',
            '      <text x="560" y="414" fill="#475569" font-size="11" font-weight="700">DEFASAGEM</text>',
            '      <text x="716" y="414" text-anchor="end" fill="#475569" font-size="11" font-weight="700">VALOR EXATO</text>',
        ]
    )

    cores_tipo = {
        "EXATO_1_1": ("#d1fae5", "#065f46"),
        "LOTE_1_N": ("#e0e7ff", "#3730a3"),
        "LOTE_N_1": ("#fae8ff", "#86198f"),
        "AJUSTE_DECLARADO": ("#dbeafe", "#1e40af"),
    }

    for idx, g in enumerate(resultado.grupos_conciliados[:9]):
        ry = 446 + idx * 40
        bg_t, fg_t = cores_tipo.get(g.tipo, ("#f1f5f9", "#334155"))
        r_str = "+".join(g.razao_ids) if g.razao_ids else f"Ajuste {g.ajuste_id}"
        e_str = "+".join(g.extrato_ids)
        vinculo = f"{r_str} ↔ {e_str}"
        val_exibir = (
            g.valor_extrato_centavos if g.tipo == "AJUSTE_DECLARADO" else g.valor_razao_centavos
        )
        cor_v = "#047857" if val_exibir >= 0 else "#b91c1c"
        if idx > 0:
            partes.append(
                f'      <line x1="52" y1="{ry - 24}" x2="730" y2="{ry - 24}" stroke="#f1f5f9" stroke-width="1" />'
            )
        partes.extend(
            [
                f'      <text x="64" y="{ry}" fill="#0f172a" font-size="12" font-weight="700">{_xml(g.id)}</text>',
                f'      <rect x="146" y="{ry - 14}" width="124" height="20" rx="4" fill="{bg_t}" />',
                f'      <text x="208" y="{ry}" text-anchor="middle" fill="{fg_t}" font-size="10" font-weight="700">{_xml(g.tipo)}</text>',
                f'      <text x="285" y="{ry}" fill="#334155" font-size="12">{_xml(_trunc(vinculo, 34))}</text>',
                f'      <text x="585" y="{ry}" text-anchor="middle" fill="#64748b" font-size="12">{g.diferenca_dias_max}d</text>',
                f'      <text x="716" y="{ry}" text-anchor="end" fill="{cor_v}" font-size="12" font-weight="700">{_xml(format_brl(val_exibir))}</text>',
            ]
        )
    partes.append("    </g>")

    # Coluna Direita Superior: Ambiguidades Preservadas (x=762, y=352, width=482, height=168)
    partes.extend(
        [
            '    <g filter="url(#cardShadow)">',
            '      <rect x="762" y="352" width="482" height="168" rx="10" fill="#fffbeb" stroke="#fde68a" />',
            '      <text x="784" y="380" fill="#92400e" font-size="14" font-weight="700">AMBIGUIDADES PRESERVADAS (SEM ESCOLHA ARBITRÁRIA)</text>',
        ]
    )
    if not resultado.ambiguidades:
        partes.append(
            '      <text x="784" y="418" fill="#78350f" font-size="12">Nenhuma ambiguidade detectada neste lote.</text>'
        )
    else:
        for idx, amb in enumerate(resultado.ambiguidades[:2]):
            ay = 410 + idx * 54
            cands_resumo = " | ".join(
                f"{'+'.join(cd.razao_ids)} ↔ {'+'.join(cd.extrato_ids)} ({cd.diferenca_dias_max}d)"
                for cd in amb.candidatos[:3]
            )
            partes.extend(
                [
                    f'      <rect x="784" y="{ay - 16}" width="438" height="46" rx="6" fill="#ffffff" stroke="#fcd34d" />',
                    f'      <text x="796" y="{ay + 2}" fill="#92400e" font-size="12" font-weight="700">{_xml(amb.id)} ({_xml(amb.estagio)}) • Alvo: {_xml(format_brl(amb.valor_alvo_centavos))}</text>',
                    f'      <text x="796" y="{ay + 20}" fill="#78350f" font-size="11">Candidatos empatados: {_xml(_trunc(cands_resumo, 55))}</text>',
                ]
            )
    partes.append("    </g>")

    # Coluna Direita Intermediária: Pendências de Razão e Extrato (x=762, y=534, width=482, height=150)
    partes.extend(
        [
            '    <g filter="url(#cardShadow)">',
            '      <rect x="762" y="534" width="482" height="150" rx="10" fill="#ffffff" stroke="#e2e8f0" />',
            '      <text x="784" y="562" fill="#0f172a" font-size="13" font-weight="700">LANÇAMENTOS PENDENTES (RAZÃO E EXTRATO)</text>',
        ]
    )
    todas_pends = list(resultado.pendentes_razao) + list(resultado.pendentes_extrato)
    if not todas_pends:
        partes.append(
            '      <text x="784" y="598" fill="#475569" font-size="12">Nenhuma pendência em aberto.</text>'
        )
    else:
        for idx, item in enumerate(todas_pends[:3]):
            py = 592 + idx * 32
            cor_tag = "#fee2e2" if item.origem == "RAZAO" else "#fef3c7"
            txt_tag = "#991b1b" if item.origem == "RAZAO" else "#92400e"
            partes.extend(
                [
                    f'      <rect x="784" y="{py - 14}" width="72" height="20" rx="4" fill="{cor_tag}" />',
                    f'      <text x="820" y="{py}" text-anchor="middle" fill="{txt_tag}" font-size="10" font-weight="700">{_xml(item.origem)}</text>',
                    f'      <text x="866" y="{py}" fill="#0f172a" font-size="11" font-weight="700">{_xml(item.id)} ({_xml(item.data)})</text>',
                    f'      <text x="980" y="{py}" fill="#475569" font-size="11">{_xml(_trunc(item.descricao, 22))}</text>',
                    f'      <text x="1222" y="{py}" text-anchor="end" fill="#0f172a" font-size="11" font-weight="700">{_xml(format_brl(item.valor_centavos))}</text>',
                ]
            )
    partes.append("    </g>")

    # Coluna Direita Inferior: Ponte Algébrica de Saldos (x=762, y=698, width=482, height=126)
    partes.extend(
        [
            '    <g filter="url(#cardShadow)">',
            '      <rect x="762" y="698" width="482" height="126" rx="10" fill="#0f172a" />',
            '      <text x="784" y="726" fill="#38bdf8" font-size="12" font-weight="700">PONTE ALGÉBRICA DE CONSERVAÇÃO (EXTRATO − RAZÃO)</text>',
            f'      <text x="784" y="750" fill="#e2e8f0" font-size="11">Divergência Bruta Final: {_xml(format_brl(q.divergencia_bruta_saldos_centavos))} ({q.divergencia_bruta_saldos_centavos} centavos)</text>',
            f'      <text x="784" y="772" fill="#94a3b8" font-size="11">= Ajustes ({_xml(format_brl(q.soma_ajustes_declarados_centavos))}) + Pendências ({_xml(format_brl(q.efeito_liquido_pendentes_centavos))}) + Ambíguos ({_xml(format_brl(q.efeito_liquido_ambiguos_centavos))})</text>',
            f'      <text x="784" y="798" fill="#34d399" font-size="12" font-weight="700">Saldo Conciliado: {_xml(format_brl(q.saldo_razao_ajustado_centavos))} • Resíduo: {q.diferenca_nao_explicada_centavos} centavos</text>',
            "    </g>",
            "  </g>",
            "</svg>\n",
        ]
    )

    return "\n".join(partes)


def build_bundle_files(resultado: ResultadoConciliacao) -> dict[str, bytes]:
    """Constrói em memória todos os artefatos do pacote de conciliação e o manifesto SHA256SUMS."""
    arquivos: dict[str, bytes] = {
        "entrada.json": dumps(resultado.source_document()).encode("utf-8"),
        "resultado.json": dumps_output(resultado.to_dict()).encode("utf-8"),
        "conciliados.csv": render_conciliados_csv(resultado).encode("utf-8"),
        "pendencias.csv": render_pendencias_csv(resultado).encode("utf-8"),
        "lancamentos.csv": render_lancamentos_csv(resultado).encode("utf-8"),
        "RELATORIO.md": render_markdown_report(resultado).encode("utf-8"),
        "painel-conciliacao.svg": render_svg_panel(resultado).encode("utf-8"),
    }
    linhas_sha: list[str] = []
    for nome in BUNDLE_DATA_FILES:
        digest = hashlib.sha256(arquivos[nome]).hexdigest()
        linhas_sha.append(f"{digest}  {nome}")
    arquivos["SHA256SUMS"] = ("\n".join(linhas_sha) + "\n").encode("utf-8")
    return arquivos


def _validate_output_path(destino: str | Path) -> Path:
    if isinstance(destino, str):
        if not destino.strip() or "\x00" in destino:
            raise ConciliacaoError(
                "CAMINHO_INVALIDO",
                "Caminho de saída inválido ou vazio.",
                path="saida",
            )
        caminho = Path(destino)
    elif isinstance(destino, Path):
        texto = str(destino)
        if not texto.strip() or "\x00" in texto:
            raise ConciliacaoError(
                "CAMINHO_INVALIDO",
                "Caminho de saída inválido ou vazio.",
                path="saida",
            )
        caminho = destino
    else:
        raise ConciliacaoError("CAMINHO_INVALIDO", "Caminho de saída deve ser str ou Path.", path="saida")

    if caminho.exists() or caminho.is_symlink():
        raise ConciliacaoError(
            "SAIDA_EXISTENTE",
            f"O caminho de saída já existe e não será sobrescrito: {caminho.name}.",
            path="saida",
        )
    return caminho


def publish_file_bytes(destino: str | Path, conteudo: bytes) -> Path:
    """Publica bytes em arquivo novo de forma atômica sem sobrescrever destino existente."""
    caminho = _validate_output_path(destino)
    pai = caminho.parent if str(caminho.parent) != "" else Path(".")
    if not pai.exists() or not pai.is_dir():
        raise ConciliacaoError(
            "CAMINHO_INVALIDO",
            "O diretório pai do arquivo de saída não existe.",
            path="saida",
        )

    tmp_nome = f".tmp-{caminho.name}-{os.getpid()}-{secrets.token_hex(8)}"
    tmp_caminho = pai / tmp_nome
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(tmp_caminho, flags, 0o644)
    try:
        view = memoryview(conteudo)
        escrito = 0
        while escrito < len(view):
            escrito += os.write(fd, view[escrito:])
        os.fsync(fd)
    finally:
        os.close(fd)

    try:
        try:
            os.link(tmp_caminho, caminho)
        except FileExistsError:
            raise ConciliacaoError(
                "SAIDA_EXISTENTE",
                f"O caminho de saída já existe e não será sobrescrito: {caminho.name}.",
                path="saida",
            ) from None
        except OSError:
            if caminho.exists() or caminho.is_symlink():
                raise ConciliacaoError(
                    "SAIDA_EXISTENTE",
                    f"O caminho de saída já existe e não será sobrescrito: {caminho.name}.",
                    path="saida",
                ) from None
            os.replace(tmp_caminho, caminho)
    finally:
        if tmp_caminho.exists():
            try:
                os.unlink(tmp_caminho)
            except OSError:
                pass

    return caminho


def build_bundle_zip_bytes(resultado: ResultadoConciliacao) -> bytes:
    """Constrói os bytes determinísticos do arquivo ZIP de conciliação."""
    arquivos = build_bundle_files(resultado)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for nome in BUNDLE_ALL_FILES:
            info = zipfile.ZipInfo(filename=nome, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            zf.writestr(info, arquivos[nome])
    return buffer.getvalue()


def write_bundle_zip(resultado: ResultadoConciliacao, destino: str | Path) -> Path:
    """Escreve o pacote ZIP determinístico de conciliação sem sobrescrever arquivos existentes."""
    zip_bytes = build_bundle_zip_bytes(resultado)
    return publish_file_bytes(destino, zip_bytes)


def write_bundle_directory(resultado: ResultadoConciliacao, destino: str | Path) -> Path:
    """Cria um novo diretório de saída e publica todos os artefatos e SHA256SUMS."""
    caminho = _validate_output_path(destino)
    pai = caminho.parent if str(caminho.parent) != "" else Path(".")
    if not pai.exists() or not pai.is_dir():
        raise ConciliacaoError(
            "CAMINHO_INVALIDO",
            "O diretório pai da pasta de saída não existe.",
            path="saida",
        )
    try:
        os.mkdir(caminho, 0o755)
    except FileExistsError:
        raise ConciliacaoError(
            "SAIDA_EXISTENTE",
            f"O diretório de saída já existe e não será sobrescrito: {caminho.name}.",
            path="saida",
        ) from None

    arquivos = build_bundle_files(resultado)
    for nome in BUNDLE_ALL_FILES:
        publish_file_bytes(caminho / nome, arquivos[nome])
    return caminho


def verify_bundle_zip(caminho_zip: str | Path) -> dict[str, str]:
    """Valida a integridade estrutural e os hashes SHA-256 de um pacote ZIP de conciliação."""
    caminho = Path(caminho_zip)
    if not caminho.is_file():
        raise ConciliacaoError("ZIP_INVALIDO", "Arquivo ZIP não encontrado.", path=str(caminho.name))
    try:
        with zipfile.ZipFile(caminho, mode="r") as zf:
            infos = zf.infolist()
            nomes = [info.filename for info in infos]
            if len(nomes) != len(set(nomes)):
                raise ConciliacaoError("ZIP_INVALIDO", "Pacote ZIP contém entradas duplicadas.")
            for nome in nomes:
                if nome.startswith(("/", "\\")) or ".." in Path(nome).parts or "\\" in nome:
                    raise ConciliacaoError("ZIP_INVALIDO", f"Entrada ZIP com caminho inseguro: {nome!r}.")
            esperados = set(BUNDLE_ALL_FILES)
            if set(nomes) != esperados:
                raise ConciliacaoError(
                    "ZIP_INVALIDO",
                    "Conjunto de arquivos no ZIP difere do manifesto esperado.",
                )
            sha_texto = zf.read("SHA256SUMS").decode("utf-8", "strict")
            mapa_sha: dict[str, str] = {}
            for linha in sha_texto.splitlines():
                if not linha.strip():
                    continue
                partes = linha.split("  ", 1)
                if len(partes) != 2 or len(partes[0]) != 64:
                    raise ConciliacaoError("ZIP_INVALIDO", "Formato inválido em SHA256SUMS.")
                digest_decl, arq_nome = partes
                mapa_sha[arq_nome] = digest_decl

            if set(mapa_sha.keys()) != set(BUNDLE_DATA_FILES):
                raise ConciliacaoError(
                    "ZIP_INVALIDO",
                    "SHA256SUMS não lista exatamente os arquivos de dados do pacote.",
                )
            for arq_nome in BUNDLE_DATA_FILES:
                real_digest = hashlib.sha256(zf.read(arq_nome)).hexdigest()
                if real_digest != mapa_sha[arq_nome]:
                    raise ConciliacaoError(
                        "ZIP_INVALIDO",
                        f"Divergência de hash SHA-256 para {arq_nome}.",
                    )
            return mapa_sha
    except ConciliacaoError:
        raise
    except (zipfile.BadZipFile, UnicodeError, OSError):
        raise ConciliacaoError("ZIP_INVALIDO", "Arquivo ZIP corrompido ou ilegível.") from None
