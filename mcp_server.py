"""Servidor MCP local para as seis APIs consultivas documentadas do ssOtica."""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping
from urllib.parse import urljoin

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
RECORD_KEYS = ("data", "items", "results", "vendas", "lancamentos", "extrato")
MAX_PERIODO_DIAS = 30
CNPJ_DIGITS = re.compile(r"^\d{14}$")
mcp = FastMCP("ssotica")


class SsOticaError(RuntimeError):
    """Erro seguro para exibir ao usuário da tool."""


@dataclass(frozen=True)
class Settings:
    token: str
    base_url: str


@dataclass(frozen=True)
class EmpresaRegistro:
    cnpj: str
    codigo_licenca: str
    nome: str


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SsOticaError(f"Configuração ausente: defina {name} no arquivo .env.")
    if "replace-with-real" in value:
        raise SsOticaError(f"Configuração inválida: substitua o valor de exemplo de {name} no arquivo .env.")
    return value


def load_settings() -> Settings:
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        raise SsOticaError("Arquivo .env não encontrado. Copie .env.example para .env e preencha os valores.")
    load_dotenv(env_file, override=False)
    return Settings(token=require_env("SSOTICA_TOKEN"), base_url=require_env("SSOTICA_BASE_URL").rstrip("/"))


def get_auth_headers(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.token}", "Accept": "application/json"}


def endpoint_url(settings: Settings, env_name: str) -> str:
    endpoint = require_env(env_name)
    return endpoint if endpoint.startswith(("http://", "https://")) else f"{settings.base_url}/{endpoint.lstrip('/')}"


def normalize_cnpj(value: str) -> str:
    return re.sub(r"\D", "", value)


def load_empresas() -> list[EmpresaRegistro]:
    path = DATA_DIR / "empresas.csv"
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return [
            EmpresaRegistro(
                cnpj=row.get("cnpj", "").strip(),
                codigo_licenca=row.get("codigo_licenca", "").strip(),
                nome=row.get("nome", "").strip(),
            )
            for row in csv.DictReader(file)
            if row.get("cnpj") and row.get("codigo_licenca")
        ]


def resolve_empresa(empresa: str, licenca: str | None = None) -> EmpresaRegistro:
    value = empresa.strip()
    licenca_value = licenca.strip() if licenca else None

    if not value and not licenca_value:
        raise SsOticaError(
            "Informe empresa. Para vendas, O.S. e extrato use o CNPJ sem pontuação; "
            "para produtos, contas a pagar e contas a receber use o Código da Licença "
            "(painel ssOtica ou data/empresas.csv). "
            "Se o CNPJ tiver várias licenças, informe também licenca."
        )

    empresas = load_empresas()

    if licenca_value:
        by_licenca = [item for item in empresas if item.codigo_licenca.casefold() == licenca_value.casefold()]
        if not by_licenca:
            raise SsOticaError(f"Código da Licença '{licenca_value}' não encontrado em data/empresas.csv.")
        row = by_licenca[0]
        if value:
            normalized_cnpj = normalize_cnpj(value)
            if CNPJ_DIGITS.fullmatch(normalized_cnpj) and row.cnpj != normalized_cnpj:
                raise SsOticaError(
                    f"A licença {row.codigo_licenca} não pertence ao CNPJ {normalized_cnpj}."
                )
        return row

    if not value:
        raise SsOticaError("Informe empresa ou licenca.")

    by_licenca = [item for item in empresas if item.codigo_licenca.casefold() == value.casefold()]
    if by_licenca:
        return by_licenca[0]

    normalized_cnpj = normalize_cnpj(value)
    by_cnpj = [item for item in empresas if item.cnpj == normalized_cnpj]
    if by_cnpj:
        if len(by_cnpj) == 1:
            return by_cnpj[0]
        licencas = ", ".join(item.codigo_licenca for item in by_cnpj)
        raise SsOticaError(
            f"O CNPJ {normalized_cnpj} possui várias licenças ({licencas}). "
            "Informe licenca com o Código da Licença da unidade desejada."
        )

    if CNPJ_DIGITS.fullmatch(normalized_cnpj):
        return EmpresaRegistro(cnpj=normalized_cnpj, codigo_licenca="", nome="")
    return EmpresaRegistro(cnpj="", codigo_licenca=value, nome="")


def resolve_identificador(
    empresa: str,
    prefer: Literal["cnpj", "licenca"],
    licenca: str | None = None,
) -> str:
    row = resolve_empresa(empresa, licenca)
    if prefer == "cnpj":
        if row.cnpj:
            return row.cnpj
        raise SsOticaError("Para esta API informe o CNPJ com 14 dígitos, sem pontuação.")
    if row.codigo_licenca:
        return row.codigo_licenca
    raise SsOticaError(
        "Para esta API informe o Código da Licença. "
        "Se informou CNPJ com várias licenças, use o parâmetro licenca."
    )


def fetch_json(client: httpx.Client, url: str, headers: Mapping[str, str], params: Mapping[str, Any]) -> Any:
    try:
        response = client.get(url, headers=headers, params=params)
    except httpx.TimeoutException as exc:
        raise SsOticaError("Tempo limite atingido ao consultar a API ssOtica.") from exc
    except httpx.RequestError as exc:
        raise SsOticaError(f"Erro de rede ao consultar a API ssOtica: {exc.__class__.__name__}.") from exc
    if response.status_code == 401:
        raise SsOticaError("Autenticação recusada (HTTP 401). Verifique SSOTICA_TOKEN.")
    if response.status_code == 403:
        raise SsOticaError("Autorização recusada (HTTP 403). Verifique as permissões do token.")
    if response.status_code == 404:
        raise SsOticaError("Endpoint não encontrado (HTTP 404). Verifique a configuração no .env.")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text.strip()
        if detail:
            detail = detail[:240].replace("\n", " ")
            raise SsOticaError(f"A API ssOtica retornou HTTP {response.status_code}: {detail}") from exc
        raise SsOticaError(f"A API ssOtica retornou HTTP {response.status_code}.") from exc
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise SsOticaError("A API ssOtica retornou uma resposta que não é JSON válido.") from exc


def looks_like_record(value: Mapping[str, Any]) -> bool:
    markers = (
        "data_operacao",
        "numero",
        "referencia",
        "vencimento",
        "valor_bruto",
        "valor_liquido",
        "status",
        "tipo_os",
    )
    return any(key in value for key in markers)


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        list_key = next((key for key in RECORD_KEYS if isinstance(payload.get(key), list)), None)
        if list_key is not None:
            records = payload[list_key]
        elif looks_like_record(payload):
            records = [payload]
        else:
            keys = ", ".join(sorted(str(key) for key in payload)) or "(nenhuma chave)"
            raise SsOticaError(f"Formato inesperado. Chaves JSON recebidas: {keys}.")
    else:
        raise SsOticaError(f"Formato inesperado: esperado objeto ou lista, recebido {type(payload).__name__}.")
    if not all(isinstance(record, dict) for record in records):
        raise SsOticaError("Formato inesperado: todos os registros devem ser objetos JSON.")
    return records


def _next_page(payload: Any, current_url: str, current_params: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None
    links = payload.get("links")
    raw_next = links.get("next") if isinstance(links, dict) else payload.get("next", payload.get("next_page"))
    if raw_next not in (None, False, ""):
        if isinstance(raw_next, str) and raw_next.isdigit():
            params = dict(current_params)
            params["page"] = int(raw_next)
            return current_url, params
        if isinstance(raw_next, str):
            return urljoin(current_url, raw_next), {}
        if isinstance(raw_next, (int, float)):
            params = dict(current_params)
            params["page"] = int(raw_next)
            return current_url, params
    page = payload.get("page", payload.get("current_page", payload.get("currentPage")))
    total = payload.get("total_pages", payload.get("totalPages"))
    if payload.get("has_more") is True and isinstance(page, int):
        params = dict(current_params)
        params["page"] = page + 1
        return current_url, params
    if isinstance(page, int) and isinstance(total, int) and page < total:
        params = dict(current_params)
        params["page"] = page + 1
        return current_url, params
    return None


def fetch_paginated(client: httpx.Client, url: str, headers: Mapping[str, str], params: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current_url, current_params = url, dict(params)
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    while True:
        key = (current_url, tuple(sorted((str(k), str(v)) for k, v in current_params.items())))
        if key in seen:
            raise SsOticaError("Paginação interrompida: a API retornou repetidamente a mesma página.")
        seen.add(key)
        payload = fetch_json(client, current_url, headers, current_params)
        records.extend(extract_records(payload))
        next_request = _next_page(payload, current_url, current_params)
        if next_request is None:
            return records
        current_url, current_params = next_request


def flatten_dict(value: Mapping[str, Any], parent_key: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        full_key = f"{parent_key}.{key}" if parent_key else str(key)
        if isinstance(item, dict):
            flattened.update(flatten_dict(item, full_key))
        elif isinstance(item, list):
            flattened[full_key] = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        else:
            flattened[full_key] = "" if item is None else item
    return flattened


def save_csv(path: Path, rows: Iterable[Mapping[str, Any]], required_fields: Iterable[str] = ()) -> None:
    materialized = [dict(row) for row in rows]
    fields = list(required_fields)
    for row in materialized:
        for field in row:
            if field not in fields:
                fields.append(field)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


@mcp.tool(name="consultar-empresas")
def consultar_empresas(busca: str | None = None) -> str:
    """Lista as óticas cadastradas em data/empresas.csv; busca opcional por nome, CNPJ ou código da licença."""
    path = DATA_DIR / "empresas.csv"
    try:
        if not path.is_file():
            raise SsOticaError("Arquivo data/empresas.csv não encontrado.")
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            empresas = list(csv.DictReader(file))
        if busca and busca.strip():
            needle = busca.strip().casefold()
            empresas = [
                empresa
                for empresa in empresas
                if any(needle in str(value).casefold() for value in empresa.values())
            ]
        return json.dumps(
            {"quantidade": len(empresas), "empresas": empresas},
            ensure_ascii=False,
        )
    except (OSError, csv.Error) as exc:
        return f"Erro ao consultar empresas: {exc.__class__.__name__}."
    except SsOticaError as exc:
        return f"Erro ao consultar empresas: {exc}"


def parse_date(value: str, argument_name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise SsOticaError(f"{argument_name} inválida: use o formato YYYY-MM-DD.") from exc


def resolve_periodo(data_inicio: str | None, data_fim: str | None, maximo_30_dias: bool = False) -> tuple[str, str]:
    today = date.today()
    start = parse_date(data_inicio, "data_inicio") if data_inicio else today.replace(day=1)
    end = parse_date(data_fim, "data_fim") if data_fim else today
    if start > end:
        raise SsOticaError("Período inválido: data_inicio não pode ser posterior a data_fim.")
    if maximo_30_dias and (end - start).days > MAX_PERIODO_DIAS:
        raise SsOticaError(
            f"Período inválido: esta API ssOtica permite no máximo {MAX_PERIODO_DIAS} dias entre inicio_periodo e fim_periodo."
        )
    return start.isoformat(), end.isoformat()


def normalize_tipo_periodo(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().casefold().replace("ç", "c")
    mapping = {
        "vencimento": "vencimento",
        "cancelamento": "cancelamento",
        "pagamento": "pagamento",
        "lancamento": "lançamento",
        "emissao": "emissao",
        "renegociacao": "renegociacao",
    }
    if normalized not in mapping:
        return None
    return mapping[normalized]


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)


def consultar_e_salvar(
    endpoint_env: str,
    output_name: str,
    params: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> str:
    settings = load_settings()
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        records = fetch_paginated(client, endpoint_url(settings, endpoint_env), get_auth_headers(settings), params)
    prefix = dict(metadata or {})
    rows = [{**prefix, **flatten_dict(record)} for record in records]
    output = DATA_DIR / output_name
    save_csv(output, rows, prefix.keys())
    return f"Arquivo data/{output_name} atualizado com {len(rows)} registros."


def consulta_por_periodo(
    endpoint_env: str,
    output_dir: str,
    empresa: str,
    data_inicio: str | None,
    data_fim: str | None,
    identificador_tipo: Literal["cnpj", "licenca"],
    maximo_30_dias: bool,
    extras: Mapping[str, Any] | None = None,
    incluir_paginacao: bool = False,
    licenca: str | None = None,
) -> str:
    registro = resolve_empresa(empresa, licenca)
    identificador = registro.cnpj if identificador_tipo == "cnpj" else registro.codigo_licenca
    if identificador_tipo == "cnpj" and not identificador:
        raise SsOticaError("Para esta API informe o CNPJ com 14 dígitos, sem pontuação.")
    if identificador_tipo == "licenca" and not identificador:
        raise SsOticaError(
            "Para esta API informe o Código da Licença. "
            "Se informou CNPJ com várias licenças, use o parâmetro licenca."
        )

    empresa_param = "cnpj" if identificador_tipo == "cnpj" else "empresa"
    extras_clean = {key: value for key, value in (extras or {}).items() if value is not None and value != ""}

    if extras_clean.get("id"):
        params: dict[str, Any] = {"id": extras_clean["id"]}
        inicio, fim = "", ""
    else:
        inicio, fim = resolve_periodo(data_inicio, data_fim, maximo_30_dias)
        params = {empresa_param: identificador, "inicio_periodo": inicio, "fim_periodo": fim}
        params.update(extras_clean)

    if incluir_paginacao:
        params.setdefault("page", 1)
        params.setdefault("perPage", 100)

    file_tag = safe_name(registro.codigo_licenca or identificador)
    metadata: dict[str, Any] = {
        "empresa": identificador,
        "cnpj": registro.cnpj,
        "codigo_licenca": registro.codigo_licenca,
        "nome": registro.nome,
    }

    if extras_clean.get("id"):
        name = f"{output_dir}/{output_dir}_{file_tag}_id_{safe_name(str(extras_clean['id']))}.csv"
        metadata["id"] = extras_clean["id"]
    else:
        name = f"{output_dir}/{output_dir}_{file_tag}_{inicio}_{fim}.csv"
        metadata["inicio_periodo"] = inicio
        metadata["fim_periodo"] = fim

    return consultar_e_salvar(endpoint_env, name, params, metadata)


@mcp.tool(name="consultar-vendas-ativas")
def consultar_vendas_ativas(
    empresa: str,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    licenca: str | None = None,
) -> str:
    """Consulta vendas ativas (somente status ATIVA) por CNPJ ou código da licença; período máximo de 30 dias."""
    try:
        return consulta_por_periodo(
            "SSOTICA_VENDAS_ENDPOINT",
            "vendas",
            empresa,
            data_inicio,
            data_fim,
            "cnpj",
            True,
            licenca=licenca,
        )
    except SsOticaError as exc:
        return f"Erro ao consultar vendas ativas: {exc}"


@mcp.tool(name="consultar-ordens-servico")
def consultar_ordens_servico(
    empresa: str,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    licenca: str | None = None,
) -> str:
    """Consulta ordens de serviço por CNPJ ou código da licença; período máximo de 30 dias."""
    try:
        return consulta_por_periodo(
            "SSOTICA_ORDENS_SERVICO_ENDPOINT",
            "ordens_servico",
            empresa,
            data_inicio,
            data_fim,
            "cnpj",
            True,
            licenca=licenca,
        )
    except SsOticaError as exc:
        return f"Erro ao consultar ordens de serviço: {exc}"


@mcp.tool(name="consultar-lancamentos-financeiros")
def consultar_lancamentos_financeiros(
    empresa: str,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    licenca: str | None = None,
) -> str:
    """Consulta lançamentos do extrato financeiro por CNPJ ou código da licença; período máximo de 30 dias."""
    try:
        return consulta_por_periodo(
            "SSOTICA_FINANCEIRO_ENDPOINT",
            "lancamentos_financeiros",
            empresa,
            data_inicio,
            data_fim,
            "cnpj",
            True,
            licenca=licenca,
        )
    except SsOticaError as exc:
        return f"Erro ao consultar lançamentos financeiros: {exc}"


@mcp.tool(name="consultar-produtos-estoque")
def consultar_produtos_estoque(
    empresa: str,
    referencia: str | None = None,
    produto_id: str | None = None,
    page: int = 1,
    per_page: int = 100,
    licenca: str | None = None,
) -> str:
    """Consulta produtos, estoque, reservas de O.S., preços, grupos, grifes e fornecedores (paginação automática)."""
    try:
        if page < 1:
            raise SsOticaError("page inválida: informe um número inteiro maior ou igual a 1.")
        if per_page < 1 or per_page > 100:
            raise SsOticaError("per_page inválido: informe um valor entre 1 e 100.")
        registro = resolve_empresa(empresa, licenca)
        if not registro.codigo_licenca:
            raise SsOticaError(
                "Para produtos informe o Código da Licença. "
                "Se informou CNPJ com várias licenças, use o parâmetro licenca."
            )
        identificador = registro.codigo_licenca
        params: dict[str, Any] = {"empresa": identificador, "page": page, "perPage": per_page}
        if referencia:
            params["referencia"] = referencia
        if produto_id:
            params["id"] = produto_id
        suffix = safe_name(referencia or produto_id or "todos")
        name = f"produtos_estoque/produtos_estoque_{safe_name(identificador)}_{suffix}.csv"
        return consultar_e_salvar(
            "SSOTICA_PRODUTOS_ESTOQUE_ENDPOINT",
            name,
            params,
            {
                "empresa": identificador,
                "cnpj": registro.cnpj,
                "codigo_licenca": registro.codigo_licenca,
                "nome": registro.nome,
            },
        )
    except SsOticaError as exc:
        return f"Erro ao consultar produtos e estoque: {exc}"


@mcp.tool(name="consultar-contas-pagar")
def consultar_contas_pagar(
    empresa: str,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    tipo_periodo: str | None = None,
    conta_id: str | None = None,
    documento: str | None = None,
    emissao: str | None = None,
    page: int = 1,
    per_page: int = 100,
    licenca: str | None = None,
) -> str:
    """Consulta contas a pagar por Código da Licença; tipo_periodo padrão é vencimento."""
    try:
        normalized_tipo = normalize_tipo_periodo(tipo_periodo)
        if tipo_periodo and normalized_tipo is None:
            raise SsOticaError("tipo_periodo inválido para contas a pagar.")
        if page < 1:
            raise SsOticaError("page inválida: informe um número inteiro maior ou igual a 1.")
        if per_page < 1 or per_page > 100:
            raise SsOticaError("per_page inválido: informe um valor entre 1 e 100.")
        return consulta_por_periodo(
            "SSOTICA_CONTAS_PAGAR_ENDPOINT",
            "contas_pagar",
            empresa,
            data_inicio,
            data_fim,
            "licenca",
            False,
            {
                "tipo_periodo": normalized_tipo,
                "id": conta_id,
                "documento": documento,
                "emissao": emissao,
                "page": page,
                "perPage": per_page,
            },
            True,
            licenca=licenca,
        )
    except SsOticaError as exc:
        return f"Erro ao consultar contas a pagar: {exc}"


@mcp.tool(name="consultar-contas-receber")
def consultar_contas_receber(
    empresa: str,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    tipo_periodo: str | None = None,
    conta_id: str | None = None,
    documento: str | None = None,
    renegociacao: str | None = None,
    page: int = 1,
    per_page: int = 100,
    licenca: str | None = None,
) -> str:
    """Consulta contas a receber por Código da Licença; tipo_periodo padrão é vencimento."""
    try:
        normalized_tipo = normalize_tipo_periodo(tipo_periodo)
        if tipo_periodo and normalized_tipo is None:
            raise SsOticaError("tipo_periodo inválido para contas a receber.")
        if page < 1:
            raise SsOticaError("page inválida: informe um número inteiro maior ou igual a 1.")
        if per_page < 1 or per_page > 100:
            raise SsOticaError("per_page inválido: informe um valor entre 1 e 100.")
        return consulta_por_periodo(
            "SSOTICA_CONTAS_RECEBER_ENDPOINT",
            "contas_receber",
            empresa,
            data_inicio,
            data_fim,
            "licenca",
            False,
            {
                "tipo_periodo": normalized_tipo,
                "id": conta_id,
                "documento": documento,
                "renegociacao": renegociacao,
                "page": page,
                "perPage": per_page,
            },
            True,
            licenca=licenca,
        )
    except SsOticaError as exc:
        return f"Erro ao consultar contas a receber: {exc}"


if __name__ == "__main__":
    mcp.run()
