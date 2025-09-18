import unicodedata
import base64
import pandas as pd
import streamlit as st
from database import Database

st.set_page_config(
    page_title="Sistema de Consulta de Empresas",
    page_icon="https://cdn-icons-png.flaticon.com/128/3915/3915151.png",
    layout="wide"
)

st.markdown("""
<style>
.badge {display:inline-flex; align-items:center; gap:.5rem; padding:.25rem .6rem; margin:.15rem;
        border-radius:999px; background:black; border:1px solid #c7d2fe; font-size:12px;}
.badge .tag {opacity:.8}
.badge-applied {background:black; border-color:#a7f3d0}
.badge small {opacity:.7}
.btn-x {padding:.1rem .45rem; border:1px solid #e5e7eb; border-radius:8px; background:#fff; cursor:pointer;}
.section-sub {font-size:.85rem; color:#6b7280; margin:.25rem 0 .25rem}
</style>
""", unsafe_allow_html=True)

try:
    import plotly.express as px
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    st.warning("Plotly não instalado. Instale com: pip install plotly")

def _normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower()

def limpar_cnae(codigo) -> str:
    return "".join(ch for ch in str(codigo) if ch.isdigit())

def formatar_cnpj(cnpj):
    if not cnpj:
        return cnpj
    s = "".join(filter(str.isdigit, str(cnpj)))
    if len(s) != 14:
        return cnpj
    return f"{s[:2]}.{s[2:5]}.{s[5:8]}/{s[8:12]}-{s[12:]}"

def formatar_moeda(valor):
    if pd.isna(valor):
        return "N/A"
    try:
        return f"R$ {float(valor):,.2f}"
    except Exception:
        return f"R$ {valor}"

_MAPEAMENTO_PORTE_FWD = {"01": "Microempresa", "03": "Empresa de Pequeno Porte", "05": "Demais"}
_MAPEAMENTO_PORTE_REV = {"Microempresa": "01", "Empresa de Pequeno Porte": "03", "Demais": "05"}

def traduzir_porte(porte):
    if pd.isna(porte):
        return "N/A"
    p = str(porte).zfill(2)
    return _MAPEAMENTO_PORTE_FWD.get(p, p)

db = Database()

@st.cache_data(show_spinner=False)
def get_ufs():
    try:
        result, _ = db.execute_query("SELECT DISTINCT uf FROM estabelecimento WHERE uf IS NOT NULL ORDER BY uf")
        return [r[0] for r in result] if result else []
    except Exception:
        return []

@st.cache_data(show_spinner=False)
def get_portes():
    try:
        result, _ = db.execute_query("SELECT DISTINCT porte_empresa FROM empresa WHERE porte_empresa IS NOT NULL ORDER BY porte_empresa")
        vistos = []
        for (cod,) in result or []:
            lbl = traduzir_porte(cod)
            if lbl not in vistos:
                vistos.append(lbl)
        return vistos
    except Exception:
        return []

@st.cache_data(show_spinner=False)
def get_capital_range():
    try:
        result, _ = db.execute_query("""
            SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY capital_social) AS p95
            FROM empresa
            WHERE capital_social IS NOT NULL AND capital_social > 0
        """)
        if result and result[0] and result[0][0] and result[0][0] > 0:
            return 0, float(result[0][0])
        return 0, 500000
    except Exception:
        return 0, 250000

@st.cache_data(ttl=600, show_spinner=False)
def get_cnae_infos(codigos):
    """Mapa codigo_limpo -> descrição (para chips bonitos)."""
    if not codigos:
        return {}
    placeholders = ", ".join(["%s"] * len(codigos))
    sql = f"""
        SELECT regexp_replace(c.codigo::text, '\\D', '', 'g') AS codigo_limpo, c.descricao
        FROM cnae c
        WHERE regexp_replace(c.codigo::text, '\\D', '', 'g') IN ({placeholders})
    """
    res, _ = db.execute_query(sql, codigos)
    return {row[0]: row[1] for row in (res or [])}

SUGGEST_LIMIT = 20

def _sql_sugerir_cnae_unaccent():
    return """
        SELECT
            regexp_replace(c.codigo::text, '\\D', '', 'g') AS codigo_limpo,
            c.descricao,
            COUNT(est.cnpj) AS empresas
        FROM cnae c
        LEFT JOIN estabelecimento est
          ON est.cnae_fiscal_principal::text = regexp_replace(c.codigo::text, '\\D', '', 'g')
        WHERE unaccent(lower(c.descricao)) LIKE unaccent(lower(%s))
        GROUP BY 1,2
        ORDER BY empresas DESC, codigo_limpo
        LIMIT %s
    """

def _sql_sugerir_cnae_fallback():
    return """
        SELECT
            regexp_replace(c.codigo::text, '\\D', '', 'g') AS codigo_limpo,
            c.descricao,
            COUNT(est.cnpj) AS empresas
        FROM cnae c
        LEFT JOIN estabelecimento est
          ON est.cnae_fiscal_principal::text = regexp_replace(c.codigo::text, '\\D', '', 'g')
        WHERE lower(c.descricao) ILIKE lower(%s)
        GROUP BY 1,2
        ORDER BY empresas DESC, codigo_limpo
        LIMIT %s
    """

@st.cache_data(show_spinner=False)
def has_unaccent() -> bool:
    try:
        res, _ = db.execute_query("SELECT 1 FROM pg_extension WHERE extname = 'unaccent' LIMIT 1")
        return bool(res)
    except Exception:
        return False

@st.cache_data(ttl=600, show_spinner=False)
def sugerir_cnae_cache(termo: str, limit: int = SUGGEST_LIMIT):
    if not termo or len(termo.strip()) < 2:
        return []
    termo = termo.strip()
    try:
        if has_unaccent():
            res, _ = db.execute_query(_sql_sugerir_cnae_unaccent(), (f"%{termo}%", limit))
            return res or []
    except Exception:
        pass
 
    res2, _ = db.execute_query(_sql_sugerir_cnae_fallback(), (f"%{termo}%", limit))
    return res2 or []

def sugerir_razao_social(termo: str, limit: int = 12):
    if not termo or len(termo.strip()) < 2:
        return []
    sql = """
        SELECT emp.razao_social, COUNT(*) AS qtd
        FROM empresa emp
        JOIN estabelecimento est USING (cnpj_basico)
        WHERE emp.razao_social ILIKE %s
        GROUP BY emp.razao_social
        ORDER BY qtd DESC, emp.razao_social
        LIMIT %s
    """
    res, _ = db.execute_query(sql, (f"%{termo.strip()}%", limit))
    return res or []

def sugerir_nome_fantasia(termo: str, limit: int = 12):
    if not termo or len(termo.strip()) < 2:
        return []
    sql = """
        SELECT est.nome_fantasia, COUNT(*) AS qtd
        FROM estabelecimento est
        WHERE est.nome_fantasia ILIKE %s
        GROUP BY est.nome_fantasia
        ORDER BY qtd DESC, est.nome_fantasia
        LIMIT %s
    """
    res, _ = db.execute_query(sql, (f"%{termo.strip()}%", limit))
    return res or []

if "filtros" not in st.session_state:
    st.session_state.filtros = {
        "cnpj": "",
        "razao_social": "",
        "nome_fantasia": "",
        "uf": "Todos",
        "porte": "Todos",
        "situacao": "Todos",
        "cnae": [],          
        "capital_min": 0,
        "capital_max": 500000,
        "sem_limite_capital": False,
        "limit": 100
    }
if "cnaes_selecionados" not in st.session_state:
    st.session_state.cnaes_selecionados = list(st.session_state.filtros["cnae"]) 
if "page" not in st.session_state:
    st.session_state.page = 1
if "consulta_pronta" not in st.session_state:
    st.session_state.consulta_pronta = False

if "cnae_resultados" not in st.session_state:
    st.session_state.cnae_resultados = []   

if "cnae_multisel_version" not in st.session_state:
    st.session_state.cnae_multisel_version = 0

def build_queries(filtros: dict, limit: int = None, offset: int = None):
    base_where = "WHERE 1=1"
    params = []

    if filtros["cnpj"]:
        base_where += " AND est.cnpj ILIKE %s"
        params.append(f"%{filtros['cnpj']}%")
    if filtros["razao_social"]:
        base_where += " AND emp.razao_social ILIKE %s"
        params.append(f"%{filtros['razao_social']}%")
    if filtros["nome_fantasia"]:
        base_where += " AND est.nome_fantasia ILIKE %s"
        params.append(f"%{filtros['nome_fantasia']}%")
    if filtros["cnae"] and "Todos" not in filtros["cnae"]:
        placeholders = ", ".join(["%s"] * len(filtros["cnae"]))
        base_where += f" AND est.cnae_fiscal_principal::text IN ({placeholders})"
        params.extend(filtros["cnae"])
    if filtros["uf"] != "Todos":
        base_where += " AND est.uf = %s"
        params.append(filtros["uf"])
    if filtros["porte"] != "Todos":
        porte_codigo = _MAPEAMENTO_PORTE_REV.get(filtros["porte"], filtros["porte"])
        base_where += " AND COALESCE(LPAD(emp.porte_empresa::text, 2, '0'), '') = %s"
        params.append(str(porte_codigo).zfill(2))
    if filtros["situacao"] != "Todos":
        situacao_map = {"Ativa": 2, "Baixada": 3, "Suspensa": 4, "Inapta": 8, "Nula": 5}
        base_where += " AND est.situacao_cadastral = %s"
        params.append(situacao_map[filtros["situacao"]])
    if not filtros.get("sem_limite_capital", False):
        base_where += " AND emp.capital_social BETWEEN %s AND %s"
        params.append(filtros["capital_min"])
        params.append(filtros["capital_max"])
    else:
        base_where += " AND emp.capital_social > 0"

    sql_count = f"""
        SELECT COUNT(*)
        FROM estabelecimento est
        LEFT JOIN empresa emp ON est.cnpj_basico = emp.cnpj_basico
        {base_where}
    """
    params_count = list(params)

    sql_select = f"""
        SELECT
            emp.razao_social,
            est.nome_fantasia,
            est.cnpj,
            est.uf,
            est.data_inicio_atividade,
            est.situacao_cadastral,
            emp.porte_empresa,
            emp.capital_social,
            est.municipio,
            est.cnae_fiscal_principal,
            cna.descricao AS cnae_descricao
        FROM estabelecimento est
        LEFT JOIN empresa emp ON est.cnpj_basico = emp.cnpj_basico
        LEFT JOIN cnae cna
          ON regexp_replace(cna.codigo::text, '\\D', '', 'g') = est.cnae_fiscal_principal::text
        {base_where}
        ORDER BY emp.razao_social NULLS LAST
        LIMIT %s OFFSET %s
    """
    params_select = list(params) + [limit if limit is not None else 100, offset if offset is not None else 0]
    return (sql_count, params_count), (sql_select, params_select)

st.title("Sistema de Consulta de Empresas")
st.markdown("---")

with st.sidebar.form("filtros_form", clear_on_submit=False):
    st.subheader("Filtros de Pesquisa")

    cnpj_input = st.text_input("CNPJ (completo ou parcial)", value=st.session_state.filtros.get("cnpj", ""))
    razao_input = st.text_input("Razão Social (completo ou parcial)", value=st.session_state.filtros.get("razao_social", ""))
    fantasia_input = st.text_input("Nome Fantasia (completo ou parcial)", value=st.session_state.filtros.get("nome_fantasia", ""))

    st.markdown("**CNAE**")
    c1, c2 = st.columns([0.65, 0.35])
    with c1:
        cnae_busca = st.text_input("Buscar por descrição (ex.: sorveteria)", value="", key="cnae_busca_texto")
    with c2:
        buscar = st.form_submit_button("🔎 Buscar CNAE")

    if buscar and len(cnae_busca.strip()) >= 3:
        st.session_state.cnae_resultados = sugerir_cnae_cache(cnae_busca.strip())

    options = [f"{cod} — {desc} ({qtd})" for cod, desc, qtd in st.session_state.cnae_resultados]

    ms_key = f"cnae_multisel_{st.session_state.cnae_multisel_version}"
    if options:
        st.multiselect("Resultados da busca", options=options, key=ms_key)
    selected_items = st.session_state.get(ms_key, [])

    col_add1, col_add2 = st.columns([0.5, 0.5])
    add_selecionados = col_add1.form_submit_button("➕ Adicionar selecionados")
    limpar_selec = col_add2.form_submit_button("🧹 Limpar seleção")

    if add_selecionados and selected_items:
        for item in selected_items:
            cod = item.split(" — ", 1)[0]
            if cod not in st.session_state.cnaes_selecionados:
                st.session_state.cnaes_selecionados.append(cod)
     
        st.session_state.cnae_multisel_version += 1
        st.success("CNAEs adicionados.")
        st.rerun()

    if limpar_selec:
        st.session_state.cnae_multisel_version += 1
        st.rerun()

    ufs = ["Todos"] + get_ufs()
    uf_select = st.selectbox("UF", options=ufs, index=ufs.index(st.session_state.filtros.get("uf", "Todos")))

    portes = ["Todos"] + get_portes()
    porte_select = st.selectbox("Porte da Empresa", options=portes, index=portes.index(st.session_state.filtros.get("porte", "Todos")))

    situacao_options = ["Todos", "Ativa", "Baixada", "Suspensa", "Inapta", "Nula"]
    situacao_select = st.selectbox("Situação Cadastral", options=situacao_options, index=situacao_options.index(st.session_state.filtros.get("situacao", "Todos")))

    cap_min, cap_max = get_capital_range()
    sem_limite = st.checkbox("Sem limite de capital", value=st.session_state.filtros.get("sem_limite_capital", False))
    if sem_limite:
        st.slider("Capital Social (sem limite aplicado)", min_value=int(cap_min), max_value=int(cap_max),
                  value=(0, int(cap_max)), disabled=True)
        preview_capital = (0, float("inf"))
    else:
        preview_capital = st.slider("Selecione a faixa de capital social",
                                    min_value=int(cap_min), max_value=int(cap_max),
                                    value=(int(st.session_state.filtros.get("capital_min", 0)),
                                           int(st.session_state.filtros.get("capital_max", cap_max))))
    limit_slider = st.slider("Resultados por página", 10, 500, st.session_state.filtros.get("limit", 100))

    cta1, cta2, cta3 = st.columns(3)
    atualizar_contagem = cta1.form_submit_button("📊 Atualizar contagem")
    executar_consulta = cta2.form_submit_button("🔍 Executar consulta")
    limpar_tudo = cta3.form_submit_button("🧹 Limpar filtros")

with st.sidebar:
    st.markdown("---")
    cnaes_pend = list(st.session_state.cnaes_selecionados)
    cnaes_aplic = list(st.session_state.filtros.get("cnae", []))
    infos_pend = get_cnae_infos(cnaes_pend)
    infos_aplic = get_cnae_infos(cnaes_aplic)

    st.caption(f"**CNAEs selecionados (pendentes)** — {len(cnaes_pend)}")
    if cnaes_pend:
        for cod in cnaes_pend:
            cols = st.columns([0.82, 0.18])
            with cols[0]:
                desc = (infos_pend.get(cod) or "")[:60]
                st.markdown(f"<span class='badge'><span class='tag'>{cod}</span><small>{desc}</small></span>", unsafe_allow_html=True)
            with cols[1]:
                if st.button("✖", key=f"rm_pend_{cod}"):
                    st.session_state.cnaes_selecionados = [c for c in st.session_state.cnaes_selecionados if c != cod]
                    st.rerun()
        if st.button("Limpar CNAEs pendentes", key="clear_pend"):
            st.session_state.cnaes_selecionados = []
            st.rerun()
    else:
        st.caption("_Nenhum CNAE pendente_")

    st.caption(f"**CNAEs APLICADOS** — {len(cnaes_aplic)}")
    if cnaes_aplic:
        for cod in cnaes_aplic:
            desc = (infos_aplic.get(cod) or "")[:60]
            st.markdown(f"<span class='badge badge-applied'><span class='tag'>{cod}</span><small>{desc}</small></span>", unsafe_allow_html=True)
    else:
        st.caption("_Nenhum CNAE aplicado_")

f_preview = {
    "cnpj": cnpj_input.strip(),
    "razao_social": razao_input.strip(),
    "nome_fantasia": fantasia_input.strip(),
    "uf": uf_select,
    "porte": porte_select,
    "situacao": situacao_select,
    "cnae": list(st.session_state.cnaes_selecionados),
    "capital_min": preview_capital[0],
    "capital_max": preview_capital[1],
    "sem_limite_capital": sem_limite,
    "limit": limit_slider
}

total_empresas = None
if atualizar_contagem or executar_consulta:
    (sql_count, params_count), _ = build_queries(f_preview)
    try:
        res, _ = db.execute_query(sql_count, params_count)
        total_empresas = int(res[0][0]) if res else 0
    except Exception as e:
        st.sidebar.error(f"Erro na contagem: {e}")
        total_empresas = 0

if total_empresas is not None:
    st.sidebar.success(f"**{total_empresas} empresas** com os filtros acima")

if limpar_tudo:
    st.session_state.filtros = {
        "cnpj": "", "razao_social": "", "nome_fantasia": "",
        "uf": "Todos", "porte": "Todos", "situacao": "Todos",
        "cnae": [], "capital_min": 0, "capital_max": 500000,
        "sem_limite_capital": False, "limit": 100
    }
    st.session_state.cnaes_selecionados = []
    st.session_state.cnae_resultados = []
    st.session_state.cnae_multisel_version = 0
    st.session_state.page = 1
    st.session_state.consulta_pronta = False
    st.rerun()

if executar_consulta:
    st.session_state.filtros = dict(f_preview)
    st.session_state.page = 1
    st.session_state.consulta_pronta = True
    st.rerun()

def _chips_aplicados(f):
    chips = []
    if f["cnpj"]: chips.append(f"<span class='badge badge-applied'><small>CNPJ</small> {f['cnpj']}</span>")
    if f["razao_social"]: chips.append(f"<span class='badge badge-applied'><small>Razão</small> {f['razao_social']}</span>")
    if f["nome_fantasia"]: chips.append(f"<span class='badge badge-applied'><small>Fantasia</small> {f['nome_fantasia']}</span>")
    if f["uf"] != "Todos": chips.append(f"<span class='badge badge-applied'><small>UF</small> {f['uf']}</span>")
    if f["porte"] != "Todos": chips.append(f"<span class='badge badge-applied'><small>Porte</small> {f['porte']}</span>")
    if f["situacao"] != "Todos": chips.append(f"<span class='badge badge-applied'><small>Situação</small> {f['situacao']}</span>")
    if f["cnae"]:
        infos = get_cnae_infos(f["cnae"])
        for cod in f["cnae"][:6]:
            desc = (infos.get(cod) or "")[:42]
            chips.append(f"<span class='badge badge-applied'><span class='tag'>{cod}</span><small>{desc}</small></span>")
        if len(f["cnae"]) > 6:
            chips.append(f"<span class='badge badge-applied'><small>+{len(f['cnae'])-6} CNAE(s)</small></span>")
    return " ".join(chips) or "<span class='section-sub'>Nenhum filtro aplicado</span>"

st.markdown("#### Filtros aplicados")
st.markdown(_chips_aplicados(st.session_state.filtros), unsafe_allow_html=True)
st.markdown("---")

if st.session_state.consulta_pronta:
    f = st.session_state.filtros
    limit = f["limit"]; page = st.session_state.page; offset = (page - 1) * limit
    (_, _), (sql_select, params_select) = build_queries(f, limit=limit, offset=offset)

    with st.spinner("Executando consulta..."):
        try:
            resultados, colunas = db.execute_query(sql_select, params_select)
            if resultados:
                df = pd.DataFrame(resultados, columns=colunas)

                c1, c2, c3, c4 = st.columns(4)
                with c1: st.metric("Total nesta página", len(df))
                with c2: st.metric("UF's Diferentes", df["uf"].nunique() if "uf" in df.columns else 0)
                with c3:
                    capital_total = df["capital_social"].sum() if "capital_social" in df.columns else 0
                    st.metric("Capital Social (pág.)", f"R$ {float(capital_total):,.2f}")
                with c4:
                    ativas = int((df["situacao_cadastral"] == 2).sum()) if "situacao_cadastral" in df.columns else 0
                    st.metric("Empresas Ativas (pág.)", ativas)

                if "cnpj" in df.columns:
                    df["cnpj_formatado"] = df["cnpj"].apply(formatar_cnpj)
                if "capital_social" in df.columns:
                    df["capital_social_formatado"] = df["capital_social"].apply(formatar_moeda)
                if "porte_empresa" in df.columns:
                    df["porte_traduzido"] = df["porte_empresa"].apply(traduzir_porte)
                if "data_inicio_atividade" in df.columns:
                    df["_data_inicio"] = pd.to_datetime(df["data_inicio_atividade"], errors="coerce")
                if {"cnae_fiscal_principal", "cnae_descricao"}.issubset(df.columns):
                    df["cnae_exib"] = df["cnae_fiscal_principal"].astype(str) + " – " + df["cnae_descricao"].fillna("")

                rename_map = {
                    "cnpj_formatado": "CNPJ",
                    "razao_social": "Razão Social",
                    "nome_fantasia": "Nome Fantasia",
                    "uf": "UF",
                    "porte_traduzido": "Porte",
                    "capital_social_formatado": "Capital Social",
                    "cnae_exib": "CNAE Principal",
                    "_data_inicio": "Data Início",
                    "situacao_cadastral": "Situação",
                }
                colunas_exibicao = [c for c in [
                    "cnpj_formatado","razao_social","nome_fantasia","uf",
                    "porte_traduzido","capital_social_formatado","cnae_exib",
                    "_data_inicio","situacao_cadastral"
                ] if c in df.columns]
                df_exibicao = df[colunas_exibicao].rename(columns=rename_map)

                st.dataframe(df_exibicao, use_container_width=True, hide_index=True, height=440)

                csv = df.to_csv(index=False, sep=";", decimal=",")
                href = f"""<a href="data:file/csv;base64,{base64.b64encode(csv.encode()).decode()}" download="empresas_pagina_{page}.csv">
                            <button style="background-color:#4CAF50; color:white; padding:10px 20px; border:none; border-radius:4px; cursor:pointer;">
                                📥 Download CSV (página)
                            </button>
                        </a>"""
                st.markdown(href, unsafe_allow_html=True)

                (sql_count, params_count), _ = build_queries(f)
                res_total, _ = db.execute_query(sql_count, params_count)
                total = int(res_total[0][0]) if res_total else None

                cprev, cpage, cnext = st.columns([1, 2, 1])
                with cprev:
                    if st.button("⬅️ Página anterior", disabled=(page <= 1)):
                        st.session_state.page = max(1, page - 1); st.rerun()
                with cpage:
                    if total is not None and limit > 0:
                        total_pages = max(1, (total + limit - 1) // limit)
                        st.write(f"Página **{page}** de **{total_pages}** — Total: **{total}**")
                    else:
                        st.write(f"Página **{page}**")
                with cnext:
                    disable_next = False
                    if total is not None and limit > 0:
                        total_pages = max(1, (total + limit - 1) // limit)
                        disable_next = page >= total_pages
                    else:
                        disable_next = len(df) < limit
                    if st.button("Próxima página ➡️", disabled=disable_next):
                        st.session_state.page = page + 1; st.rerun()

                if PLOTLY_AVAILABLE:
                    st.subheader("📊 Visualizações")
                    tab1, tab2, tab3 = st.tabs(["UF", "Situação", "CNAE"])
                    with tab1:
                        uf_count = df["uf"].value_counts()
                        fig_uf = px.bar(x=uf_count.index, y=uf_count.values, title="Distribuição por UF",
                                        labels={"x": "UF", "y": "Quantidade"})
                        fig_uf.update_layout(xaxis_tickangle=-45)
                        st.plotly_chart(fig_uf, use_container_width=True)
                    with tab2:
                        situacao_map_inv = {2: "Ativa", 3: "Baixada", 4: "Suspensa", 8: "Inapta", 5: "Nula"}
                        s = df["situacao_cadastral"].map(situacao_map_inv).value_counts()
                        if len(s) > 0:
                            st.plotly_chart(px.pie(values=s.values, names=s.index, title="Situação Cadastral"), use_container_width=True)
                    with tab3:
                        if {"cnae_fiscal_principal","cnae_descricao"}.issubset(df.columns):
                            top = (df.assign(cnae_exib=df["cnae_fiscal_principal"].astype(str) + " – " + df["cnae_descricao"].fillna(""))
                                     .value_counts("cnae_exib").head(10))
                            fig_c = px.bar(x=top.index, y=top.values, title="Top 10 CNAEs (página)",
                                           labels={"x": "CNAE", "y": "Quantidade"})
                            fig_c.update_layout(xaxis_tickangle=-45)
                            st.plotly_chart(fig_c, use_container_width=True)
            else:
                st.warning("Nenhum resultado para os filtros aplicados.")
        except Exception as e:
            st.error(f"Erro na consulta: {e}")
else:
    st.info("Defina os filtros e use **📊 Atualizar contagem** ou **🔍 Executar consulta**.")

st.markdown("---")
st.subheader("Detalhes da Empresa")

cnpj_detalhes = st.text_input("Digite o CNPJ completo para ver detalhes (somente números):")
buscar_detalhes = st.button("Buscar Detalhes")

if buscar_detalhes and cnpj_detalhes:
    cnpj_limpo = "".join(filter(str.isdigit, str(cnpj_detalhes)))
    if len(cnpj_limpo) != 14:
        st.error("CNPJ deve conter exatamente 14 dígitos")
    else:
        with st.spinner("Buscando detalhes..."):
            try:
                query_detalhes = """
                    SELECT
                        emp.razao_social,
                        est.nome_fantasia,
                        est.cnpj,
                        est.uf,
                        est.municipio,
                        est.data_inicio_atividade,
                        est.situacao_cadastral,
                        emp.porte_empresa,
                        emp.capital_social,
                        est.logradouro,
                        est.numero,
                        est.bairro,
                        est.cep,
                        est.complemento,
                        est.ddd_1,
                        est.telefone_1,
                        est.correio_eletronico,
                        est.tipo_logradouro,
                        est.cnae_fiscal_principal,
                        cnae.descricao AS descricao_cnae
                    FROM estabelecimento est
                    LEFT JOIN empresa emp ON est.cnpj_basico = emp.cnpj_basico
                    LEFT JOIN cnae cnae
                        ON regexp_replace(cnae.codigo::text, '\\D', '', 'g') = est.cnae_fiscal_principal::text
                    WHERE est.cnpj = %s
                """
                resultado_detalhes, colunas_detalhes = db.execute_query(query_detalhes, (cnpj_limpo,))
                if resultado_detalhes:
                    df_d = pd.DataFrame(resultado_detalhes, columns=colunas_detalhes)
                    c1, c2 = st.columns(2)
                    with c1:
                        st.write("**Informações Básicas**")
                        st.write(f"**Razão Social:** {df_d.get('razao_social', pd.Series(['N/A'])).iloc[0]}")
                        st.write(f"**Nome Fantasia:** {df_d.get('nome_fantasia', pd.Series(['N/A'])).iloc[0]}")
                        st.write(f"**CNPJ:** {formatar_cnpj(df_d.get('cnpj', pd.Series([''])).iloc[0])}")
                        porte_raw = df_d.get('porte_empresa', pd.Series([None])).iloc[0]
                        st.write(f"**Porte:** {traduzir_porte(porte_raw)}")
                        capital_social = df_d.get('capital_social', pd.Series([None])).iloc[0]
                        st.write(f"**Capital Social:** {formatar_moeda(capital_social)}")
                        data_ini = pd.to_datetime(df_d.get('data_inicio_atividade', pd.Series([None])).iloc[0], errors="coerce")
                        st.write(f"**Data Início Atividade:** {data_ini.date() if pd.notna(data_ini) else 'N/A'}")
                        sit = df_d.get('situacao_cadastral', pd.Series([None])).iloc[0]
                        situacao_map_inv = {2: "Ativa", 3: "Baixada", 4: "Suspensa", 8: "Inapta", 5: "Nula"}
                        st.write(f"**Situação Cadastral:** {situacao_map_inv.get(sit, sit)}")
                        cnae_princ = df_d.get('cnae_fiscal_principal', pd.Series(['N/A'])).iloc[0]
                        st.write(f"**CNAE Principal:** {cnae_princ}")
                        st.write(f"**Descrição CNAE:** {df_d.get('descricao_cnae', pd.Series(['N/A'])).iloc[0]}")
                    with c2:
                        st.write("**Informações de Endereço**")
                        st.write(f"**UF:** {df_d.get('uf', pd.Series(['N/A'])).iloc[0]}")
                        st.write(f"**Município (cód.):** {df_d.get('municipio', pd.Series(['N/A'])).iloc[0]}")
                        tipo_log = df_d.get('tipo_logradouro', pd.Series([''])).iloc[0] or ''
                        lograd = df_d.get('logradouro', pd.Series([''])).iloc[0] or ''
                        numero = df_d.get('numero', pd.Series([''])).iloc[0] or ''
                        end = f"{tipo_log} {lograd}, {numero}".strip().strip(',')
                        st.write(f"**Endereço:** {end if end and end != ',' else 'N/A'}")
                        bairro = df_d.get('bairro', pd.Series(['N/A'])).iloc[0]
                        st.write(f"**Bairro:** {bairro}")
                        cep_raw = str(df_d.get('cep', pd.Series([''])).iloc[0] or '')
                        cep_raw = "".join(ch for ch in cep_raw if ch.isdigit())
                        cep_fmt = f"{cep_raw[:5]}-{cep_raw[5:]}" if len(cep_raw) == 8 else (cep_raw or "N/A")
                        st.write(f"**CEP:** {cep_fmt}")
                        compl = df_d.get('complemento', pd.Series(['N/A'])).iloc[0]
                        st.write(f"**Complemento:** {compl}")
                        st.write("**Informações de Contato**")
                        ddd = str(df_d.get('ddd_1', pd.Series([''])).iloc[0] or '')
                        tel = str(df_d.get('telefone_1', pd.Series(['N/A'])).iloc[0] or 'N/A')
                        st.write(f"**Telefone:** ({ddd}) {tel}")
                        email = df_d.get('correio_eletronico', pd.Series(['N/A'])).iloc[0]
                        st.write(f"**Email:** {email}")
                else:
                    st.warning("Nenhuma empresa encontrada com este CNPJ.")
            except Exception as e:
                st.error(f"Erro ao buscar detalhes: {e}")

st.markdown("---")
st.caption("Sistema de consulta empresarial - Desenvolvido com Streamlit e PostgreSQL")
