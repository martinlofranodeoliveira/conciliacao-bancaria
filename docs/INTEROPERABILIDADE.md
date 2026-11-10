# Interoperabilidade com SQLite, Planilhas e ERPs (`docs/INTEROPERABILIDADE.md`)

Os artefatos gerados por `conciliacao-bancaria` foram projetados para integração direta com bancos de dados relacionais, ferramentas de BI/planilhas e sistemas contábeis (ERP).

---

## 1. Importação Direta no SQLite

Os arquivos `conciliados.csv`, `pendencias.csv` e `lancamentos.csv` usam codificação UTF-8 sem BOM, separador vírgula (`,`) e quebra de linha `LF` (`\n`):

```sql
.mode csv
.import lancamentos.csv lancamentos
.import conciliados.csv conciliados
.import pendencias.csv pendencias

SELECT status, COUNT(*) AS qtd, SUM(CAST(valor_centavos AS INTEGER)) AS soma_centavos
FROM lancamentos
GROUP BY origem, status
ORDER BY origem, status;
```

---

## 2. Conversão de Extratos Bancários (OFX / CSV) e Razão ERP para `entrada.json`

Ao converter dados provenientes de ERPs ou arquivos bancários para o formato `entrada.json`:

1. **Converta valores monetários com `Decimal` para centavos inteiros** antes de serializar:
   ```python
   from decimal import Decimal, ROUND_HALF_EVEN

   def brl_str_to_centavos(valor_texto: str) -> int:
       limpo = valor_texto.strip().replace(".", "").replace(",", ".")
       dec = Decimal(limpo)
       exato = (dec * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
       return int(exato)
   ```
2. **Garanta identificadores ASCII únicos** (`R-001`, `E-001`, `AJ-001`) para rastreabilidade completa nos relatórios de auditoria.
3. **Valide os saldos inicial e final** antes da submissão: a soma algébrica dos movimentos do período deve fechar exatamente com `saldo_final - saldo_inicial` tanto no razão quanto no extrato.

---

## 3. Segurança em Planilhas (Excel / LibreOffice Calc)

Todos os campos textuais exportados nos arquivos `.csv` passam por neutralização de prefixos executáveis (`=`, `+`, `-`, `@`, `\t`, `\r`, `\n`), prevenindo ataques de *CSV Formula Injection* quando descrições de extrato contêm dados originados de terceiros (por exemplo, mensagens livres em transferências PIX).
