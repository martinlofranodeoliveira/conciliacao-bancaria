# Referência da API Python e CLI (`docs/API.md`)

---

## 1. Funções Principais (`conciliacao_bancaria`)

### `reconcile(documento, *, janela_dias=None, max_agrupamento=None, orcamento_combinacoes=None) -> ResultadoConciliacao`
(Alias em português: `conciliar`)

Valida um dicionário Python representando o documento de entrada e executa os 5 estágios determinísticos de conciliação bancária.

- **Parâmetros**:
  - `documento`: `Mapping[str, Any]` no formato `schema_version = 1`.
  - `janela_dias`: `int | None` (opcional, sobrescreve `parametros.janela_dias`, intervalo `0..30`).
  - `max_agrupamento`: `int | None` (opcional, sobrescreve `parametros.max_agrupamento`, intervalo `1..8`).
  - `orcamento_combinacoes`: `int | None` (opcional, sobrescreve `parametros.orcamento_combinacoes`, intervalo `1..50000`).
- **Retorno**: instância imutável de `ResultadoConciliacao`.

### `reconcile_json(dados, *, janela_dias=None, max_agrupamento=None, orcamento_combinacoes=None) -> ResultadoConciliacao`
(Alias em português: `conciliar_json`)

Decodifica `bytes` ou `str` via `jsoncodec.loads` (rejeitando BOM, `float` e chaves duplicadas) e invoca `reconcile(...)`.

### `load_example_data() -> dict[str, Any]` e `reconcile_example() -> ResultadoConciliacao`

Carrega e concilia o cenário sintético oficial empacotado em `conciliacao_bancaria/data/exemplo.json`.

### `format_brl(centavos: int) -> str`

Formata um inteiro em centavos como moeda brasileira (`R$ 29.125,00`, `-R$ 89,00`).

---

## 2. Geração de Relatórios e Pacotes (`conciliacao_bancaria.reports`)

- `render_conciliados_csv(resultado: ResultadoConciliacao) -> str`
- `render_pendencias_csv(resultado: ResultadoConciliacao) -> str`
- `render_lancamentos_csv(resultado: ResultadoConciliacao) -> str`
- `render_markdown_report(resultado: ResultadoConciliacao) -> str`
- `render_svg_panel(resultado: ResultadoConciliacao) -> str`
- `build_bundle_files(resultado: ResultadoConciliacao) -> dict[str, bytes]`
- `write_bundle_zip(resultado: ResultadoConciliacao, destino: str | Path) -> Path`
- `write_bundle_directory(resultado: ResultadoConciliacao, destino: str | Path) -> Path`
- `verify_bundle_zip(caminho_zip: str | Path) -> dict[str, str]`

Todas as funções de escrita em disco (`publish_file_bytes`, `write_bundle_zip`, `write_bundle_directory`) **recusam sobrescrever caminhos já existentes** (`ConciliacaoError` com código `SAIDA_EXISTENTE`).

---

## 3. Códigos de Erro (`ConciliacaoError`)

Toda falha de validação, orçamento ou E/S levanta `ConciliacaoError(code, message, path, detalhes)`:

| Código | Causa |
| :--- | :--- |
| `JSON_BOM` | Presença de BOM UTF-8 no início do documento JSON. |
| `JSON_UTF8` | Bytes UTF-8 inválidos ou códigos substitutos Unicode (`U+D800..U+DFFF`). |
| `JSON_FLOAT` | Número com ponto decimal ou expoente científico. |
| `JSON_DUPLICADO` | Objeto JSON com chave duplicada. |
| `JSON_LIMITE` | Excesso de bytes, profundidade, nós ou dígitos inteiros. |
| `JSON_INVALIDO` | Sintaxe JSON malformada ou constante `NaN`/`Infinity`. |
| `VERSAO_ESQUEMA_INVALIDA` | `schema_version` diferente de `1`. |
| `CAMPO_DESCONHECIDO` | Chave não prevista no esquema do objeto. |
| `ESQUEMA_INVALIDO` | Tipo incorreto, campo obrigatório ausente ou texto inválido. |
| `DATA_INVALIDA` | Data fora do formato `YYYY-MM-DD`, inexistente ou fora do período da conta. |
| `VALOR_ZERO_INVALIDO` | Lançamento de razão ou extrato com `valor_centavos == 0`. |
| `ID_DUPLICADO` | Identificador repetido entre lançamentos ou ajustes. |
| `REFERENCIA_INEXISTENTE` | `extrato_id` em `ajustes_declarados` não existe em `extrato`. |
| `CONFLITO_AJUSTE` | Dois ajustes declarados apontam para o mesmo `extrato_id`. |
| `AJUSTE_INVALIDO` | `natureza` de ajuste fora da lista permitida. |
| `SALDO_INCONSISTENTE` | Saldo inicial + soma dos movimentos difere do saldo final declarado. |
| `ORCAMENTO_COMBINACOES_EXCEDIDO` | Busca de subconjuntos 1:N / N:1 ultrapassou `orcamento_combinacoes`. |
| `SAIDA_EXISTENTE` | O arquivo ou diretório de saída já existe. |
| `CAMINHO_INVALIDO` | Caminho vazio, com byte nulo ou diretório pai inexistente. |
| `ZIP_INVALIDO` | Pacote ZIP ausente, corrompido, com caminho inseguro ou divergência em `SHA256SUMS`. |

---

## 4. Interface de Linha de Comando (CLI)

```text
conciliacao-bancaria [--version] {exemplo,conciliar,verificar} ...
```

- **Código de saída `0`**: execução concluída com resumo JSON em `stdout`.
- **Código de saída `2`**: erro de validação, domínio ou argumentos com mensagem estruturada `[CODIGO] ...` em `stderr`.
