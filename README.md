# MCP ssOtica

Servidor MCP local em Python para as seis APIs consultivas documentadas do ssOtica. Faz somente requisições `GET`, autentica com Bearer Token, pagina automaticamente quando a API informa páginas e salva cada consulta em CSV UTF-8, com objetos aninhados em colunas com ponto.

## Instalação

Use Python 3.10 ou posterior.

```bash
cd mcp
python3.12 -m venv .venv-mcp
source .venv-mcp/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edite `.env` e informe `SSOTICA_TOKEN` (Bearer Token enviado pelo ssOtica). Os seis endpoints já vêm configurados.

| Variável | Descrição |
| --- | --- |
| `SSOTICA_TOKEN` | Token enviado em `Authorization: Bearer ...`. |
| `SSOTICA_BASE_URL` | URL base (`https://app.ssotica.com.br`). |
| `SSOTICA_VENDAS_ENDPOINT` | Vendas ativas. |
| `SSOTICA_ORDENS_SERVICO_ENDPOINT` | Ordens de serviço. |
| `SSOTICA_FINANCEIRO_ENDPOINT` | Extrato/lançamentos financeiros. |
| `SSOTICA_PRODUTOS_ESTOQUE_ENDPOINT` | Produtos, estoque, reservas, preços e fornecedores. |
| `SSOTICA_CONTAS_PAGAR_ENDPOINT` | Contas a pagar. |
| `SSOTICA_CONTAS_RECEBER_ENDPOINT` | Contas a receber. |

## Registrar no Cursor

Em **Settings → MCP → Add new MCP server**, ou em `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ssotica": {
      "command": "~/mcp/.venv-mcp/bin/python",
      "args": ["~/mcp/mcp_server.py"]
    }
  }
}
```

## Tools disponíveis

Todas recebem `empresa`. O servidor aceita **CNPJ sem pontuação** ou **Código da Licença** e resolve automaticamente via `data/empresas.csv`. Se uma API da ssOtica aceitar somente CNPJ e esse CNPJ tiver várias licenças, o servidor consulta o CNPJ normalmente e salva o resultado agregado do conjunto.
 
| Tool | API | Identificador | Período |
| --- | --- | --- | --- |
| `consultar-empresas` | Lista local | — | — |
| `consultar-vendas-ativas` | Vendas ativas | CNPJ | máx. 30 dias |
| `consultar-ordens-servico` | Ordens de serviço | CNPJ | máx. 30 dias |
| `consultar-lancamentos-financeiros` | Extrato financeiro | CNPJ | máx. 30 dias |
| `consultar-produtos-estoque` | Produtos/estoque | Código da Licença | — |
| `consultar-contas-pagar` | Contas a pagar | Código da Licença | sem limite de 30 dias |
| `consultar-contas-receber` | Contas a receber | Código da Licença | sem limite de 30 dias |

Argumentos opcionais:

- **Período:** `data_inicio`, `data_fim` (`YYYY-MM-DD`; padrão: mês atual até hoje)
- **Desambiguação:** `licenca` (usada nas APIs que aceitam Código da Licença)
- **Produtos:** `referencia`, `produto_id`, `page`, `per_page` (máx. 100)
- **Contas a pagar:** `tipo_periodo` (`vencimento`, `cancelamento`, `pagamento`, `lançamento`, `emissao`), `conta_id`, `documento`, `emissao`, `page`, `per_page`
- **Contas a receber:** `tipo_periodo` (`vencimento`, `cancelamento`, `pagamento`, `lançamento`, `renegociacao`), `conta_id`, `documento`, `renegociacao`, `page`, `per_page`

Com `conta_id`, os demais filtros de período são ignorados (conforme documentação da API).

Exemplos:

```text
consultar-vendas-ativas empresa=42547637000142 licenca=RODO-UYEN data_inicio=2026-07-01 data_fim=2026-07-10
consultar-produtos-estoque empresa=42547637000142 licenca=RODO-UYEN referencia=ABC-123
consultar-contas-pagar empresa=RODO-UYEN tipo_periodo=pagamento data_inicio=2025-08-01 data_fim=2025-08-31
```

## Arquivos gerados

CSVs em `data/`, organizados por tipo: `vendas/`, `ordens_servico/`, `lancamentos_financeiros/`, `produtos_estoque/`, `contas_pagar/` e `contas_receber/`.

## Solução de problemas

- **401 / 403:** token inválido, expirado ou sem permissão.
- **404:** confirme se a conta possui acesso à API consultiva contratada.
- **Período inválido:** vendas, O.S. e extrato aceitam no máximo 30 dias por consulta.
- **CNPJ com várias licenças em API somente por CNPJ:** a consulta retorna o agregado do CNPJ; a API ssOtica não retorna a licença nos registros para separar as unidades.
- **Sem resultados:** é gerado um CSV com cabeçalho; revise identificador, período e filtros.
