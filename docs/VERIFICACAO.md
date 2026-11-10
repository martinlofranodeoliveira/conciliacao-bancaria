# Guia de Verificação Independente e Auditoria (`docs/VERIFICACAO.md`)

Este guia descreve como auditar os cálculos e artefatos de `conciliacao-bancaria` sem confiar na própria biblioteca.

---

## 1. Verificador Independente via SQLite (`tools/verify_independent.py`)

O script `tools/verify_independent.py` **não importa `conciliacao_bancaria`**. Ele executa a CLI como processo externo caixa-preta, extrai o pacote `.zip` gerado, valida o manifesto `SHA256SUMS`, carrega `entrada.json`, `resultado.json`, `conciliados.csv`, `pendencias.csv` e `lancamentos.csv` em um banco **SQLite em memória** (`:memory:`) e recalcula todas as invariantes contábeis via consultas SQL e `int`/`Decimal` puros:

1. **Integridade de saldos de entrada**:
   - `saldo_inicial_razao + SUM(razao.valor_centavos) == saldo_final_razao`
   - `saldo_inicial_extrato + SUM(extrato.valor_centavos) == saldo_final_extrato`
2. **Partição exata e disjunta**:
   - Todo lançamento de `razao` e de `extrato` aparece exatamente uma vez em `lancamentos.csv` e exatamente uma vez na união disjunta de (`conciliados`, `ajustes_declarados`, `ambiguidades`, `pendentes`).
3. **Igualdade em centavos e janela temporal de cada grupo**:
   - Em todo grupo `EXATO_1_1`, `LOTE_1_N` e `LOTE_N_1`, a soma dos lançamentos do razão é idêntica à soma dos lançamentos do extrato no SQLite, e a diferença máxima de datas respeita `janela_dias`.
4. **Equação de conservação no `quadro_saldos`**:
   - Verifica que `diferenca_nao_explicada_centavos == 0` e `saldo_razao_ajustado_centavos == saldo_extrato_conciliado_centavos`.

Para executar:

```bash
python3 tools/verify_independent.py
```

---

## 2. Verificação de Distribuição Python (`scripts/check_distribution.py`)

Após construir a *wheel* (`.whl`) e a *source distribution* (`.tar.gz`), o script `scripts/check_distribution.py` inspeciona:

- Ausência de artefatos locais (`local/`, `.env`, `__pycache__`, `.pyc`) dentro do `.whl` e do `.tar.gz`;
- Validação de todos os hashes SHA-256 listados no arquivo `RECORD` da *wheel* (PEP 427);
- Instalação isolada da *wheel* em um ambiente virtual limpo (`venv`) e execução ponta a ponta da CLI instalada fora da árvore do repositório.

```bash
python3 -m build
python3 scripts/check_distribution.py --dist dist
```

---

## 3. Verificação Manual de `SHA256SUMS`

Ao extrair qualquer pacote `.zip` emitido pela ferramenta, é possível validar os artefatos diretamente com `sha256sum`:

```bash
unzip -q exemplo-conciliacao.zip -d /tmp/auditoria-conciliacao
cd /tmp/auditoria-conciliacao
sha256sum -c SHA256SUMS
```
