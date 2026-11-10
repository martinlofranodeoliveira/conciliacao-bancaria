#!/usr/bin/env python3
"""Verificador independente (SQLite + Decimal/int) sem importar conciliacao_bancaria."""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _reject_float(_token: str) -> None:
    raise AssertionError("Float detectado no JSON; apenas inteiros são permitidos.")


def _load_json_strict(raw: bytes) -> dict:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("BOM UTF-8 detectado.")
    text = raw.decode("utf-8", "strict")
    return json.loads(text, parse_int=int, parse_float=_reject_float)


def _cents_to_brl_decimal(cents: int) -> Decimal:
    return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))


def verify_zip_bundle_with_sqlite(zip_path: Path) -> dict[str, object]:
    """Audita um pacote ZIP de conciliação usando SQLite em memória e Decimal."""
    assert "conciliacao_bancaria" not in sys.modules, "O verificador não pode importar conciliacao_bancaria"

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        expected = [
            "entrada.json",
            "resultado.json",
            "conciliados.csv",
            "pendencias.csv",
            "lancamentos.csv",
            "RELATORIO.md",
            "painel-conciliacao.svg",
            "SHA256SUMS",
        ]
        if names != expected:
            raise AssertionError(f"Lista de arquivos no ZIP inesperada: {names} != {expected}")

        sha_lines = zf.read("SHA256SUMS").decode("utf-8", "strict").strip().splitlines()
        if len(sha_lines) != 7:
            raise AssertionError("SHA256SUMS deve conter exatamente 7 linhas.")
        for line in sha_lines:
            digest, fname = line.split("  ", 1)
            actual = hashlib.sha256(zf.read(fname)).hexdigest()
            if actual != digest:
                raise AssertionError(f"Hash SHA-256 divergente para {fname}: {actual} != {digest}")

        entrada = _load_json_strict(zf.read("entrada.json"))
        resultado = _load_json_strict(zf.read("resultado.json"))
        conciliados_rows = list(
            csv.DictReader(io.StringIO(zf.read("conciliados.csv").decode("utf-8", "strict")))
        )
        pendencias_rows = list(
            csv.DictReader(io.StringIO(zf.read("pendencias.csv").decode("utf-8", "strict")))
        )
        lancamentos_rows = list(
            csv.DictReader(io.StringIO(zf.read("lancamentos.csv").decode("utf-8", "strict")))
        )
        svg_text = zf.read("painel-conciliacao.svg").decode("utf-8", "strict")
        md_text = zf.read("RELATORIO.md").decode("utf-8", "strict")

    if not svg_text.startswith("<svg ") or "</svg>" not in svg_text:
        raise AssertionError("painel-conciliacao.svg inválido.")
    if "# Relatório de Conciliação Bancária" not in md_text:
        raise AssertionError("RELATORIO.md sem cabeçalho esperado.")

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE razao (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            historico TEXT NOT NULL,
            documento TEXT NOT NULL,
            valor_centavos INTEGER NOT NULL,
            categoria TEXT NOT NULL
        );
        CREATE TABLE extrato (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            descricao TEXT NOT NULL,
            documento TEXT NOT NULL,
            valor_centavos INTEGER NOT NULL,
            tipo TEXT NOT NULL
        );
        CREATE TABLE ajustes (
            id TEXT PRIMARY KEY,
            extrato_id TEXT NOT NULL UNIQUE,
            natureza TEXT NOT NULL,
            conta_contabil TEXT NOT NULL
        );
        CREATE TABLE lancamentos_status (
            origem TEXT NOT NULL,
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            valor_centavos INTEGER NOT NULL,
            status TEXT NOT NULL,
            referencia_id TEXT NOT NULL
        );
        """
    )

    for r in entrada["razao"]:
        cur.execute(
            "INSERT INTO razao VALUES (?, ?, ?, ?, ?, ?)",
            (r["id"], r["data"], r["historico"], r["documento"], r["valor_centavos"], r["categoria"]),
        )
    for e in entrada["extrato"]:
        cur.execute(
            "INSERT INTO extrato VALUES (?, ?, ?, ?, ?, ?)",
            (e["id"], e["data"], e["descricao"], e["documento"], e["valor_centavos"], e["tipo"]),
        )
    for a in entrada.get("ajustes_declarados", []):
        cur.execute(
            "INSERT INTO ajustes VALUES (?, ?, ?, ?)",
            (a["id"], a["extrato_id"], a["natureza"], a["conta_contabil"]),
        )
    for row in lancamentos_rows:
        cur.execute(
            "INSERT INTO lancamentos_status VALUES (?, ?, ?, ?, ?, ?)",
            (
                row["origem"],
                row["id"],
                row["data"],
                int(row["valor_centavos"]),
                row["status"],
                row["referencia_id"],
            ),
        )
    conn.commit()

    # 1. Verificação SQL dos saldos de entrada
    soma_razao_sql = cur.execute("SELECT COALESCE(SUM(valor_centavos), 0) FROM razao").fetchone()[0]
    soma_extrato_sql = cur.execute("SELECT COALESCE(SUM(valor_centavos), 0) FROM extrato").fetchone()[0]
    conta = entrada["conta"]
    if conta["saldo_inicial_razao_centavos"] + soma_razao_sql != conta["saldo_final_razao_centavos"]:
        raise AssertionError("Saldo final do razão não fecha no SQLite.")
    if conta["saldo_inicial_extrato_centavos"] + soma_extrato_sql != conta["saldo_final_extrato_centavos"]:
        raise AssertionError("Saldo final do extrato não fecha no SQLite.")

    # 2. Verificação de bijeção entre entrada e lancamentos.csv
    qtd_r_status = cur.execute("SELECT COUNT(*) FROM lancamentos_status WHERE origem = 'RAZAO'").fetchone()[0]
    qtd_e_status = cur.execute("SELECT COUNT(*) FROM lancamentos_status WHERE origem = 'EXTRATO'").fetchone()[0]
    if qtd_r_status != len(entrada["razao"]) or qtd_e_status != len(entrada["extrato"]):
        raise AssertionError("Quantidade em lancamentos.csv diverge da entrada.")

    # 3. Verificação de cada grupo conciliado em conciliados.csv e resultado.json
    janela_dias = entrada["parametros"]["janela_dias"]
    max_agrupamento = entrada["parametros"]["max_agrupamento"]
    seen_r: set[str] = set()
    seen_e: set[str] = set()

    if len(conciliados_rows) != len(resultado["grupos_conciliados"]):
        raise AssertionError("conciliados.csv diverge de resultado.json.")

    for row in conciliados_rows:
        tipo = row["tipo"]
        r_ids = [x for x in row["razao_ids"].split(";") if x]
        e_ids = [x for x in row["extrato_ids"].split(";") if x]
        val_r_csv = int(row["valor_razao_centavos"])
        val_e_csv = int(row["valor_extrato_centavos"])
        diff_csv = int(row["diferenca_dias_max"])

        for rid in r_ids:
            if rid in seen_r:
                raise AssertionError(f"Lançamento do razão reutilizado: {rid}")
            seen_r.add(rid)
        for eid in e_ids:
            if eid in seen_e:
                raise AssertionError(f"Lançamento do extrato reutilizado: {eid}")
            seen_e.add(eid)

        if tipo == "AJUSTE_DECLARADO":
            if r_ids or len(e_ids) != 1 or val_r_csv != 0:
                raise AssertionError(f"Ajuste declarado inválido: {row}")
            val_ext_sql = cur.execute(
                "SELECT valor_centavos FROM extrato WHERE id = ?", (e_ids[0],)
            ).fetchone()[0]
            if val_ext_sql != val_e_csv:
                raise AssertionError("Valor do ajuste diverge do extrato.")
        else:
            if tipo == "EXATO_1_1" and (len(r_ids) != 1 or len(e_ids) != 1):
                raise AssertionError("Grupo EXATO_1_1 com cardinalidade incorreta.")
            if tipo == "LOTE_1_N" and (len(r_ids) != 1 or not (2 <= len(e_ids) <= max_agrupamento)):
                raise AssertionError("Grupo LOTE_1_N com cardinalidade incorreta.")
            if tipo == "LOTE_N_1" and (len(e_ids) != 1 or not (2 <= len(r_ids) <= max_agrupamento)):
                raise AssertionError("Grupo LOTE_N_1 com cardinalidade incorreta.")

            q_r = f"SELECT SUM(valor_centavos) FROM razao WHERE id IN ({','.join('?' for _ in r_ids)})"
            q_e = f"SELECT SUM(valor_centavos) FROM extrato WHERE id IN ({','.join('?' for _ in e_ids)})"
            sum_r_sql = cur.execute(q_r, r_ids).fetchone()[0]
            sum_e_sql = cur.execute(q_e, e_ids).fetchone()[0]
            if sum_r_sql != sum_e_sql or sum_r_sql != val_r_csv or sum_e_csv != val_e_csv if (sum_e_csv := sum_e_sql) is None else sum_e_sql != val_e_csv:
                raise AssertionError(f"Soma em centavos não fecha para {row['grupo_id']}.")

            datas_r = [
                date.fromisoformat(
                    cur.execute("SELECT data FROM razao WHERE id = ?", (rid,)).fetchone()[0]
                )
                for rid in r_ids
            ]
            datas_e = [
                date.fromisoformat(
                    cur.execute("SELECT data FROM extrato WHERE id = ?", (eid,)).fetchone()[0]
                )
                for eid in e_ids
            ]
            max_diff = max(abs((de - dr).days) for dr in datas_r for de in datas_e)
            if max_diff != diff_csv or max_diff > janela_dias:
                raise AssertionError(f"Diferença de dias inválida em {row['grupo_id']}: {max_diff}")

    # 4. Verificação das ambiguidades preservadas
    for amb in resultado["ambiguidades"]:
        if len(amb["candidatos"]) < 2:
            raise AssertionError(f"Ambiguidade {amb['id']} deve ter pelo menos 2 candidatos.")
        for rid in amb["razao_ids"]:
            if rid in seen_r:
                raise AssertionError(f"Item ambíguo já usado no razão: {rid}")
            seen_r.add(rid)
        for eid in amb["extrato_ids"]:
            if eid in seen_e:
                raise AssertionError(f"Item ambíguo já usado no extrato: {eid}")
            seen_e.add(eid)
        for cand in amb["candidatos"]:
            q_r = f"SELECT SUM(valor_centavos) FROM razao WHERE id IN ({','.join('?' for _ in cand['razao_ids'])})"
            q_e = f"SELECT SUM(valor_centavos) FROM extrato WHERE id IN ({','.join('?' for _ in cand['extrato_ids'])})"
            sr = cur.execute(q_r, cand["razao_ids"]).fetchone()[0]
            se = cur.execute(q_e, cand["extrato_ids"]).fetchone()[0]
            if sr != se or sr != cand["soma_centavos"]:
                raise AssertionError(f"Candidato ambíguo inválido em {amb['id']}.")

    # 5. Verificação das pendências e partição completa
    for p in resultado["pendentes_razao"]:
        if p["id"] in seen_r:
            raise AssertionError(f"Pendente do razão já utilizado: {p['id']}")
        seen_r.add(p["id"])
    for p in resultado["pendentes_extrato"]:
        if p["id"] in seen_e:
            raise AssertionError(f"Pendente do extrato já utilizado: {p['id']}")
        seen_e.add(p["id"])

    all_r_sql = {row[0] for row in cur.execute("SELECT id FROM razao").fetchall()}
    all_e_sql = {row[0] for row in cur.execute("SELECT id FROM extrato").fetchall()}
    if seen_r != all_r_sql:
        raise AssertionError("Partição do razão incompleta.")
    if seen_e != all_e_sql:
        raise AssertionError("Partição do extrato incompleta.")

    total_pend_csv_expected = (
        sum(len(a["razao_ids"]) + len(a["extrato_ids"]) for a in resultado["ambiguidades"])
        + len(resultado["pendentes_razao"])
        + len(resultado["pendentes_extrato"])
    )
    if len(pendencias_rows) != total_pend_csv_expected:
        raise AssertionError("pendencias.csv não reflete todas as ambiguidades e pendências.")

    # 6. Verificação da ponte de saldos via SQL e Decimal
    conc_r_sql = cur.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos_status WHERE origem='RAZAO' AND status='CONCILIADO'"
    ).fetchone()[0]
    conc_e_sql = cur.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos_status WHERE origem='EXTRATO' AND status='CONCILIADO'"
    ).fetchone()[0]
    aj_e_sql = cur.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos_status WHERE origem='EXTRATO' AND status='AJUSTE_DECLARADO'"
    ).fetchone()[0]
    amb_r_sql = cur.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos_status WHERE origem='RAZAO' AND status='AMBIGUO'"
    ).fetchone()[0]
    amb_e_sql = cur.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos_status WHERE origem='EXTRATO' AND status='AMBIGUO'"
    ).fetchone()[0]
    pend_r_sql = cur.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos_status WHERE origem='RAZAO' AND status='PENDENTE_RAZAO'"
    ).fetchone()[0]
    pend_e_sql = cur.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) FROM lancamentos_status WHERE origem='EXTRATO' AND status='PENDENTE_EXTRATO'"
    ).fetchone()[0]

    if conc_r_sql != conc_e_sql:
        raise AssertionError(f"Soma conciliada razão ({conc_r_sql}) != extrato ({conc_e_sql})")

    qs = resultado["quadro_saldos"]
    saldo_razao_ajustado_dec = _cents_to_brl_decimal(conta["saldo_final_razao_centavos"]) + _cents_to_brl_decimal(aj_e_sql)
    dif_ini_dec = _cents_to_brl_decimal(conta["saldo_inicial_extrato_centavos"]) - _cents_to_brl_decimal(
        conta["saldo_inicial_razao_centavos"]
    )
    efeito_pend_dec = _cents_to_brl_decimal(pend_e_sql) - _cents_to_brl_decimal(pend_r_sql)
    efeito_amb_dec = _cents_to_brl_decimal(amb_e_sql) - _cents_to_brl_decimal(amb_r_sql)
    saldo_extrato_conc_dec = (
        _cents_to_brl_decimal(conta["saldo_final_extrato_centavos"])
        - dif_ini_dec
        - efeito_pend_dec
        - efeito_amb_dec
    )

    if saldo_razao_ajustado_dec != saldo_extrato_conc_dec:
        raise AssertionError("Ponte de saldos em Decimal não converge.")
    if qs["diferenca_nao_explicada_centavos"] != 0 or not qs["conservacao_exata"]:
        raise AssertionError("Quadro de saldos acusa diferença não explicada.")

    conn.close()
    return {
        "verificador": "verify_independent.py",
        "status": "OK",
        "grupos_conciliados": len(conciliados_rows),
        "ambiguidades": len(resultado["ambiguidades"]),
        "pendentes_razao": len(resultado["pendentes_razao"]),
        "pendentes_extrato": len(resultado["pendentes_extrato"]),
        "saldo_conciliado_brl": str(saldo_razao_ajustado_dec),
    }


def main() -> int:
    env = dict(os.environ)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing_pp if existing_pp else "")

    with tempfile.TemporaryDirectory(prefix="conciliacao-indep-") as tmpdir:
        tmp = Path(tmpdir)
        entrada_json = tmp / "exemplo-entrada.json"
        pacote_zip = tmp / "exemplo-pacote.zip"

        cmd1 = [
            sys.executable,
            "-m",
            "conciliacao_bancaria",
            "exemplo",
            "--saida",
            str(entrada_json),
        ]
        res1 = subprocess.run(cmd1, env=env, capture_output=True, text=True, check=False)
        if res1.returncode != 0:
            raise AssertionError(f"Falha ao exportar exemplo JSON: {res1.stderr}")

        cmd2 = [
            sys.executable,
            "-m",
            "conciliacao_bancaria",
            "conciliar",
            str(entrada_json),
            "--saida",
            str(pacote_zip),
        ]
        res2 = subprocess.run(cmd2, env=env, capture_output=True, text=True, check=False)
        if res2.returncode != 0:
            raise AssertionError(f"Falha ao conciliar exemplo: {res2.stderr}")

        relatorio = verify_zip_bundle_with_sqlite(pacote_zip)
        if relatorio["grupos_conciliados"] != 8:
            raise AssertionError(f"Esperados 8 grupos conciliados no exemplo, obtido {relatorio['grupos_conciliados']}")
        if relatorio["ambiguidades"] != 1:
            raise AssertionError(f"Esperada 1 ambiguidade no exemplo, obtido {relatorio['ambiguidades']}")
        if relatorio["pendentes_razao"] != 1 or relatorio["pendentes_extrato"] != 1:
            raise AssertionError("Esperadas 2 pendências (1 razão, 1 extrato) no exemplo.")
        if relatorio["saldo_conciliado_brl"] != "29000.00":
            raise AssertionError(f"Saldo conciliado inesperado: {relatorio['saldo_conciliado_brl']}")

    assert "conciliacao_bancaria" not in sys.modules
    print(json.dumps(relatorio, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
