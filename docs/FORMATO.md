# Especificação de Formatos (`docs/FORMATO.md`)

Este documento descreve o contrato de entrada e saída do pacote `conciliacao-bancaria` (`schema_version = 1`).

---

## 1. Regras Léxicas e Estruturais do JSON (`jsoncodec`)

Tanto o JSON de entrada quanto o `resultado.json` seguem restrições estritas de integridade:

1. **Codificação**: UTF-8 estrito sem BOM (`\xef\xbb\xbf` ou `\ufeff` são rejeitados com código `JSON_BOM`).
2. **Proibição de `float`**: qualquer número com ponto decimal ou notação científica (`10.5`, `1e3`) é rejeitado com `JSON_FLOAT`. Todos os valores monetários são representados em **centavos inteiros exatos** (`int`).
3. **Sem chaves duplicadas**: objetos JSON com chaves repetidas são rejeitados com `JSON_DUPLICADO`.
4. **Limites defensivos**:
   - Tamanho máximo de entrada: `8.388.608` bytes (8 MiB)
   - Profundidade máxima: `16` níveis
   - Número máximo de nós: `250.000` (entrada) / `1.000.000` (saída)
   - Dígitos por inteiro: até `18` dígitos (`JSON_LIMITE`)

---

## 2. Esquema do Documento de Entrada (`entrada.json`)

```json
{
  "schema_version": 1,
  "conta": {
    "id": "CONTA-BB-OPERACIONAL-01",
    "titulo": "Conta Movimento Operacional — Março/2026",
    "moeda": "BRL",
    "banco": "Banco Comercial Brasileiro (001)",
    "agencia": "3412-8",
    "numero_conta": "78450-2",
    "data_inicial": "2026-03-01",
    "data_final": "2026-03-31",
    "saldo_inicial_razao_centavos": 2500000,
    "saldo_final_razao_centavos": 2912500,
    "saldo_inicial_extrato_centavos": 2500000,
    "saldo_final_extrato_centavos": 3100000
  },
  "parametros": {
    "janela_dias": 3,
    "max_agrupamento": 4,
    "orcamento_combinacoes": 5000
  },
  "razao": [
    {
      "id": "R-001",
      "data": "2026-03-02",
      "historico": "Recebimento PIX Cliente Alfa Comércio",
      "documento": "PIX-20260302-01",
      "valor_centavos": 680000,
      "categoria": "RECEBIMENTO"
    }
  ],
  "extrato": [
    {
      "id": "E-001",
      "data": "2026-03-02",
      "descricao": "PIX RECEBIDO ALFA COMERCIO LTDA",
      "documento": "PIX-20260302-01",
      "valor_centavos": 680000,
      "tipo": "PIX"
    }
  ],
  "ajustes_declarados": [
    {
      "id": "AJ-001",
      "extrato_id": "E-009",
      "natureza": "TARIFA",
      "conta_contabil": "4.1.3.01-Tarifas de Manutenção Bancária",
      "justificativa": "Tarifa mensal contratual do pacote empresarial."
    }
  ]
}
```

### Invariantes de Entrada

- `conta.saldo_inicial_razao_centavos + sum(r.valor_centavos) == conta.saldo_final_razao_centavos` (violação gera `SALDO_INCONSISTENTE`).
- `conta.saldo_inicial_extrato_centavos + sum(e.valor_centavos) == conta.saldo_final_extrato_centavos` (violação gera `SALDO_INCONSISTENTE`).
- `valor_centavos != 0` para todos os lançamentos de `razao` e `extrato` (violação gera `VALOR_ZERO_INVALIDO`).
- Todas as datas (`YYYY-MM-DD`) devem ser datas civis válidas dentro do intervalo `[conta.data_inicial, conta.data_final]`.
- `natureza` em `ajustes_declarados` deve pertencer a: `TARIFA`, `IOF`, `JUROS`, `ENCARGO`, `RENDIMENTO`, `ESTORNO`, `AJUSTE_OPERACIONAL`.

---

## 3. Equação de Conservação de Saldos (`quadro_saldos`)

Seja $S_{\text{ini,R}}, S_{\text{fim,R}}$ os saldos do razão e $S_{\text{ini,E}}, S_{\text{fim,E}}$ os saldos do extrato. Como todo grupo conciliado pareado (`EXATO_1_1`, `LOTE_1_N`, `LOTE_N_1`) satisfaz:

$$\sum_{\text{conc}} R = \sum_{\text{conc}} E$$

a divergência bruta entre os saldos finais é decomposta exatamente em quatro parcelas:

$$S_{\text{fim,E}} - S_{\text{fim,R}} = (S_{\text{ini,E}} - S_{\text{ini,R}}) + \sum \text{Ajustes} + \left(\sum \text{Pend}_E - \sum \text{Pend}_R\right) + \left(\sum \text{Amb}_E - \sum \text{Amb}_R\right)$$

O saldo conciliado converge exatamente nos dois sentidos:

$$\text{saldo\_razao\_ajustado\_centavos} = S_{\text{fim,R}} + \sum \text{Ajustes} = \text{saldo\_extrato\_conciliado\_centavos}$$

com `diferenca_nao_explicada_centavos == 0` e `conservacao_exata == true`.

---

## 4. Estrutura do Pacote ZIP e Arquivos CSV

Cada pacote `.zip` contém 8 arquivos na raiz:

1. `entrada.json` — cópia canônica do documento de entrada.
2. `resultado.json` — resultado estruturado completo da conciliação.
3. `conciliados.csv` — grupos conciliados (`EXATO_1_1`, `LOTE_1_N`, `LOTE_N_1`, `AJUSTE_DECLARADO`).
4. `pendencias.csv` — ambiguidades preservadas (`AMBIGUO`) e pendências (`PENDENTE_RAZAO`, `PENDENTE_EXTRATO`).
5. `lancamentos.csv` — trilha individual de todos os lançamentos de razão e extrato com seu status final.
6. `RELATORIO.md` — relatório contábil em Markdown.
7. `painel-conciliacao.svg` — painel visual vetorial autocontido.
8. `SHA256SUMS` — hashes SHA-256 dos 7 arquivos anteriores.

### Proteção contra CSV Formula Injection

Qualquer campo textual que comece (diretamente ou após espaços) com `=`, `+`, `-`, `@`, `\t`, `\r` ou `\n` recebe automaticamente o prefixo `'` na exportação CSV (`sanitize_csv_text`), impedindo execução acidental de fórmulas ao abrir em softwares de planilha.
