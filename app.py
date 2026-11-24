"""Painel de Implantação e Onboarding (HDI): Jira + banco → integracao_implantacao.py → Google Sheets → Streamlit."""
import html
import re
from datetime import timedelta
from pathlib import Path

import gspread
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:  # busca que filtra a cada tecla; sem o pacote, cai no campo padrão (filtra ao dar Enter)
    from st_keyup import st_keyup
except ImportError:
    st_keyup = None

st.set_page_config(page_title="Implantação", page_icon="🚀", layout="wide")

# =============================================================================
# PALETA E CONSTANTES
# =============================================================================
AZUL, CIANO, CINZA, LIMA, LIMA_CLARO = "#078CA9", "#53D1EC", "#F2F2F2", "#C5DF16", "#DDF156"
AZUL_ESCURO, LIMA_ESCURO = "#056A80", "#8FA30E"
TINTA, TINTA_SUAVE, ALERTA = "#1E2A32", "#5B6B75", "#C0392B"
FUSO = "America/Sao_Paulo"
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
DIAS_ALERTA = 7  # chamado parado na mesma fila há esse número de dias entra no alerta
DIAS_SEM_USO = 7  # revenda sem atendimento há mais que isso entra no alerta de uso
DIAS_ALERTA_ENTRADA = 10  # alertas do pop-up de entrada (onboarding sem uso, Hypercare longo)
DIAS_PARADO_CRITICO = 30  # card parado na mesma fila há mais que isso entra no pop-up
# Analistas acompanhados no painel (comparação pelo início do nome do responsável no Jira).
# "Lia" ainda não tem usuário no Jira: aparece com zero até receber cards.
ANALISTAS = ["Arthur Domingues", "Carol Rodrigues", "Lia"]
META_ADERENCIA = 0.7  # checklists de recepção ÷ passagens de chassi no DMS, no mês
# Módulos do mês (colunas do banco) mostrados no uso por revenda
MODULOS_USO = {
    "QTD_CHECKLIST_RECEPCAO": "Checklist recepção", "QTD_ORCAMENTO_RECEPCAO": "Orçamento recepção",
    "QTD_ORCAMENTO_COMPLEMENTAR": "Orçamento complementar", "QTD_CHECKLIST_OFICINA": "Checklist oficina",
    "QTD_CHECKLIST_QUALIDADE": "Checklist qualidade", "QTD_CHECKLIST_ENTREGA": "Checklist entrega",
    "QTD_CHECKLIST_PESQUISA": "Pesquisa", "QTD_OS": "OS Mobato",
}

# Quadros do Jira (filas do service desk HDI) e os status que compõem cada um
QUADROS = {
    "2. Novas implantações": [
        "Análise de Implantação", "Aguardando Dados do Cliente", "Implantação Sem Contrato", "Cadastro de Parceiro",
        "Atualizar Dados", "Analisar/Qualificar Dados", "Liberado Jurídico", "Documento Recusado",
        "Em Assinatura [E]", "Aguardando Termo Assinado", "Pendente", "Pendência Solicitante", "Pendência Cliente",
        "BackOffice",
    ],
    "3. Liberado p/ implantação": [
        "Fila de Implantação", "Configuração de ambiente", "Validação de Ambiente", "Integração",
        "Validação Checklist de Implantação", "Implantação Presencial", "Dependência externa", "Pendência DMS",
    ],
    "4. Onboarding": ["Fila Onboarding", "Kickoff", "Treinamento", "Hypercare", "Certificação", "Handoff"],
}
COR_QUADRO = {"2. Novas implantações": CIANO, "3. Liberado p/ implantação": AZUL, "4. Onboarding": LIMA_ESCURO}
QUADRO_DO_STATUS = {s: q for q, lista in QUADROS.items() for s in lista}
ORDEM_STATUS = [s for lista in QUADROS.values() for s in lista]
# Ordem do fluxo para os SLAs de etapa (nomes como estão no Jira, sem o "[HDI]")
ORDEM_ETAPAS_SLA = ["Aguardando termo assinado", "Fila Implantação", "Configuração de ambiente", "Integração",
                    "Validação", "Validação de Checklist", "Implantação presencial", "Dependência externa",
                    "Fila Onboarding", "Kickoff", "Treinamento", "Hypercare", "Certificação", "Handoff"]
ENCERRADOS = {"Implantação Finalizada", "Implantação Cancelada", "Implantação Não Autorizada", "Fechado Sem Resposta",
              "Resolvido", "Transferido para o Suporte"}


def agora() -> pd.Timestamp:
    """Data e hora de Brasília, sem fuso (o Streamlit Cloud roda em UTC)."""
    return pd.Timestamp.now(tz=FUSO).tz_localize(None)


SOMBRA = "0 1px 2px rgba(5,60,75,.06), 0 6px 18px rgba(5,60,75,.10)"
CSS = f"""
<style>
.block-container {{ padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1480px; }}
section[data-testid="stSidebar"] {{ background: #fff; box-shadow: 2px 0 14px rgba(5,60,75,.08); }}
[data-testid="stSidebarHeader"] img, [data-testid="stLogo"] {{ height: 2.6rem; max-width: 100%; }}

.hdr {{ background: {AZUL}; border-radius: 14px; padding: 20px 26px; display: flex; gap: 16px;
        justify-content: space-between; align-items: center; flex-wrap: wrap;
        box-shadow: 0 8px 24px rgba(7,140,169,.30); margin-bottom: 14px;
        border-bottom: 5px solid {LIMA}; }}
.hdr h1 {{ color: #fff; font-size: 30px; font-weight: 700; margin: 0; padding: 0; line-height: 1.15; }}
.hdr p {{ color: rgba(255,255,255,.88); margin: 4px 0 0; font-size: 14px; }}
.hdr .lido {{ background: rgba(255,255,255,.16); color: #fff; font-size: 13px; padding: 6px 12px;
              border-radius: 8px; white-space: nowrap; }}

.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); gap: 16px; margin: 4px 0 18px; }}
.kpi {{ background: #fff; border-radius: 12px; padding: 14px 18px 14px; box-shadow: {SOMBRA};
        border-left: 5px solid var(--c); }}
.kpi-t {{ font-size: 13px; color: {TINTA_SUAVE}; font-weight: 600; line-height: 1.3; }}
.kpi-v {{ font-size: 30px; font-weight: 700; color: {TINTA}; line-height: 1.15; margin-top: 6px;
          font-variant-numeric: tabular-nums; }}
.kpi-d {{ font-size: 12.5px; margin-top: 4px; color: {TINTA_SUAVE}; }}
.kpi-d.bom {{ color: {AZUL_ESCURO}; font-weight: 600; }}
.kpi-d.ruim {{ color: {ALERTA}; font-weight: 600; }}

div[class*="st-key-card_"] {{ background: #fff; border-radius: 12px; padding: 16px 18px 10px; box-shadow: {SOMBRA}; }}
.card-t {{ font-weight: 700; font-size: 16px; color: {TINTA}; line-height: 1.3; }}
.card-s {{ font-size: 12.5px; color: {TINTA_SUAVE}; margin-top: 2px; }}

/* abas: seletores por role valem para o Streamlit antigo (BaseWeb) e o novo (react-aria) */
.stTabs [role="tablist"] {{ gap: 4px; background: #fff; padding: 6px; border-radius: 12px; border: 0;
                            box-shadow: {SOMBRA}; margin-bottom: 14px; flex-wrap: wrap; }}
.stTabs [role="tab"] {{ height: 38px; padding: 0 16px; border-radius: 8px; display: flex; align-items: center; }}
.stTabs [role="tab"]:hover {{ background: #E8F4F7; }}
.stTabs [role="tab"][aria-selected="true"] {{ background: {AZUL}; }}
.stTabs [role="tab"][aria-selected="true"] p {{ color: #fff; font-weight: 600; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"],
.stTabs .react-aria-SelectionIndicator {{ display: none; }}

.hm-wrap {{ overflow-x: auto; }}
.hm {{ width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13.5px; color: {TINTA}; margin: 0; }}
.hm, .hm * {{ border: 0 !important; }}
.hm tr {{ background: transparent !important; }}
.hm th {{ text-align: right; font-weight: 600; color: {TINTA_SUAVE}; padding: 6px 10px; white-space: nowrap; }}
.hm th:first-child, .hm td.rot {{ text-align: left; }}
.hm td {{ padding: 7px 10px; }}
.hm td.n {{ text-align: right; font-variant-numeric: tabular-nums; min-width: 64px; box-shadow: inset 0 0 0 2px #fff; }}
.hm tbody td.rot {{ box-shadow: inset 0 -1px 0 {CINZA}; }}
.hm tfoot td {{ font-weight: 700; box-shadow: inset 0 2px 0 #D5DEE2 !important; }}

.login {{ max-width: 460px; margin: 8vh auto 0; background: #fff; border-radius: 14px; padding: 28px;
          box-shadow: {SOMBRA}; border-top: 5px solid {AZUL}; }}
.login h1 {{ font-size: 26px; margin: 0 0 6px; padding: 0; color: {TINTA}; }}
.login p {{ color: {TINTA_SUAVE}; margin: 0; }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# Logo no topo da barra lateral (também aparece na tela de login). Se o arquivo não existir, segue sem logo.
LOGO = Path(__file__).parent / "assets" / "logo_mobato.png"
if LOGO.exists():
    st.logo(str(LOGO), size="large")


def para_data(s: pd.Series) -> pd.Series:
    """Converte serial do Sheets (dias desde 1899-12-30) ou texto dd/mm/aaaa em datetime."""
    num = pd.to_numeric(s, errors="coerce")
    serial = pd.to_datetime(num, unit="D", origin="1899-12-30")
    texto = pd.to_datetime(s.where(num.isna()), format="%d/%m/%Y", errors="coerce")
    return serial.fillna(texto)


# =============================================================================
# ACESSO
# =============================================================================
def exigir_acesso() -> None:
    """Login Google + lista de e-mails em secrets ([acessos])."""
    if not st.user.is_logged_in:
        st.markdown("<div class='login'><h1>Implantação</h1>"
                    "<p>Entre com a sua conta Google da empresa para ver o painel.</p></div>",
                    unsafe_allow_html=True)
        _, meio, _ = st.columns([1, 1, 1])
        meio.button("Entrar com Google", on_click=st.login, type="primary", use_container_width=True)
        st.stop()
    acessos = {str(k).lower() for k in st.secrets["acessos"].keys()}
    if str(st.user.email).lower() not in acessos:
        st.error(f"{st.user.email} não tem acesso a este painel. Peça liberação ao time de dados.")
        st.button("Sair", on_click=st.logout)
        st.stop()


# =============================================================================
# DADOS
# =============================================================================
DATAS_IMPL = ["Data Criacao", "Data Onboarding", "Data Resolucao", "Status desde", "Data Ativacao",
              "Data Primeiro Uso (Jira)", "Data Hypercare", "Entrada Hypercare", "Saida Hypercare",
              "DATA_IMPLANTACAO", "DATA_PRIMEIRO_ATENDIMENTO", "DATA_ULTIMO_ATENDIMENTO"]
TEXTO_IMPL = ["Status", "Request Type", "Resumo", "Relator", "Responsável", "UF", "Cidade", "Marca",
              "Houve retrabalho", "Classificacao retrabalho", "Pesquisa recebida", "Avaliacao 7 dias",
              "Avaliacao 15 dias", "Avaliacao 30 dias", "Revenda_Banco", "Alerta_Cadastro"]
NUM_IMPL = ["Dias no status atual", "Total retrabalho", "Nota geral", "Satisfacao portal", "DIAS_IMPLANT_ATE_1O_ATEND",
            "QTD_ATENDIMENTO_CHECKLIST", "QTD_ATENDIMENTO_CHECKLIST_ACUMULADA", "QTD_ATENDIMENTO_ORCAMENTO",
            "QTD_ATENDIMENTO_ORCAMENTO_ACUMULADO", "QTD_OS", "QTD_OS_ACUMULADA"]
TEXTO_SUP = ["Resumo", "Status", "Status categoria", "Prioridade", "Request Type", "Motivo", "Relator",
             "Responsável", "UF", "Marca", "SLA_1a_Resposta_Violado", "SLA_Resolucao_Violado"]
NUM_SUP = ["SLA_1a_Resposta_h", "Tempo_1a_Resposta_h", "Tempo_Restante_1a_Resposta_h", "SLA_Resolucao_h",
           "Tempo_Resolucao_h", "Tempo_Restante_Resolucao_h", "Satisfacao portal"]


def tipar(df: pd.DataFrame, datas: list, textos: list, numeros: list) -> pd.DataFrame:
    for c in datas + textos + numeros:
        if c not in df.columns:
            df[c] = "" if c in textos else np.nan
    for c in datas:
        df[c] = para_data(df[c])
    for c in numeros + [c for c in df.columns if c.startswith(("Horas em '", "QTD_"))]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in textos:
        df[c] = df[c].fillna("").astype(str).str.strip().replace({"": "Não Informado", "nan": "Não Informado"})
    return df


@st.cache_data(ttl=900, show_spinner="Lendo a planilha de implantação…")
def carregar() -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    planilha = gc.open_by_key(st.secrets["sheets"]["id"])
    impl = pd.DataFrame(planilha.sheet1.get_all_records(value_render_option="UNFORMATTED_VALUE"))
    try:
        sup = pd.DataFrame(planilha.worksheet("SUPORTE").get_all_records(value_render_option="UNFORMATTED_VALUE"))
    except gspread.exceptions.WorksheetNotFound:
        sup = pd.DataFrame(columns=["Chamado", "Data Criacao", "Data Resolucao"])
    if len(impl) < 50:
        raise RuntimeError(f"A planilha veio com {len(impl)} linhas; provavelmente está sendo atualizada.")

    url_jira = st.secrets.get("jira", {}).get("url", "").rstrip("/")
    link = lambda d: (url_jira + "/browse/" + d["Chamado"].astype(str)) if url_jira else d["Chamado"].astype(str)  # noqa: E731

    impl = tipar(impl, DATAS_IMPL, TEXTO_IMPL, NUM_IMPL)
    impl = impl[impl["Data Criacao"].notna()].copy()
    impl["Quadro"] = impl["Status"].map(QUADRO_DO_STATUS).fillna("Outros")
    impl["Aberto"] = ~impl["Status"].isin(ENCERRADOS)
    impl["Inicio Hypercare"] = impl["Data Hypercare"].fillna(impl["Entrada Hypercare"])
    impl["Primeiro uso"] = impl["Data Primeiro Uso (Jira)"].fillna(impl["DATA_PRIMEIRO_ATENDIMENTO"])
    impl["Revenda"] = impl["Revenda_Banco"].where(impl["Revenda_Banco"] != "Não Informado", impl["Resumo"])
    impl["Link"] = link(impl)

    sup = tipar(sup, ["Data Criacao", "Data Resolucao"], TEXTO_SUP, NUM_SUP)
    sup = sup[sup["Data Criacao"].notna()].copy()
    sup["Aberto"] = sup["Data Resolucao"].isna() & (sup["Status categoria"] != "Itens concluídos")
    sup["SLA 1ª resposta OK"] = sup["SLA_1a_Resposta_Violado"].map({"Não": 1.0, "Sim": 0.0})
    sup["SLA resolução OK"] = sup["SLA_Resolucao_Violado"].map({"Não": 1.0, "Sim": 0.0}).where(~sup["Aberto"])
    sup["Link"] = link(sup)
    return impl, sup, agora()


# =============================================================================
# FORMATAÇÃO
# =============================================================================
def num(n: float) -> str:
    return "—" if pd.isna(n) else f"{n:,.0f}".replace(",", ".")


def pct(x: float) -> str:
    return "—" if pd.isna(x) else f"{x:.1%}".replace(".", ",")


def horas(x: float) -> str:
    return "—" if pd.isna(x) else f"{x:.1f} h".replace(".", ",")


def razao(a: float, b: float) -> float:
    return a / b if b else np.nan


def rotulo_mes(ts, parciais: set = frozenset()) -> str:
    ts = pd.Timestamp(ts)
    return f"{MESES[ts.month - 1]}/{ts:%y}" + ("*" if ts in parciais else "")


def meses_parciais(ini: pd.Timestamp, fim: pd.Timestamp) -> set:
    """Meses cortados pelo período escolhido (ou pelo dia de hoje)."""
    meses = pd.period_range(ini, fim, freq="M")
    ultimo_dia = min(fim, agora().normalize())
    return {m.to_timestamp() for m in meses
            if m.start_time.normalize() < ini or m.end_time.normalize() > ultimo_dia}


def delta_qtd(atual: float, anterior: float) -> tuple[str, str] | None:
    if not anterior:
        return None
    seta = "▲" if atual > anterior else "▼" if atual < anterior else "="
    return f"{seta} {num(abs(atual - anterior))} vs. anterior", ""


def delta_pp(atual: float, anterior: float, maior_melhor: bool = True) -> tuple[str, str] | None:
    if pd.isna(atual) or pd.isna(anterior):
        return None
    d = (atual - anterior) * 100
    tom = "" if abs(d) < 0.05 else ("bom" if (d > 0) == maior_melhor else "ruim")
    seta = "▲" if d > 0 else "▼" if d < 0 else "="
    return f"{seta} {abs(d):.1f} p.p. vs. anterior".replace(".", ",", 1), tom


def delta_h(atual: float, anterior: float) -> tuple[str, str] | None:
    if pd.isna(atual) or pd.isna(anterior):
        return None
    d = atual - anterior
    tom = "" if abs(d) < 0.05 else ("bom" if d < 0 else "ruim")
    seta = "▲" if d > 0 else "▼" if d < 0 else "="
    return f"{seta} {horas(abs(d))} vs. anterior", tom


# =============================================================================
# COMPONENTES VISUAIS
# =============================================================================
def kpis(itens: list[dict]) -> None:
    """Linha de cartões: dict(t=título, v=valor, d=(texto, tom) | None, c=cor da borda)."""
    blocos = []
    for i in itens:
        delta = ""
        if i.get("d"):
            texto, tom = i["d"]
            delta = f"<div class='kpi-d {tom}'>{html.escape(texto)}</div>"
        blocos.append(f"<div class='kpi' style='--c:{i.get('c', AZUL)}'>"
                      f"<div class='kpi-t'>{html.escape(i['t'])}</div>"
                      f"<div class='kpi-v'>{html.escape(i['v'])}</div>{delta}</div>")
    st.markdown(f"<div class='kpis'>{''.join(blocos)}</div>", unsafe_allow_html=True)


def card(chave: str, titulo: str, subtitulo: str | None = None):
    """Container branco com sombra e título; use com `with`."""
    c = st.container(key=f"card_{chave}")
    sub = f"<div class='card-s'>{html.escape(subtitulo)}</div>" if subtitulo else ""
    c.markdown(f"<div class='card-t'>{html.escape(titulo)}</div>{sub}", unsafe_allow_html=True)
    return c


def estilo(fig: go.Figure, altura: int = 300, legenda: bool = False, y_titulo: str | None = None) -> go.Figure:
    fig.update_layout(
        height=altura, margin=dict(l=4, r=8, t=28 if legenda else 12, b=4), separators=",.",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", bargap=0.28, bargroupgap=0.08,
        font=dict(family="Source Sans Pro, Source Sans 3, Segoe UI, sans-serif", color=TINTA, size=13),
        showlegend=legenda, legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title=None),
        hoverlabel=dict(bgcolor="#fff", font_color=TINTA),
    )
    fig.update_xaxes(showgrid=False, linecolor="#D5DEE2", title=None, tickfont=dict(color=TINTA_SUAVE))
    fig.update_yaxes(showgrid=True, gridcolor="#EDF1F3", zeroline=False, tickfont=dict(color=TINTA_SUAVE),
                     title=dict(text=y_titulo, font=dict(size=12, color=TINTA_SUAVE)) if y_titulo else None)
    return fig


def barras(x, y, cor=AZUL, horizontal=False, fmt="%{y:,.0f}", y_titulo=None, altura=300, contorno=None):
    texto = fmt.replace("%{y", "%{x") if horizontal else fmt
    fig = go.Figure(go.Bar(
        x=y if horizontal else x, y=x if horizontal else y, orientation="h" if horizontal else "v",
        marker=dict(color=cor, line=dict(color=contorno or cor, width=1 if contorno else 0)),
        texttemplate=texto, textposition="outside", cliponaxis=False,
        textfont=dict(color=TINTA, size=12.5), hovertemplate=texto + "<extra></extra>",
    ))
    fig = estilo(fig, altura, y_titulo=y_titulo)
    if horizontal:
        fig.update_yaxes(autorange="reversed", automargin=True)
        maior = max((len(str(r)) for r in x), default=10)
        fig.update_layout(margin=dict(l=min(340, 8 * maior), r=36))  # rótulos longos à esquerda, valor à direita
    return fig


def grafico(fig: go.Figure, chave: str) -> None:
    st.plotly_chart(fig, key=chave, config={"displayModeBar": False})


def ordenar(valores, ordem: list[str]) -> list[str]:
    presentes = set(valores)
    return [v for v in ordem if v in presentes] + sorted(presentes - set(ordem))



def selecao(fig: go.Figure, chave: str, eixo: str = "x") -> list:
    """Mostra o gráfico e devolve os rótulos das barras clicadas (vazio = nenhuma)."""
    evento = st.plotly_chart(fig, key=chave, on_select="rerun", selection_mode="points",
                             config={"displayModeBar": False})
    pontos = (evento or {}).get("selection", {}).get("points", []) if isinstance(evento, dict) else \
        getattr(getattr(evento, "selection", None), "points", []) or []
    return [p.get(eixo) for p in pontos if p.get(eixo) is not None]


def lista(df: pd.DataFrame, colunas: list, chave: str, altura: int = 320, extra: dict | None = None) -> None:
    config = {"Link": COL_LINK, "Data Criacao": COL_CRIADO, **(extra or {})}
    for c in colunas:
        if c.startswith("Data") or c in ("Inicio Hypercare", "Primeiro uso", "Status desde"):
            config.setdefault(c, st.column_config.DateColumn(c, format="DD/MM/YYYY"))
    st.dataframe(df[colunas], hide_index=True, height=min(altura, 38 + 35 * max(len(df), 1)), key=chave,
                 column_config=config)


COL_PCT_BARRA = st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)
COL_LINK = st.column_config.LinkColumn("Chamado", display_text=r"([A-Z]+-\d+)$")
COL_CRIADO = st.column_config.DateColumn("Criado em", format="DD/MM/YYYY")
COLS_CARD = ["Link", "Resumo", "Status", "Relator", "UF", "Marca"]


# =============================================================================
# APP
# =============================================================================
exigir_acesso()
try:
    impl_todos, sup_todos, lido_em = carregar()
except Exception as e:
    st.error(f"Não foi possível ler a planilha: {e}")
    st.button("Tentar de novo", on_click=carregar.clear)
    st.stop()

with st.sidebar:
    st.caption(f"Conectado como {st.user.email}")
    slot_alertas = st.container()  # botão "⚠️ N alertas", preenchido depois de aplicar os filtros
    min_d = min(impl_todos["Data Criacao"].min(), sup_todos["Data Criacao"].min()
                if not sup_todos.empty else impl_todos["Data Criacao"].min()).date()
    max_d = agora().date()
    periodo = st.date_input("Criados entre", (max(min_d, max_d - timedelta(days=180)), max_d),
                            min_value=min_d, max_value=max_d, format="DD/MM/YYYY")
    ini, fim = (periodo[0], periodo[-1]) if periodo else (min_d, max_d)

    def multi(rotulo: str, coluna: str) -> list:
        opcoes = sorted(set(impl_todos[coluna]) | set(sup_todos[coluna] if coluna in sup_todos else []))
        return st.multiselect(rotulo, opcoes, placeholder="Todos")

    f_marca, f_uf, f_relator = multi("Marca", "Marca"), multi("UF", "UF"), multi("Relator", "Relator")
    st.divider()
    if st.button("Recarregar dados", use_container_width=True):
        carregar.clear()
        st.rerun()
    st.button("Sair", on_click=st.logout, use_container_width=True)


def filtrar(df: pd.DataFrame) -> pd.DataFrame:
    for col, sel in (("Marca", f_marca), ("UF", f_uf), ("Relator", f_relator)):
        if sel and col in df.columns:
            df = df[df[col].isin(sel)]
    return df


ini_ts, fim_ts = pd.Timestamp(ini), pd.Timestamp(fim)
impl = filtrar(impl_todos)                                   # estado atual: sem corte de período
impl_per = impl[impl["Data Criacao"].between(ini_ts, fim_ts)]
sup = filtrar(sup_todos)
sup_per = sup[sup["Data Criacao"].between(ini_ts, fim_ts)]
hoje = agora().normalize()

# ---------- Alertas de entrada ----------
def calcular_alertas() -> dict:
    """Listas de cards que merecem atenção imediata (situação atual, com os filtros da barra lateral)."""
    onb = impl[impl["Status"].isin(QUADROS["4. Onboarding"]) & impl["Aberto"]].copy()
    inicio_onb = onb["Data Onboarding"].fillna(onb["Status desde"])
    onb["Dias em onboarding"] = (hoje - inicio_onb).dt.days
    ultimo = onb["DATA_ULTIMO_ATENDIMENTO"].where(onb["DATA_ULTIMO_ATENDIMENTO"] <= hoje)
    onb["Dias sem uso"] = (hoje - ultimo).dt.days
    nunca = onb["DATA_PRIMEIRO_ATENDIMENTO"].isna()
    onb["Situação de uso"] = np.where(nunca, "Nunca usou", "Sem atendimento recente")
    sem_uso = onb[(nunca & (onb["Dias em onboarding"] >= DIAS_ALERTA_ENTRADA))
                  | (onb["Dias sem uso"] >= DIAS_ALERTA_ENTRADA)]

    hyp = impl[(impl["Status"] == "Hypercare") & impl["Inicio Hypercare"].notna()].copy()
    hyp["Dias em Hypercare"] = (hoje - hyp["Inicio Hypercare"]).dt.days
    hyp = hyp[hyp["Dias em Hypercare"] >= DIAS_ALERTA_ENTRADA]
    hyp["Avaliações pendentes"] = [
        ", ".join(f"{m} dias" for m in (7, 15, 30) if d >= m and r[f"Avaliacao {m} dias"] != "Sim") or "—"
        for d, (_, r) in zip(hyp["Dias em Hypercare"], hyp.iterrows())]

    parados = impl[impl["Aberto"] & (impl["Quadro"] != "Outros")
                   & (impl["Dias no status atual"] >= DIAS_PARADO_CRITICO)]
    sup_estourado = sup[sup["Aberto"] & (sup["Tempo_Restante_Resolucao_h"] < 0)].copy()
    sup_estourado["Dias em aberto"] = (hoje - sup_estourado["Data Criacao"]).dt.days
    cadastro = impl[impl["Alerta_Cadastro"] != "Não Informado"]
    return {
        f"Onboarding sem uso há {DIAS_ALERTA_ENTRADA}+ dias": (
            sem_uso.sort_values("Dias em onboarding", ascending=False),
            ["Link", "Revenda", "Status", "Situação de uso", "Dias em onboarding", "Dias sem uso", "Relator", "UF"]),
        f"Em Hypercare há {DIAS_ALERTA_ENTRADA}+ dias": (
            hyp.sort_values("Dias em Hypercare", ascending=False),
            ["Link", "Resumo", "Inicio Hypercare", "Dias em Hypercare", "Avaliações pendentes", "Relator", "UF"]),
        f"Parados na mesma fila há {DIAS_PARADO_CRITICO}+ dias": (
            parados.sort_values("Dias no status atual", ascending=False),
            ["Link", "Resumo", "Status", "Dias no status atual", "Responsável", "Relator"]),
        "Suporte com SLA de resolução estourado": (
            sup_estourado.sort_values("Dias em aberto", ascending=False),
            ["Link", "Resumo", "Motivo", "Status", "Dias em aberto", "Responsável"]),
        "Alertas de cadastro no banco": (
            cadastro, ["Link", "Resumo", "Status", "Alerta_Cadastro", "Relator"]),
    }


@st.dialog("Alertas de hoje", width="large")
def popup_alertas(alertas: dict) -> None:
    ativos = {k: v for k, v in alertas.items() if len(v[0])}
    st.markdown(f"**{len(ativos)} tipo(s) de alerta** com cards que precisam de atenção. Clique para ver a lista.")
    for i, (titulo, (df, cols)) in enumerate(ativos.items()):
        with st.expander(f"{titulo}: {num(len(df))}"):
            lista(df, cols, f"pop_{i}", altura=360)
    st.caption("Os mesmos dados estão nas abas Funil, Onboarding, Suporte e Uso da ferramenta.")
    if st.button("Entendi", type="primary"):
        st.rerun()


alertas_entrada = calcular_alertas()
total_tipos = sum(1 for df, _ in alertas_entrada.values() if len(df))
if total_tipos:
    reabrir = slot_alertas.button(f"⚠️ {total_tipos} tipo(s) de alerta", use_container_width=True)
    if reabrir or not st.session_state.get("alertas_vistos"):
        st.session_state.alertas_vistos = True  # fechar no X também conta como visto
        popup_alertas(alertas_entrada)

st.markdown(
    f"<div class='hdr'><div><h1>Implantação e Onboarding</h1>"
    f"<p>Período de {ini:%d/%m/%Y} a {fim:%d/%m/%Y}. Filas e alertas mostram a situação atual.</p></div>"
    f"<div class='lido'>Base lida às {lido_em:%H:%M}</div></div>",
    unsafe_allow_html=True,
)
aba_funil, aba_onb, aba_sup, aba_uso, aba_analista, aba_cards = st.tabs(
    ["Funil (Quadros 2 a 4)", "Onboarding (Quadro 4)", "Suporte (Quadro 5)", "Uso da ferramenta", "Por analista",
     "Cards"])

# ---------- Funil ----------
with aba_funil:
    abertos = impl[impl["Aberto"] & (impl["Quadro"] != "Outros")]  # só os Quadros 2, 3 e 4
    parados = abertos[abertos["Dias no status atual"] >= DIAS_ALERTA]
    kpis([dict(t="Em andamento", v=num(len(abertos)))]
         + [dict(t=q, v=num((abertos["Quadro"] == q).sum()), c=COR_QUADRO[q]) for q in QUADROS]
         + [dict(t=f"Parados há {DIAS_ALERTA}+ dias", v=num(len(parados)), c=ALERTA)])

    with card("q_fila", "1. Chamados por fila agora", "Chamados não concluídos. Clique numa barra para ver os cards"):
        por = abertos.groupby("Status").size().reindex([s for s in ORDEM_STATUS if s in set(abertos["Status"])]
                                                       + sorted(set(abertos["Status"]) - set(ORDEM_STATUS)))
        cores = [COR_QUADRO.get(QUADRO_DO_STATUS.get(s), CIANO) for s in por.index]
        fig = barras(por.index, por.values, cor=cores, y_titulo="Chamados", altura=320)
        fig.update_xaxes(tickangle=-30)
        clicados = selecao(fig, "g_fila")
        sel = abertos[abertos["Status"].isin(clicados)] if clicados else abertos
        st.caption(f"{'Fila: ' + ', '.join(clicados) if clicados else 'Todas as filas'} ({num(len(sel))} cards)")
        lista(sel.sort_values("Dias no status atual", ascending=False),
              COLS_CARD + ["Quadro", "Dias no status atual"], "l_fila")

    with card("q_tempo", "2. Tempo médio em cada fila",
              "Dias, entre os cards criados no período que passaram pela fila (inclui os que já saíram). "
              "Clique numa barra para ver os cards"):
        linhas = []
        for s in ORDEM_STATUS:
            col = f"Horas em '{s}'"
            if col in impl_per.columns:
                passou = impl_per[impl_per[col] > 0]
                if len(passou):
                    linhas.append((s, passou[col].mean() / 24, len(passou)))
        tempos = pd.DataFrame(linhas, columns=["Fila", "Dias", "Cards"])
        if tempos.empty:
            st.info("Nenhum card no período.")
        else:
            fig = barras(tempos["Fila"], tempos["Dias"], cor=[COR_QUADRO.get(QUADRO_DO_STATUS.get(s), CIANO)
                                                               for s in tempos["Fila"]],
                         fmt="%{y:.1f}", y_titulo="Dias (média)", altura=320)
            fig.update_xaxes(tickangle=-30)
            clicados = selecao(fig, "g_tempo")
            fila_sel = clicados[0] if clicados else tempos.sort_values("Dias").iloc[-1]["Fila"]
            col = f"Horas em '{fila_sel}'"
            passou = impl_per[impl_per[col] > 0].assign(**{"Dias na fila": lambda d: (d[col] / 24).round(1)})
            st.caption(f"Cards que passaram por **{fila_sel}**"
                       + ("" if clicados else " (a fila mais demorada; clique em outra barra para trocar)"))
            lista(passou.sort_values("Dias na fila", ascending=False), COLS_CARD + ["Dias na fila"], "l_tempo")

    etapas = [m.group(1) for c in impl.columns
              if (m := re.match(r"^Estourou (.+)$", c)) and m.group(1) not in {"Treinamento"}]
    etapas.sort(key=lambda e: ORDEM_ETAPAS_SLA.index(e) if e in ORDEM_ETAPAS_SLA else len(ORDEM_ETAPAS_SLA))
    if etapas:
        with card("q_sla_etapa", "SLA por etapa (meta do Jira)",
                  "Cards criados no período que já tiveram o SLA da etapa medido. "
                  "Clique numa barra para ver os que estouraram"):
            linhas = []
            for etapa in etapas:
                est = impl_per[f"Estourou {etapa}"].astype(str)
                medidos = est.isin(["Sim", "Não"])
                if medidos.any():
                    linhas.append((etapa, (est[medidos] == "Não").mean() * 100, int(medidos.sum()),
                                   int((est == "Sim").sum())))
            res_etapa = pd.DataFrame(linhas, columns=["Etapa", "No prazo (%)", "Medidos", "Estourados"])
            if res_etapa.empty:
                st.info("Nenhum card do período com SLA de etapa medido.")
            else:
                fig = barras(res_etapa["Etapa"], res_etapa["No prazo (%)"], cor=LIMA, contorno=LIMA_ESCURO,
                             fmt="%{y:.0f}%", y_titulo="% no prazo", altura=300)
                fig.update_yaxes(range=[0, 110])
                clicados = selecao(fig, "g_sla_etapa")
                etapa_sel = clicados[0] if clicados else res_etapa.sort_values("No prazo (%)").iloc[0]["Etapa"]
                estourados = impl_per[impl_per[f"Estourou {etapa_sel}"].astype(str) == "Sim"].copy()
                estourados["Tempo na etapa (h)"] = pd.to_numeric(estourados[f"SLA {etapa_sel} (h)"], errors="coerce")
                st.caption(f"Estouraram o SLA de **{etapa_sel}**: {num(len(estourados))} de "
                           f"{num(res_etapa.set_index('Etapa').loc[etapa_sel, 'Medidos'])} medidos"
                           + ("" if clicados else " (a etapa com pior resultado; clique em outra barra para trocar)"))
                lista(estourados.sort_values("Tempo na etapa (h)", ascending=False),
                      COLS_CARD + ["Tempo na etapa (h)"], "l_sla_etapa",
                      extra={"Tempo na etapa (h)": st.column_config.NumberColumn(format="%.1f")})

    with card("q_alerta", "4. Alerta de chamado parado na mesma fila",
              "Chamados abertos sem trocar de fila, do mais antigo para o mais recente"):
        limite = st.slider("Dias sem sair da fila", 3, 60, DIAS_ALERTA, key="lim_alerta")
        par = abertos[abertos["Dias no status atual"] >= limite].copy()
        par["Faixa"] = pd.cut(par["Dias no status atual"], [-1, 14, 29, np.inf],
                              labels=["7 a 14 dias", "15 a 29 dias", "30 dias ou mais"])
        kpis([dict(t="7 a 14 dias", v=num((par["Faixa"] == "7 a 14 dias").sum()), c=CIANO),
              dict(t="15 a 29 dias", v=num((par["Faixa"] == "15 a 29 dias").sum()), c=AZUL_ESCURO),
              dict(t="30 dias ou mais", v=num((par["Faixa"] == "30 dias ou mais").sum()), c=ALERTA)])
        lista(par.sort_values("Dias no status atual", ascending=False),
              ["Link", "Resumo", "Status", "Dias no status atual", "Faixa", "Status desde", "Relator", "UF"], "l_alerta")

# ---------- Onboarding ----------
with aba_onb:
    st.caption("Indicadores dos cards criados no período escolhido, exceto o Hypercare, que mostra a situação atual.")

    with card("q_pesq", "3. Pesquisa de satisfação"):
        enviadas = impl_per[impl_per["Pesquisa recebida"].isin(["Sim", "Não"])]
        respondidas = impl_per[impl_per["Pesquisa recebida"] == "Sim"]
        kpis([dict(t="Pesquisas respondidas", v=num(len(respondidas))),
              dict(t="Nota média", v="—" if respondidas["Nota geral"].isna().all()
                   else f"{respondidas['Nota geral'].mean():.1f}".replace(".", ","), c=LIMA),
              dict(t="Taxa de resposta", v=pct(razao(len(respondidas), len(enviadas))), c=CIANO)])
        lista(respondidas.sort_values("Nota geral"), COLS_CARD + ["Nota geral"], "l_pesq",
              extra={"Nota geral": st.column_config.NumberColumn("Nota", format="%.1f")})

    with card("q_retrab", "5. Retrabalho"):
        com = impl_per[impl_per["Houve retrabalho"] == "Sim"]
        kpis([dict(t="Chamados com retrabalho", v=num(len(com)), c=ALERTA),
              dict(t="Ocorrências de retrabalho", v=num(com["Total retrabalho"].fillna(1).sum()), c=AZUL_ESCURO),
              dict(t="% dos chamados do período", v=pct(razao(len(com), len(impl_per))), c=CIANO)])
        lista(com.sort_values("Total retrabalho", ascending=False),
              COLS_CARD + ["Total retrabalho", "Classificacao retrabalho"], "l_retrab")

    with card("q_uso", "6. Revendas sem registro de primeiro uso",
              "Ativadas (saíram de Implantação Presencial) e sem data de primeiro uso no Jira nem no banco"):
        sem_uso = impl[impl["Data Ativacao"].notna() & impl["Primeiro uso"].isna()
                       & ~impl["Status"].isin(["Implantação Cancelada", "Implantação Não Autorizada"])].copy()
        sem_uso["Dias desde a ativação"] = (hoje - sem_uso["Data Ativacao"]).dt.days
        kpis([dict(t="Revendas sem primeiro uso", v=num(len(sem_uso)), c=ALERTA),
              dict(t="Há mais de 15 dias", v=num((sem_uso["Dias desde a ativação"] > 15).sum()), c=AZUL_ESCURO)])
        lista(sem_uso.sort_values("Dias desde a ativação", ascending=False),
              ["Link", "Revenda", "UF", "Relator", "Data Ativacao", "Dias desde a ativação", "Status"], "l_semuso")

    with card("q_hyper", "7. Acompanhamento do Hypercare",
              "Marcos de 7, 15 e 30 dias contados a partir do início do Hypercare"):
        hyp = impl[(impl["Status"] == "Hypercare") | impl["Saida Hypercare"].notna()].copy()
        hyp = hyp[hyp["Inicio Hypercare"].notna()]
        hyp["Em Hypercare"] = hyp["Status"] == "Hypercare"
        hyp["Dias em Hypercare"] = ((hyp["Saida Hypercare"].where(~hyp["Em Hypercare"], hoje)
                                     - hyp["Inicio Hypercare"]).dt.days)
        tabela, pend = {}, []
        for m in (7, 15, 30):
            feito = hyp[f"Avaliacao {m} dias"] == "Sim"
            dentro = hyp["Em Hypercare"]
            tabela[f"{m} dias"] = {
                "Total em Hypercare": int(dentro.sum()),
                "No prazo": int((dentro & ~feito & (hyp["Dias em Hypercare"] < m)).sum()),
                "Atrasada": int((dentro & ~feito & (hyp["Dias em Hypercare"] >= m)).sum()),
                "Realizada": int((dentro & feito).sum()),
            }
            pend.append(np.where(dentro & ~feito & (hyp["Dias em Hypercare"] >= m), f"{m} dias", ""))
        completos = ~hyp["Em Hypercare"] & (hyp[[f"Avaliacao {m} dias" for m in (7, 15, 30)]] == "Sim").all(axis=1)
        resumo_h = pd.DataFrame(tabela)
        resumo_h.loc["Encerrado completo"] = int(completos.sum())
        resumo_h.loc["Encerrado incompleto"] = int((~hyp["Em Hypercare"] & ~completos).sum())
        st.dataframe(resumo_h, use_container_width=True)
        hyp["Avaliação atrasada"] = [", ".join(p for p in trio if p) for trio in zip(*pend)] if len(hyp) else []
        atrasados = hyp[hyp["Avaliação atrasada"] != ""]
        st.markdown(f"**Fila de alerta: avaliações atrasadas** ({num(len(atrasados))})")
        lista(atrasados.sort_values("Dias em Hypercare", ascending=False),
              ["Link", "Resumo", "UF", "Relator", "Inicio Hypercare", "Dias em Hypercare", "Avaliação atrasada"],
              "l_hyp_atr")
        incompletos = hyp[~hyp["Em Hypercare"] & ~completos]
        with st.expander(f"Saíram do Hypercare com avaliação em branco ({num(len(incompletos))})"):
            lista(incompletos, ["Link", "Resumo", "UF", "Relator", "Inicio Hypercare", "Saida Hypercare",
                                "Avaliacao 7 dias", "Avaliacao 15 dias", "Avaliacao 30 dias"], "l_hyp_inc")
        with st.expander(f"Todos em Hypercare agora ({num(int(hyp['Em Hypercare'].sum()))})"):
            lista(hyp[hyp["Em Hypercare"]], ["Link", "Resumo", "UF", "Relator", "Inicio Hypercare",
                                             "Dias em Hypercare", "Avaliacao 7 dias", "Avaliacao 15 dias",
                                             "Avaliacao 30 dias"], "l_hyp_todos")

# ---------- Suporte ----------
with aba_sup:
    if sup.empty:
        st.info("A aba SUPORTE ainda não existe na planilha. Rode o integracao_implantacao.py atualizado.")
    else:
        mes_atual = hoje.to_period("M")
        no_mes = sup[sup["Data Criacao"].dt.to_period("M") == mes_atual]
        mes_ant = sup[sup["Data Criacao"].dt.to_period("M") == mes_atual - 1]
        var = razao(len(no_mes) - len(mes_ant), len(mes_ant))
        resolvidos = sup_per[~sup_per["Aberto"]]
        kpis([
            dict(t=f"8. Chamados em {rotulo_mes(mes_atual.to_timestamp())}", v=num(len(no_mes)),
                 d=None if pd.isna(var) else (f"{'▲' if var > 0 else '▼'} {abs(var):.0%} vs. mês anterior"
                                             .replace(".", ","), "")),
            dict(t="9. 1ª resposta no SLA", v=pct(sup_per["SLA 1ª resposta OK"].mean()), c=LIMA,
                 d=(f"tempo médio {horas(sup_per['Tempo_1a_Resposta_h'].mean())}", "")),
            dict(t="10. Resolução no SLA", v=pct(resolvidos["SLA resolução OK"].mean()), c=LIMA,
                 d=(f"tempo médio {horas(resolvidos['Tempo_Resolucao_h'].mean())}", "")),
            dict(t="Em aberto agora", v=num(sup["Aberto"].sum()), c=CIANO),
        ])
        with st.expander(f"Chamados de {rotulo_mes(mes_atual.to_timestamp())} ({num(len(no_mes))})"):
            lista(no_mes.sort_values("Data Criacao", ascending=False),
                  ["Link", "Resumo", "Status", "Motivo", "Relator", "UF", "Data Criacao"], "l_mes")

        e, d = st.columns(2, gap="medium")
        with e, card("q_sla1", "9. 1ª resposta fora do prazo ou em risco",
                     "Fora do prazo no período e abertos com menos de 25% do prazo restante"):
            risco1 = sup[sup["Aberto"] & (sup["Tempo_Restante_1a_Resposta_h"] <= 0.25 * sup["SLA_1a_Resposta_h"])]
            fora1 = pd.concat([sup_per[sup_per["SLA_1a_Resposta_Violado"] == "Sim"], risco1]).drop_duplicates("Chamado")
            fora1["Situação"] = np.where(fora1["SLA_1a_Resposta_Violado"] == "Sim", "Fora do prazo", "Em risco")
            lista(fora1.sort_values("Data Criacao", ascending=False),
                  ["Link", "Situação", "Relator", "Tempo_1a_Resposta_h", "Status"], "l_sla1",
                  extra={"Tempo_1a_Resposta_h": st.column_config.NumberColumn("Tempo decorrido (h)", format="%.1f")})
        with d, card("q_sla2", "10. Resolução estourada ou próxima de estourar",
                     "Abertos com o prazo estourado ou com menos de 25% restante"):
            risco2 = sup[sup["Aberto"] & (sup["Tempo_Restante_Resolucao_h"] <= 0.25 * sup["SLA_Resolucao_h"])].copy()
            risco2["Situação"] = np.where(risco2["Tempo_Restante_Resolucao_h"] < 0, "Estourado", "Próximo de estourar")
            risco2["Dias em aberto"] = (hoje - risco2["Data Criacao"]).dt.days
            lista(risco2.sort_values("Dias em aberto", ascending=False),
                  ["Link", "Situação", "Status", "Relator", "Dias em aberto"], "l_sla2")

        with card("q_motivo", "11. Motivos dos chamados", "Chamados do período. Clique numa barra para ver os cards"):
            mot = sup_per["Motivo"].value_counts()
            if mot.empty:
                st.info("Nenhum chamado no período.")
            else:
                rot = [f"{m} ({v / mot.sum():.0%})" for m, v in mot.items()]
                fig = barras(rot, mot.values, cor=AZUL, horizontal=True, fmt="%{y:,.0f}",
                             altura=max(260, 34 * len(mot)))
                clicados = selecao(fig, "g_motivo", eixo="y")
                escolhidos = [re.sub(r" \(\d+%\)$", "", c) for c in clicados]
                sel = sup_per[sup_per["Motivo"].isin(escolhidos)] if escolhidos else sup_per
                st.caption(f"{', '.join(escolhidos) if escolhidos else 'Todos os motivos'} ({num(len(sel))} cards)")
                lista(sel.sort_values("Data Criacao", ascending=False),
                      ["Link", "Resumo", "Motivo", "Status", "Relator", "UF", "Data Criacao"], "l_motivo")

        with card("q_evol", "12. Recebidos × resolvidos nos últimos 12 meses",
                  "Barras: chamados criados e resolvidos em cada mês. Linha: saldo em aberto no fim do mês. "
                  "Clique num mês para ver os cards recebidos"):
            meses = pd.period_range(mes_atual - 11, mes_atual, freq="M")
            receb = sup["Data Criacao"].dt.to_period("M").value_counts().reindex(meses, fill_value=0)
            resol = sup["Data Resolucao"].dt.to_period("M").value_counts().reindex(meses, fill_value=0)
            saldo = [((sup["Data Criacao"].dt.to_period("M") <= m)
                      & ~(sup["Data Resolucao"].dt.to_period("M") <= m)).sum() for m in meses]
            rot = [rotulo_mes(m.to_timestamp()) for m in meses]
            fig = go.Figure()
            fig.add_bar(name="Recebidos", x=rot, y=receb.values, marker_color=AZUL, texttemplate="%{y}",
                        textposition="outside", cliponaxis=False)
            fig.add_bar(name="Resolvidos", x=rot, y=resol.values, marker=dict(color=LIMA, line=dict(color=LIMA_ESCURO, width=1)),
                        texttemplate="%{y}", textposition="outside", cliponaxis=False)
            fig.add_scatter(name="Saldo em aberto", x=rot, y=saldo, mode="lines+markers",
                            line=dict(color=ALERTA, width=3))
            fig.update_layout(barmode="group")
            clicados = selecao(estilo(fig, 340, legenda=True, y_titulo="Chamados"), "g_evol")
            if clicados:
                mes_sel = meses[rot.index(clicados[0])]
                rec_mes = sup[sup["Data Criacao"].dt.to_period("M") == mes_sel]
                st.caption(f"Recebidos em {clicados[0]} ({num(len(rec_mes))})")
                lista(rec_mes.sort_values("Data Criacao"), ["Link", "Resumo", "Motivo", "Status", "Relator",
                                                            "Data Criacao", "Data Resolucao"], "l_evol")

# ---------- Uso da ferramenta ----------
with aba_uso:
    st.caption("Uso vindo do banco da Mobato, ligado ao card pelo CNPJ. Os números \"do mês\" vão do dia 1 até hoje; "
               "a aderência compara checklists de recepção com as passagens de chassi no DMS no mesmo período.")
    escopo_uso = st.segmented_control(
        "Mostrar", ["Em onboarding (Quadro 4)", "Implantação finalizada", "Todas com revenda no banco"],
        default="Em onboarding (Quadro 4)", key="escopo_uso") or "Em onboarding (Quadro 4)"
    rev = impl[impl["Revenda_Banco"] != "Não Informado"].copy()
    if escopo_uso.startswith("Em onboarding"):
        rev = rev[rev["Status"].isin(QUADROS["4. Onboarding"])]
    elif escopo_uso.startswith("Implantação finalizada"):
        rev = rev[rev["Status"] == "Implantação Finalizada"]

    ultimo = rev["DATA_ULTIMO_ATENDIMENTO"].where(rev["DATA_ULTIMO_ATENDIMENTO"] <= hoje)  # ignora datas futuras
    rev["Dias sem uso"] = (hoje - ultimo).dt.days
    rev["Uso acumulado"] = rev[["QTD_ATENDIMENTO_CHECKLIST_ACUMULADA", "QTD_ATENDIMENTO_ORCAMENTO_ACUMULADO",
                                "QTD_OS_ACUMULADA"]].fillna(0).sum(axis=1)
    passagens = rev["QTD_PASSAGENS_CHASSI"].where(rev["QTD_PASSAGENS_CHASSI"] > 0)
    rev["Aderência"] = (rev["QTD_CHECKLIST_RECEPCAO"] / passagens * 100).clip(upper=150)
    rev["OS Mobato ÷ DMS"] = rev["QTD_OS"] / rev["QTD_OS_DMS"].where(rev["QTD_OS_DMS"] > 0) * 100
    rev["Situação de uso"] = np.select(
        [rev["DATA_PRIMEIRO_ATENDIMENTO"].isna(),  # nenhum atendimento de qualquer tipo no banco
         rev["Dias sem uso"] > DIAS_SEM_USO,
         rev["Aderência"] < META_ADERENCIA * 100],
        ["Nunca usou", f"Parado há mais de {DIAS_SEM_USO} dias", f"Aderência abaixo de {META_ADERENCIA:.0%}"],
        default="Em uso")

    kpis([
        dict(t="Revendas", v=num(len(rev))),
        dict(t="Nunca usaram", v=num((rev["Situação de uso"] == "Nunca usou").sum()), c=ALERTA),
        dict(t=f"Paradas há mais de {DIAS_SEM_USO} dias", v=num(rev["Situação de uso"].str.startswith("Parado").sum()),
             c=AZUL_ESCURO),
        dict(t=f"Aderência abaixo de {META_ADERENCIA:.0%}", v=num(rev["Situação de uso"].str.startswith("Aderência").sum()),
             c=CIANO),
        dict(t="Aderência mediana no mês", v="—" if rev["Aderência"].isna().all()
             else f"{rev['Aderência'].median():.0f}%", c=LIMA),
    ])

    alerta_uso = rev[rev["Situação de uso"] != "Em uso"]
    ordem_sit = {"Nunca usou": 0, f"Parado há mais de {DIAS_SEM_USO} dias": 1}
    with card("q_uso_alerta", "Alerta de uso", f"{escopo_uso}: revendas que nunca usaram, estão sem atendimento há mais "
              f"de {DIAS_SEM_USO} dias ou com aderência abaixo da meta"):
        if alerta_uso.empty:
            st.success("Nenhuma revenda em alerta neste recorte.")
        else:
            lista(alerta_uso.assign(_o=alerta_uso["Situação de uso"].map(ordem_sit).fillna(2))
                  .sort_values(["_o", "Dias sem uso"], ascending=[True, False]),
                  ["Link", "Revenda_Banco", "Situação de uso", "Status", "Dias no status atual", "DATA_PRIMEIRO_ATENDIMENTO",
                   "DATA_ULTIMO_ATENDIMENTO", "Dias sem uso", "Aderência", "QTD_CHECKLIST_RECEPCAO",
                   "QTD_PASSAGENS_CHASSI", "Relator", "UF"], "l_uso_alerta", altura=420,
                  extra={"Revenda_Banco": "Revenda",
                         "DATA_PRIMEIRO_ATENDIMENTO": st.column_config.DateColumn("1º atendimento", format="DD/MM/YYYY"),
                         "DATA_ULTIMO_ATENDIMENTO": st.column_config.DateColumn("Último atendimento", format="DD/MM/YYYY"),
                         "Dias no status atual": "Dias na etapa",
                         "Aderência": st.column_config.ProgressColumn("Aderência no mês", format="%.0f%%",
                                                                      min_value=0, max_value=100),
                         "QTD_CHECKLIST_RECEPCAO": "Checklists no mês", "QTD_PASSAGENS_CHASSI": "Passagens no mês"})

    e, d = st.columns([3, 2], gap="medium")
    with e, card("q_uso_ader", "Aderência no mês por revenda",
                 f"Checklists de recepção ÷ passagens de chassi no DMS. Linha tracejada: meta de {META_ADERENCIA:.0%}. "
                 "Clique numa barra para ver o detalhe"):
        ad = rev.dropna(subset=["Aderência"]).sort_values("Aderência").head(25)
        if ad.empty:
            st.info("Nenhuma revenda com passagens no DMS neste mês.")
        else:
            nomes_ad = ad["Revenda_Banco"].str.slice(0, 32).tolist()
            fig = barras(nomes_ad, ad["Aderência"].values, horizontal=True, fmt="%{y:.0f}%",
                         cor=[ALERTA if v < META_ADERENCIA * 100 else LIMA for v in ad["Aderência"]],
                         altura=max(300, 26 * len(ad)))
            fig.add_vline(x=META_ADERENCIA * 100, line_dash="dash", line_color=TINTA_SUAVE)
            clicados = selecao(fig, "g_uso_ader", eixo="y")
            if clicados:
                det = ad[ad["Revenda_Banco"].str.slice(0, 32).isin(clicados)]
                lista(det, ["Link", "Revenda_Banco", "Status", "QTD_CHECKLIST_RECEPCAO", "QTD_PASSAGENS_CHASSI",
                            "QTD_OS", "QTD_OS_DMS", "DATA_ULTIMO_ATENDIMENTO"], "l_uso_ader",
                      extra={"Revenda_Banco": "Revenda", "QTD_CHECKLIST_RECEPCAO": "Checklists",
                             "QTD_PASSAGENS_CHASSI": "Passagens", "QTD_OS": "OS Mobato", "QTD_OS_DMS": "OS DMS"})
    with d, card("q_uso_mod", "Uso por módulo no mês", "Soma das revendas do recorte"):
        mods = pd.Series({rot: rev[col].sum() for col, rot in MODULOS_USO.items() if col in rev}).sort_values(ascending=False)
        grafico(barras(mods.index, mods.values, cor=AZUL, horizontal=True, fmt="%{y:,.0f}",
                       altura=max(300, 34 * len(mods))), "g_uso_mod")

    with card("q_usotab", "Uso por revenda", "Mês corrente por módulo, acumulado e datas de uso"):
        cols_mod = [c for c in MODULOS_USO if c in rev.columns]
        lista(rev.sort_values("Dias sem uso", ascending=False),
              ["Link", "Revenda_Banco", "Situação de uso", "Status", "Marca", "UF", "DATA_IMPLANTACAO",
               "DATA_PRIMEIRO_ATENDIMENTO", "DIAS_IMPLANT_ATE_1O_ATEND", "DATA_ULTIMO_ATENDIMENTO", "Dias sem uso",
               "Aderência", "OS Mobato ÷ DMS"] + cols_mod + ["Uso acumulado"], "l_uso", altura=520,
              extra={"Revenda_Banco": "Revenda",
                     "DATA_IMPLANTACAO": st.column_config.DateColumn("Implantação", format="DD/MM/YYYY"),
                     "DATA_PRIMEIRO_ATENDIMENTO": st.column_config.DateColumn("1º atendimento", format="DD/MM/YYYY"),
                     "DATA_ULTIMO_ATENDIMENTO": st.column_config.DateColumn("Último atendimento", format="DD/MM/YYYY"),
                     "DIAS_IMPLANT_ATE_1O_ATEND": "Dias até o 1º atend.",
                     "Aderência": st.column_config.NumberColumn("Aderência no mês", format="%.0f%%"),
                     "OS Mobato ÷ DMS": st.column_config.NumberColumn(format="%.0f%%"),
                     **{c: MODULOS_USO[c] for c in cols_mod}})

    alertas = impl[impl["Alerta_Cadastro"] != "Não Informado"]
    if len(alertas):
        with card("q_cad", "Alertas de cadastro", "Em etapa que exige a revenda criada no banco, mas o CNPJ não foi encontrado"):
            lista(alertas, ["Link", "Resumo", "Status", "Relator", "UF", "Alerta_Cadastro"], "l_cad")


# ---------- Por analista ----------
ETAPAS_ANALISTA = [m.group(1) for c in impl.columns
                   if (m := re.match(r"^Estourou (.+)$", c)) and m.group(1) not in {"Treinamento"}]


def etapas_medidas(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Por card: etapas com SLA medido, etapas estouradas e os nomes das estouradas."""
    if not ETAPAS_ANALISTA:
        vazio = pd.Series(0, index=df.index)
        return vazio, vazio, pd.Series("", index=df.index)
    est = df[[f"Estourou {e}" for e in ETAPAS_ANALISTA]].astype(str)
    est.columns = ETAPAS_ANALISTA
    nomes = est.apply(lambda r: ", ".join(e for e in ETAPAS_ANALISTA if r[e] == "Sim"), axis=1)
    return est.isin(["Sim", "Não"]).sum(axis=1), (est == "Sim").sum(axis=1), nomes


def estourou_suporte(df: pd.DataFrame) -> pd.Series:
    return (df["SLA_1a_Resposta_Violado"].eq("Sim") | df["SLA_Resolucao_Violado"].eq("Sim")
            | (df["Aberto"] & (df["Tempo_Restante_Resolucao_h"] < 0)))


def do_analista(df: pd.DataFrame, nome: str) -> pd.Series:
    """Cards cujo responsável começa com o nome configurado ("Lia" pega "Lia Souza", por exemplo)."""
    return df["Responsável"].str.lower().str.startswith(nome.lower())


def resumo_impl(g: pd.DataFrame, g_per: pd.DataFrame) -> dict:
    abertos_g = g[g["Aberto"] & (g["Quadro"] != "Outros")]
    finalizados = g[(g["Status"] == "Implantação Finalizada") & g["Data Resolucao"].between(ini_ts, fim_ts)]
    medidos, estourados, _ = etapas_medidas(g_per)
    return {
        "Em andamento": len(abertos_g),
        f"Parados {DIAS_ALERTA}+ dias": int((abertos_g["Dias no status atual"] >= DIAS_ALERTA).sum()),
        "Criados no período": len(g_per),
        "Finalizados no período": len(finalizados),
        "Dias médios de implantação": (finalizados["Data Resolucao"] - finalizados["Data Criacao"]).dt.days.mean(),
        "SLA de etapa no prazo": razao(medidos.sum() - estourados.sum(), medidos.sum()) * 100,
        "Etapas estouradas": int(estourados.sum()),
    }


def resumo_sup(g: pd.DataFrame, g_per: pd.DataFrame) -> dict:
    resolvidos = g_per[~g_per["Aberto"]]
    return {
        "Chamados no período": len(g_per),
        "Resolvidos no período": int(g["Data Resolucao"].between(ini_ts, fim_ts).sum()),
        "Em aberto agora": int(g["Aberto"].sum()),
        "1ª resposta no SLA": g_per["SLA 1ª resposta OK"].mean() * 100,
        "Média 1ª resposta (h)": g_per["Tempo_1a_Resposta_h"].mean(),
        "Resolução no SLA": resolvidos["SLA resolução OK"].mean() * 100,
        "Média resolução (h)": resolvidos["Tempo_Resolucao_h"].mean(),
        "SLAs estourados": int(estourou_suporte(g_per).sum()),
    }


with aba_analista:
    st.caption("Analista é o **responsável atual** do card no Jira. Mostra só os analistas do time de implantação "
               "(lista `ANALISTAS` no código). Os números \"no período\" usam o período da barra lateral.")
    analista = st.selectbox("Ver detalhe do analista", ANALISTAS, index=None, placeholder="Escolha um analista")
    if analista:
        mi, mi_per = impl[do_analista(impl, analista)], impl_per[do_analista(impl_per, analista)]
        ms, ms_per = sup[do_analista(sup, analista)], sup_per[do_analista(sup_per, analista)]
        if mi.empty and ms.empty:
            st.info(f"{analista} ainda não tem cards no Jira como responsável.")
        ri, rs = resumo_impl(mi, mi_per), resumo_sup(ms, ms_per)
        st.markdown("**Implantação**")
        kpis([
            dict(t="Em andamento", v=num(ri["Em andamento"])),
            dict(t=f"Parados há {DIAS_ALERTA}+ dias", v=num(ri[f"Parados {DIAS_ALERTA}+ dias"]), c=ALERTA),
            dict(t="Finalizados no período", v=num(ri["Finalizados no período"]), c=LIMA),
            dict(t="Dias médios de implantação", v="—" if pd.isna(ri["Dias médios de implantação"])
                 else f"{ri['Dias médios de implantação']:.0f}", c=AZUL_ESCURO),
            dict(t="SLA de etapa no prazo", v=pct(ri["SLA de etapa no prazo"] / 100), c=CIANO,
                 d=(f"{num(ri['Etapas estouradas'])} etapas estouradas", "")),
        ])
        st.markdown("**Suporte**")
        kpis([
            dict(t="Chamados no período", v=num(rs["Chamados no período"])),
            dict(t="Em aberto agora", v=num(rs["Em aberto agora"]), c=CIANO),
            dict(t="1ª resposta no SLA", v=pct(rs["1ª resposta no SLA"] / 100), c=LIMA,
                 d=(f"tempo médio {horas(rs['Média 1ª resposta (h)'])}", "")),
            dict(t="Resolução no SLA", v=pct(rs["Resolução no SLA"] / 100), c=LIMA,
                 d=(f"tempo médio {horas(rs['Média resolução (h)'])}", "")),
            dict(t="Chamados com SLA estourado", v=num(rs["SLAs estourados"]), c=ALERTA),
        ])

        e, d = st.columns(2, gap="medium")
        with e, card("an_impl_ab", "Implantações em andamento", "Da mais parada para a menos parada"):
            lista(mi[mi["Aberto"] & (mi["Quadro"] != "Outros")].sort_values("Dias no status atual", ascending=False),
                  ["Link", "Resumo", "Status", "Dias no status atual", "UF", "Relator"], "l_an_impl_ab")
        with d, card("an_sup_ab", "Suporte em aberto", "Do mais antigo para o mais recente"):
            ab_s = ms[ms["Aberto"]].assign(**{"Dias em aberto": lambda x: (hoje - x["Data Criacao"]).dt.days})
            lista(ab_s.sort_values("Dias em aberto", ascending=False),
                  ["Link", "Resumo", "Motivo", "Status", "Dias em aberto", "Relator"], "l_an_sup_ab")

        e, d = st.columns(2, gap="medium")
        with e, card("an_impl_est", "Implantações com SLA de etapa estourado", "Cards criados no período"):
            _, est_n, est_nomes = etapas_medidas(mi_per)
            lista(mi_per.assign(**{"Etapas estouradas": est_nomes})[est_n > 0].sort_values("Data Criacao", ascending=False),
                  ["Link", "Resumo", "Status", "Etapas estouradas", "UF"], "l_an_impl_est")
        with d, card("an_sup_est", "Suporte com SLA estourado", "Chamados criados no período"):
            se = ms_per[estourou_suporte(ms_per)].copy()
            se["SLA estourado"] = np.where(se["SLA_1a_Resposta_Violado"].eq("Sim"), "1ª resposta", "") + \
                np.where(se["SLA_Resolucao_Violado"].eq("Sim") | (se["Aberto"] & (se["Tempo_Restante_Resolucao_h"] < 0)),
                         np.where(se["SLA_1a_Resposta_Violado"].eq("Sim"), ", resolução", "resolução"), "")
            lista(se.sort_values("Data Criacao", ascending=False),
                  ["Link", "Resumo", "SLA estourado", "Motivo", "Status", "Data Criacao"], "l_an_sup_est")

    with card("an_tab_impl", "Implantação por analista", f"\"No período\": cards criados ou finalizados de "
              f"{ini:%d/%m/%Y} a {fim:%d/%m/%Y}; \"Em andamento\" e \"Parados\" são a situação atual"):
        tab_i = pd.DataFrame({a: resumo_impl(impl[do_analista(impl, a)], impl_per[do_analista(impl_per, a)])
                              for a in ANALISTAS}).T.rename_axis("Analista").reset_index()
        tab_i[tab_i.columns[1:]] = tab_i[tab_i.columns[1:]].apply(pd.to_numeric, errors="coerce")
        st.dataframe(tab_i.sort_values("Em andamento", ascending=False), hide_index=True, key="t_an_impl",
                     column_config={**{c: st.column_config.NumberColumn(format="%d") for c in tab_i.columns
                                       if c not in ("Analista", "SLA de etapa no prazo", "Dias médios de implantação")},
                                    "SLA de etapa no prazo": COL_PCT_BARRA,
                                    "Dias médios de implantação": st.column_config.NumberColumn(format="%.0f")})
    with card("an_tab_sup", "Suporte por analista", f"Chamados criados de {ini:%d/%m/%Y} a {fim:%d/%m/%Y}; "
              "\"Em aberto agora\" considera qualquer data"):
        tab_s = pd.DataFrame({a: resumo_sup(sup[do_analista(sup, a)], sup_per[do_analista(sup_per, a)])
                              for a in ANALISTAS}).T.rename_axis("Analista").reset_index()
        tab_s[tab_s.columns[1:]] = tab_s[tab_s.columns[1:]].apply(pd.to_numeric, errors="coerce")
        if not tab_s.empty:
            st.dataframe(tab_s.sort_values("Chamados no período", ascending=False), hide_index=True, key="t_an_sup",
                         column_config={**{c: st.column_config.NumberColumn(format="%d") for c in
                                           ("Chamados no período", "Resolvidos no período", "Em aberto agora",
                                            "SLAs estourados")},
                                        "1ª resposta no SLA": COL_PCT_BARRA, "Resolução no SLA": COL_PCT_BARRA,
                                        "Média 1ª resposta (h)": st.column_config.NumberColumn(format="%.1f"),
                                        "Média resolução (h)": st.column_config.NumberColumn(format="%.1f")})


# ---------- Cards ----------
with aba_cards:
    origem = st.radio("Base", ["Implantação", "Suporte"], horizontal=True)
    base_cards = impl_per if origem == "Implantação" else sup_per
    dica = "Número do card: 48 mostra os que começam com 48, 488 afunila, e assim por diante"
    busca = (st_keyup("Buscar card", placeholder=dica, debounce=300, key="busca_card") if st_keyup
             else st.text_input("Buscar card", placeholder=dica, key="busca_card"))
    prefixos = tuple(re.findall(r"\d+", busca or ""))
    if prefixos:
        todos = impl if origem == "Implantação" else sup
        numero = todos["Chamado"].astype(str).str.extract(r"(\d+)$", expand=False).fillna("")
        base_cards = todos[numero.str.startswith(prefixos)]
    with card("q_cards", f"Cards de {origem.lower()}", f"{num(len(base_cards))} cards"):
        colunas = (["Link", "Resumo", "Status", "Quadro", "Request Type", "Relator", "Responsável", "UF", "Marca",
                    "Data Criacao", "Dias no status atual"] if origem == "Implantação" else
                   ["Link", "Resumo", "Status", "Motivo", "Relator", "Responsável", "UF", "Marca", "Data Criacao",
                    "Data Resolucao", "SLA_1a_Resposta_Violado", "SLA_Resolucao_Violado"])
        lista(base_cards.sort_values("Data Criacao", ascending=False), colunas, "l_cards", altura=560)
        csv = base_cards.drop(columns=["Link"]).to_csv(index=False, sep=";", decimal=",", date_format="%d/%m/%Y")
        st.download_button("Baixar CSV", csv.encode("utf-8-sig"), f"cards_{origem.lower()}.csv", "text/csv")
