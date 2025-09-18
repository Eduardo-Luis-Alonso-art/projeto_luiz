import streamlit as st
import pandas as pd
from database import Database
import base64

# Adicionar esses imports no topo do arquivo
try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    st.warning("Plotly não instalado. Instale com: pip install plotly")

# Função para formatar CNPJ
def formatar_cnpj(cnpj):
    """Formata o CNPJ para o padrão XX.XXX.XXX/XXXX-XX"""
    if not cnpj or len(cnpj) != 14:
        return cnpj
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"

# Função para formatar valores monetários
def formatar_moeda(valor):
    """Formata valores monetários para o padrão R$"""
    if pd.isna(valor):
        return "N/A"
    try:
        return f"R$ {float(valor):,.2f}"
    except:
        return f"R$ {valor}"

# Função para traduzir porte da empresa
def traduzir_porte(porte):
    """Traduz o código do porte para descrição amigável"""
    if pd.isna(porte):
        return "N/A"
    
    porte = str(porte).strip()
    mapeamento_porte = {
        '1': 'Pequena Empresa', 
        '3': 'Média Empresa',
        '5': 'Grande Empresa'
    }
    return mapeamento_porte.get(porte, porte)

# Configuração da página
st.set_page_config(
    page_title="Sistema de Consulta de Empresas",
    page_icon="https://cdn-icons-png.flaticon.com/128/3915/3915151.png",
    layout="wide"
)

# Título da aplicação
st.title("Sistema de Consulta de Empresas")
st.markdown("---")

# Inicializa a conexão com o banco
db = Database()

# Inicializar session state para filtros
if 'filtros' not in st.session_state:
    st.session_state.filtros = {
        'cnpj': '', 
        'razao_social': '', 
        'nome_fantasia': '',
        'uf': 'Todos', 
        'porte': 'Todos', 
        'situacao': 'Todos',
        'cnae': [],  # Alterado para lista vazia para múltiplos CNAEs
        'capital_min': 0,
        'capital_max': 500000,
        'sem_limite_capital': False,
        'limit': 100
    }

# Função para obter UFs
def get_ufs():
    try:
        result, _ = db.execute_query("SELECT DISTINCT uf FROM estabelecimento WHERE uf IS NOT NULL ORDER BY uf")
        return [uf[0] for uf in result] if result else []
    except:
        return []

# Função para obter portes
def get_portes():
    try:
        result, _ = db.execute_query("SELECT DISTINCT porte_empresa FROM empresa WHERE porte_empresa IS NOT NULL ORDER BY porte_empresa")
        portes = [porte[0] for porte in result] if result else []
        
        # Traduzir os portes para exibição no filtro
        portes_traduzidos = []
        for porte in portes:
            portes_traduzidos.append(traduzir_porte(porte))
        
        return portes_traduzidos
    except:
        return []

# Função para obter CNAEs com descrição (para sugestões)
def get_cnaes_options():
    try:
        result, _ = db.execute_query("""
            SELECT codigo, descricao 
            FROM cnae 
            WHERE codigo IS NOT NULL 
            ORDER BY descricao 
            LIMIT 200  -- Aumentado para mostrar mais opções
        """)
        if result:
            # Retorna lista de tuplas (código, descrição formatada)
            return [("Todos", "Todos")] + [(cnae[0], f"{cnae[0]} - {cnae[1]}") for cnae in result]
        return [("Todos", "Todos")]
    except Exception as e:
        print(f"Erro ao obter CNAEs: {e}")
        return [("Todos", "Todos")]

# Função para obter range de capital social
def get_capital_range():
    try:
        # Obtém o percentil 95 para evitar outliers
        result, _ = db.execute_query("""
            SELECT 
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY capital_social) as p95
            FROM empresa 
            WHERE capital_social IS NOT NULL AND capital_social > 0
        """)
        
        if result and result[0]:
            p95 = result[0][0]
            if p95 and p95 > 0:
                # Usa o percentil 95 como máximo
                return 0, float(p95)
        
        # Fallback: valor máximo razoável
        return 0, 500000
        
    except Exception as e:
        print(f"Erro ao obter range de capital: {e}")
        # Valores padrão conservadores
        return 0, 250000

# Função para contar empresas com os filtros atuais
def contar_empresas_filtradas():
    """Conta o total de empresas que correspondem aos filtros atuais"""
    query_count = """
    SELECT COUNT(*)
    FROM estabelecimento est
    LEFT JOIN empresa emp ON est.cnpj_basico = emp.cnpj_basico
    WHERE 1=1
    """
    
    params = []
    
    # Aplicar filtros (mesma lógica da query principal)
    if st.session_state.filtros['cnpj']:
        query_count += " AND est.cnpj ILIKE %s"
        params.append(f"%{st.session_state.filtros['cnpj']}%")

    if st.session_state.filtros['razao_social']:
        query_count += " AND emp.razao_social ILIKE %s"
        params.append(f"%{st.session_state.filtros['razao_social']}%")

    if st.session_state.filtros['nome_fantasia']:
        query_count += " AND est.nome_fantasia ILIKE %s"
        params.append(f"%{st.session_state.filtros['nome_fantasia']}%")

    # Filtro CNAE usando IN para múltiplos valores
    if st.session_state.filtros['cnae'] and "Todos" not in st.session_state.filtros['cnae']:
        placeholders = ', '.join(['%s'] * len(st.session_state.filtros['cnae']))
        query_count += f" AND est.cnae_fiscal_principal IN ({placeholders})"
        params.extend(st.session_state.filtros['cnae'])

    if st.session_state.filtros['uf'] != "Todos":
        query_count += " AND est.uf = %s"
        params.append(st.session_state.filtros['uf'])

    if st.session_state.filtros['porte'] != "Todos":
        mapeamento_reverso = {
            'Micro Empresa': '1',
            'Pequena Empresa': '2',
            'Média Empresa': '3',
            'Demais': '4',
            'Grande Empresa': '5'
        }
        porte_codigo = mapeamento_reverso.get(st.session_state.filtros['porte'], st.session_state.filtros['porte'])
        query_count += " AND emp.porte_empresa = %s"
        params.append(porte_codigo)

    if st.session_state.filtros['situacao'] != "Todos":
        situacao_map = {"Ativa": 2, "Baixada": 3, "Suspensa": 4, "Inapta": 8, "Nula": 5}
        query_count += " AND est.situacao_cadastral = %s"
        params.append(situacao_map[st.session_state.filtros['situacao']])

    # Filtro por capital social (range) - só aplica se não estiver em modo "sem limite"
    if not st.session_state.filtros.get('sem_limite_capital', False):
        query_count += " AND emp.capital_social BETWEEN %s AND %s"
        params.append(st.session_state.filtros['capital_min'])
        params.append(st.session_state.filtros['capital_max'])
    else:
        # No modo "sem limite", ainda filtra capital social maior que 0 para evitar nulos
        query_count += " AND emp.capital_social > 0"
    
    try:
        result, _ = db.execute_query(query_count, params)
        return result[0][0] if result else 0
    except Exception as e:
        print(f"Erro ao contar empresas: {e}")
        return 0

# Sidebar com filtros
st.sidebar.header("Filtros de Pesquisa")

# Filtro por CNPJ
cnpj_filter = st.sidebar.text_input(
    "CNPJ (completo ou parcial)",
    value=st.session_state.filtros['cnpj'],
    key='cnpj_input'
)

# Filtro por Razão Social
razao_social_filter = st.sidebar.text_input(
    "Razão Social (completo ou parcial)",
    value=st.session_state.filtros['razao_social'],
    key='razao_input'
)

# Filtro por Nome Fantasia
nome_fantasia_filter = st.sidebar.text_input(
    "Nome Fantasia (completo ou parcial)",
    value=st.session_state.filtros['nome_fantasia'],
    key='fantasia_input'
)

# Filtro por CNAE - MODIFICADO PARA MULTISELECT
cnae_options = get_cnaes_options()
cnae_dict = {desc: cod for cod, desc in cnae_options}

# Encontra os índices atuais baseado no session state
cnae_defaults = []
for cod, desc in cnae_options:
    if cod in st.session_state.filtros['cnae'] or (not st.session_state.filtros['cnae'] and desc == "Todos"):
        cnae_defaults.append(desc)

cnae_filter = st.sidebar.multiselect(
    "CNAE Fiscal Principal (selecione um ou mais)",
    options=[desc for cod, desc in cnae_options],
    default=cnae_defaults,
    key='cnae_select',
    help="Selecione um ou mais CNAEs para filtrar"
)

# Se "Todos" estiver selecionado junto com outros, remover "Todos"
if "Todos" in cnae_filter and len(cnae_filter) > 1:
    cnae_filter = [cnae for cnae in cnae_filter if cnae != "Todos"]
    st.sidebar.warning("'Todos' foi removido pois outros CNAEs foram selecionados.")

# Se nenhum CNAE selecionado, usar "Todos"
if not cnae_filter:
    cnae_filter = ["Todos"]

# Filtro por UF
ufs = get_ufs()
uf_options = ["Todos"] + ufs
uf_filter = st.sidebar.selectbox(
    "UF",
    options=uf_options,
    index=uf_options.index(st.session_state.filtros['uf']),
    key='uf_select'
)

# Filtro por Porte
portes = get_portes()
porte_options = ["Todos"] + portes
porte_filter = st.sidebar.selectbox(
    "Porte da Empresa",
    options=porte_options,
    index=porte_options.index(st.session_state.filtros['porte']),
    key='porte_select'
)

# Filtro por Situação Cadastral
situacao_options = ["Todos", "Ativa", "Baixada", "Suspensa", "Inapta", "Nula"]
situacao_map = {"Ativa": 2, "Baixada": 3, "Suspensa": 4, "Inapta": 8, "Nula": 5}
situacao_filter = st.sidebar.selectbox(
    "Situação Cadastral",
    options=situacao_options,
    index=situacao_options.index(st.session_state.filtros['situacao']),
    key='situacao_select'
)

# Filtro por Capital Social (Range) com opção sem limite
capital_min, capital_max = get_capital_range()
st.sidebar.write("**Capital Social (R$)**")

# Checkbox para sem limite
sem_limite_capital = st.sidebar.checkbox(
    "Sem limite de capital", 
    value=st.session_state.filtros.get('sem_limite_capital', False),
    key='sem_limite_checkbox'
)

if sem_limite_capital:
    # Se selecionou "Sem limite", mostra apenas um slider informativo desabilitado
    capital_range = st.sidebar.slider(
        "Capital Social (sem limite aplicado)",
        min_value=int(capital_min),
        max_value=int(capital_max),
        value=(0, int(capital_max)),
        key='capital_slider_disabled',
        format="R$ %d",
        disabled=True
    )
    # Define o range máximo para la query
    capital_range = (0, float('inf'))
else:
    # Slider normal com range selecionável
    capital_range = st.sidebar.slider(
        "Selecione a faixa de capital social",
        min_value=int(capital_min),
        max_value=int(capital_max),
        value=(int(st.session_state.filtros['capital_min']), int(st.session_state.filtros['capital_max'])),
        key='capital_slider',
        format="R$ %d"
    )

# Limite de resultados
limit_results = st.sidebar.slider(
    "Limite de resultados", 
    10, 500, st.session_state.filtros['limit'],
    key='limit_slider'
)

# Contador de empresas que correspondem aos filtros
if st.session_state.filtros['cnpj'] or st.session_state.filtros['razao_social'] or st.session_state.filtros['nome_fantasia'] or st.session_state.filtros['uf'] != "Todos" or st.session_state.filtros['porte'] != "Todos" or st.session_state.filtros['situacao'] != "Todos" or st.session_state.filtros['cnae']:
    total_empresas = contar_empresas_filtradas()
    st.sidebar.info(f"📊 **{total_empresas} empresas** correspondem aos filtros atuais")

# Botão para executar consulta
executar_consulta = st.sidebar.button("🔍 Executar Consulta", type="primary", key='executar_btn')

# Botão para limpar filtros
if st.sidebar.button("🧹 Limpar Filtros", key='limpar_btn'):
    st.session_state.filtros = {
        'cnpj': '', 
        'razao_social': '', 
        'nome_fantasia': '',
        'uf': 'Todos', 
        'porte': 'Todos', 
        'situacao': 'Todos',
        'cnae': [],  # Alterado para lista vazia
        'capital_min': 0,
        'capital_max': 500000,
        'sem_limite_capital': False,
        'limit': 100
    }
    st.rerun()

# Construção da query principal
query = """
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
    est.cnae_fiscal_principal
FROM estabelecimento est
LEFT JOIN empresa emp ON est.cnpj_basico = emp.cnpj_basico
WHERE 1=1
"""

params = []

# Aplicar filtros
if cnpj_filter:
    query += " AND est.cnpj ILIKE %s"
    params.append(f'%{cnpj_filter}%')

if razao_social_filter:
    query += " AND emp.razao_social ILIKE %s"
    params.append(f'%{razao_social_filter}%')

if nome_fantasia_filter:
    query += " AND est.nome_fantasia ILIKE %s"
    params.append(f'%{nome_fantasia_filter}%')

# MODIFICADO: Filtro CNAE usando IN para múltiplos valores
if "Todos" not in cnae_filter:
    # Obtém os códigos CNAE das seleções
    cnae_codigos = [cnae_dict.get(cnae, cnae) for cnae in cnae_filter]
    
    # Cria placeholders para a cláusula IN
    placeholders = ', '.join(['%s'] * len(cnae_codigos))
    query += f" AND est.cnae_fiscal_principal IN ({placeholders})"
    params.extend(cnae_codigos)

if uf_filter != "Todos":
    query += " AND est.uf = %s"
    params.append(uf_filter)

if porte_filter != "Todos":
    # Reverter a tradução para o código original
    mapeamento_reverso = {
        'Micro Empresa': '1',
        'Pequena Empresa': '2',
        'Média Empresa': '3',
        'Demais': '4',
        'Grande Empresa': '5'
    }
    porte_codigo = mapeamento_reverso.get(porte_filter, porte_filter)
    query += " AND emp.porte_empresa = %s"
    params.append(porte_codigo)

if situacao_filter != "Todos":
    query += " AND est.situacao_cadastral = %s"
    params.append(situacao_map[situacao_filter])

# Filtro por capital social (range) - só aplica se não estiver em modo "sem limite"
if not sem_limite_capital:
    query += " AND emp.capital_social BETWEEN %s AND %s"
    params.append(capital_range[0])
    params.append(capital_range[1])
else:
    # No modo "sem limite", ainda filtra capital social maior que 0 para evitar nulos
    query += " AND emp.capital_social > 0"

# Ordenação e limite
query += " ORDER BY emp.razao_social LIMIT %s"
params.append(limit_results)

# Executar a consulta quando o botão for pressionado
if executar_consulta:
    # Salvar os valores atuais nos session state
    st.session_state.filtros = {
        'cnpj': cnpj_filter,
        'razao_social': razao_social_filter,
        'nome_fantasia': nome_fantasia_filter,
        'cnae': [cnae_dict.get(cnae, cnae) for cnae in cnae_filter],  # LINHA MODIFICADA
        'uf': uf_filter,
        'porte': porte_filter,
        'situacao': situacao_filter,
        'capital_min': capital_range[0],
        'capital_max': capital_range[1],
        'sem_limite_capital': sem_limite_capital,
        'limit': limit_results
    }
    
    with st.spinner("Executando consulta..."):
        try:
            resultados, colunas = db.execute_query(query, params)
            
            if resultados:
                # Converter para DataFrame
                df = pd.DataFrame(resultados, columns=colunas)
                
                # Mostrar estatísticas
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total de Empresas", len(df))
                with col2:
                    st.metric("UF's Diferentes", df['uf'].nunique())
                with col3:
                    capital_total = df['capital_social'].sum() if 'capital_social' in df.columns and df['capital_social'].notna().any() else 0
                    st.metric("Capital Social Total", f"R$ {capital_total:,.2f}")
                with col4:
                    empresas_ativas = len(df[df['situacao_cadastral'] == 2]) if 'situacao_cadastral' in df.columns else 0
                    st.metric("Empresas Ativas", empresas_ativas)
                
                # Adicionar CNPJ formatado para exibição
                if 'cnpj' in df.columns:
                    df['cnpj_formatado'] = df['cnpj'].apply(formatar_cnpj)
                
                # Adicionar capital social formatado para exibição
                if 'capital_social' in df.columns:
                    df['capital_social_formatado'] = df['capital_social'].apply(formatar_moeda)
                
                # Adicionar porte traduzido para exibição
                if 'porte_empresa' in df.columns:
                    df['porte_traduzido'] = df['porte_empresa'].apply(traduzir_porte)
                
                # Mostrar tabela com CNPJ formatado e capital social
                colunas_exibicao = [
                    'cnpj_formatado', 
                    'razao_social', 
                    'nome_fantasia', 
                    'uf', 
                    'porte_traduzido',
                    'capital_social_formatado',
                    'cnae_fiscal_principal',
                    'data_inicio_atividade',
                    'situacao_cadastral'
                ]
                
                # Filtrar apenas colunas que existem no DataFrame
                colunas_exibicao = [col for col in colunas_exibicao if col in df.columns]
                
                # Renomear colunas para exibição mais amigável
                df_exibicao = df[colunas_exibicao].copy()
                df_exibicao.columns = [
                    'CNPJ', 
                    'Razão Social', 
                    'Nome Fantasia', 
                    'UF', 
                    'Porte',
                    'Capital Social',
                    'CNAE Principal',
                    'Data Início',
                    'Situação'
                ]
                
                st.dataframe(
                    df_exibicao,
                    use_container_width=True,
                    hide_index=True,
                    height=400
                )
                
                # OPÇÃO DE DOWNLOAD CORRIGIDA (não causa refresh)
                # Usar o DataFrame original para download (com dados numéricos)
                csv = df.to_csv(index=False, sep=';', decimal=',')
                st.markdown(f"""
                <a href="data:file/csv;base64,{base64.b64encode(csv.encode()).decode()}" download="empresas.csv">
                    <button style="background-color:#4CAF50; color:white; padding:10px 20px; border:none; border-radius:4px; cursor:pointer;">
                        📥 Download CSV
                    </button>
                </a>
                """, unsafe_allow_html=True)
                
                # Visualizações gráficas
                st.subheader("📊 Visualizações Gráficas")
                
                # Criar abas para organizar os gráficos
                tab1, tab2, tab3, tab4, tab5 = st.tabs(["Distribuição Geográfica", "Situação e Porte", "Capital Social", "CNAE", "Linha do Tempo"])
                
                with tab1:
                    # Gráfico de empresas por UF
                    if 'uf' in df.columns:
                        uf_count = df['uf'].value_counts()
                        if not uf_count.empty:
                            if PLOTLY_AVAILABLE:
                                fig_uf = px.bar(uf_count, x=uf_count.index, y=uf_count.values,
                                               title="Distribuição de Empresas por UF",
                                               labels={'x': 'UF', 'y': 'Quantidade de Empresas'},
                                               color=uf_count.values)
                                fig_uf.update_layout(xaxis_tickangle=-45)
                                st.plotly_chart(fig_uf, use_container_width=True)
                            else:
                                st.bar_chart(uf_count)
                
                with tab2:
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        # Gráfico de situação cadastral
                        if 'situacao_cadastral' in df.columns:
                            situacao_map_inverso = {2: 'Ativa', 3: 'Baixada', 4: 'Suspensa', 8: 'Inapta', 5: 'Nula'}
                            df_situacao = df['situacao_cadastral'].map(situacao_map_inverso).value_counts()
                            
                            if not df_situacao.empty:
                                if PLOTLY_AVAILABLE:
                                    fig_situacao = px.pie(df_situacao, values=df_situacao.values, 
                                                         names=df_situacao.index,
                                                         title="Distribuição por Situação Cadastral")
                                    st.plotly_chart(fig_situacao, use_container_width=True)
                                else:
                                    st.bar_chart(df_situacao)
                    
                    with col2:
                        # Gráfico de empresas por porte (usando a versão traduzida)
                        if 'porte_traduzido' in df.columns:
                            porte_count = df['porte_traduzido'].value_counts()
                            if not porte_count.empty:
                                if PLOTLY_AVAILABLE:
                                    fig_porte = px.bar(porte_count, x=porte_count.index, y=porte_count.values,
                                                      title="Distribuição por Porte da Empresa",
                                                      labels={'x': 'Porte', 'y': 'Quantidade'})
                                    st.plotly_chart(fig_porte, use_container_width=True)
                                else:
                                    st.bar_chart(porte_count)
                
                with tab3:
                    # Gráfico de capital social por UF
                    if 'capital_social' in df.columns and 'uf' in df.columns:
                        capital_por_uf = df.groupby('uf')['capital_social'].sum().sort_values(ascending=False)
                        if not capital_por_uf.empty and capital_por_uf.sum() > 0:
                            if PLOTLY_AVAILABLE:
                                fig_capital = px.bar(capital_por_uf, x=capital_por_uf.index, y=capital_por_uf.values,
                                                   title="Capital Social Total por UF (em R$)",
                                                   labels={'x': 'UF', 'y': 'Capital Social'})
                                fig_capital.update_layout(yaxis_tickformat=",.2f")
                                st.plotly_chart(fig_capital, use_container_width=True)
                            else:
                                st.bar_chart(capital_por_uf)
                        else:
                            st.info("Não há dados de capital social disponíveis para visualização.")
                
                with tab4:
                    # Gráfico de CNAEs mais comuns
                    if 'cnae_fiscal_principal' in df.columns:
                        cnae_count = df['cnae_fiscal_principal'].value_counts().head(10)
                        if not cnae_count.empty:
                            if PLOTLY_AVAILABLE:
                                fig_cnae = px.bar(cnae_count, x=cnae_count.index, y=cnae_count.values,
                                                 title="Top 10 CNAEs Principais",
                                                 labels={'x': 'CNAE', 'y': 'Quantidade de Empresas'})
                                fig_cnae.update_layout(xaxis_tickangle=-45)
                                st.plotly_chart(fig_cnae, use_container_width=True)
                            else:
                                st.bar_chart(cnae_count)
                
                with tab5:
                    # Gráfico de empresas por ano de início
                    if 'data_inicio_atividade' in df.columns:
                        try:
                            df['ano_inicio'] = pd.to_datetime(df['data_inicio_atividade']).dt.year
                            ano_count = df['ano_inicio'].value_counts().sort_index()
                            
                            if not ano_count.empty:
                                if PLOTLY_AVAILABLE:
                                    fig_ano = px.line(ano_count, x=ano_count.index, y=ano_count.values,
                                                     title="Empresas por Ano de Início",
                                                     labels={'x': 'Ano', 'y': 'Quantidade de Empresas'})
                                    st.plotly_chart(fig_ano, use_container_width=True)
                                else:
                                    st.line_chart(ano_count)
                        except:
                            st.info("Não foi possível processar os dados de data de início.")
                
            else:
                st.warning("Nenhum resultado encontrado com os filtros aplicados.")
                
        except Exception as e:
            st.error(f"Erro na consulta: {e}")
            st.info("Tente ajustar os filtros ou reduzir o limite de resultados")

# Seção de detalhes da empresa
st.markdown("---")
st.subheader("Detalhes da Empresa")

cnpj_detalhes = st.text_input("Digite o CNPJ completo para ver detalhes (somente números):")
buscar_detalhes = st.button("Buscar Detalhes")

if buscar_detalhes and cnpj_detalhes:
    # Remove formatação se o usuário digitou com pontuação
    cnpj_limpo = ''.join(filter(str.isdigit, cnpj_detalhes))
    
    # Verifica se o CNPJ tem o tamanho correto (14 dígitos)
    if len(cnpj_limpo) != 14:
        st.error("CNPJ deve conter exatamente 14 dígitos")
    else:
        with st.spinner("Buscando detalhes..."):
            try:
                # Query para detalhes básicos - CORREÇÃO: usando cnpj_limpo sem formatação
                query_detalhes = """
                SELECT 
                    emp.razao_social,
                    est.nome_fantasia,
                    est.cnpj,
                    est.uf,
                    m.descricao as municipio,
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
                    cnae.descricao as descricao_cnae
                FROM estabelecimento est
                LEFT JOIN empresa emp ON est.cnpj_basico = emp.cnpj_basico
                LEFT JOIN munic m ON est.municipio = m.codigo
                LEFT JOIN cnae cnae ON est.cnae_fiscal_principal = cnae.codigo
                WHERE est.cnpj = %s
                """
                
                resultado_detalhes, colunas_detalhes = db.execute_query(query_detalhes, (cnpj_limpo,))
                
                if resultado_detalhes:
                    df_detalhes = pd.DataFrame(resultado_detalhes, columns=colunas_detalhes)
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.write("**Informações Básicas**")
                        st.write(f"**Razão Social:** {df_detalhes['razao_social'].iloc[0] if pd.notna(df_detalhes['razao_social'].iloc[0]) else 'N/A'}")
                        st.write(f"**Nome Fantasia:** {df_detalhes['nome_fantasia'].iloc[0] if pd.notna(df_detalhes['nome_fantasia'].iloc[0]) else 'N/A'}")
                        st.write(f"**CNPJ:** {formatar_cnpj(df_detalhes['cnpj'].iloc[0])}")
                        
                        # Traduzir o porte para exibição
                        porte = df_detalhes['porte_empresa'].iloc[0] if pd.notna(df_detalhes['porte_empresa'].iloc[0]) else 'N/A'
                        st.write(f"**Porte:** {traduzir_porte(porte)}")
                        
                        # Formatação do capital social
                        capital_social = df_detalhes['capital_social'].iloc[0]
                        if pd.notna(capital_social):
                            try:
                                st.write(f"**Capital Social:** R$ {capital_social:,.2f}")
                            except:
                                st.write(f"**Capital Social:** R$ {capital_social}")
                        else:
                            st.write("**Capital Social:** N/A")
                        
                        st.write(f"**Data Início Atividade:** {df_detalhes['data_inicio_atividade'].iloc[0] if pd.notna(df_detalhes['data_inicio_atividade'].iloc[0]) else 'N/A'}")
                        
                        # Traduzir situação cadastral
                        situacao = df_detalhes['situacao_cadastral'].iloc[0]
                        situacao_map_inverso = {2: 'Ativa', 3: 'Baixada', 4: 'Suspensa', 8: 'Inapta', 5: 'Nula'}
                        situacao_desc = situacao_map_inverso.get(situacao, situacao)
                        st.write(f"**Situação Cadastral:** {situacao_desc}")
                        
                        # Informações do CNAE
                        st.write(f"**CNAE Principal:** {df_detalhes['cnae_fiscal_principal'].iloc[0] if pd.notna(df_detalhes['cnae_fiscal_principal'].iloc[0]) else 'N/A'}")
                        if 'descricao_cnae' in df_detalhes.columns:
                            st.write(f"**Descrição CNAE:** {df_detalhes['descricao_cnae'].iloc[0] if pd.notna(df_detalhes['descricao_cnae'].iloc[0]) else 'N/A'}")
                    
                    with col2:
                        st.write("**Informações de Endereço**")
                        st.write(f"**UF:** {df_detalhes['uf'].iloc[0] if pd.notna(df_detalhes['uf'].iloc[0]) else 'N/A'}")
                        st.write(f"**Município:** {df_detalhes['municipio'].iloc[0] if pd.notna(df_detalhes['municipio'].iloc[0]) else 'N/A'}")
                        
                        # Montando o endereço completo
                        tipo_logradouro = df_detalhes['tipo_logradouro'].iloc[0] if 'tipo_logradouro' in df_detalhes.columns and pd.notna(df_detalhes['tipo_logradouro'].iloc[0]) else ''
                        logradouro = df_detalhes['logradouro'].iloc[0] if pd.notna(df_detalhes['logradouro'].iloc[0]) else ''
                        numero = df_detalhes['numero'].iloc[0] if pd.notna(df_detalhes['numero'].iloc[0]) else ''
                        
                        endereco_completo = f"{tipo_logradouro} {logradouro}, {numero}".strip()
                        if endereco_completo and endereco_completo != ",":
                            st.write(f"**Endereço:** {endereco_completo}")
                        else:
                            st.write("**Endereço:** N/A")
                        
                        st.write(f"**Bairro:** {df_detalhes['bairro'].iloc[0] if pd.notna(df_detalhes['bairro'].iloc[0]) else 'N/A'}")
                        
                        # Formatando CEP (XXXXX-XXX)
                        cep = df_detalhes['cep'].iloc[0] if pd.notna(df_detalhes['cep'].iloc[0]) else ''
                        if cep and len(cep) == 8:
                            st.write(f"**CEP:** {cep[:5]}-{cep[5:]}")
                        else:
                            st.write(f"**CEP:** {cep}")
                        
                        st.write(f"**Complemento:** {df_detalhes['complemento'].iloc[0] if pd.notna(df_detalhes['complemento'].iloc[0]) else 'N/A'}")
                    
                    # Informações de contato
                    st.write("**Informações de Contato**")
                    col1, col2 = st.columns(2)
                    with col1:
                        ddd = df_detalhes['ddd_1'].iloc[0] if pd.notna(df_detalhes['ddd_1'].iloc[0]) else ''
                        telefone = df_detalhes['telefone_1'].iloc[0] if pd.notna(df_detalhes['telefone_1'].iloc[0]) else 'N/A'
                        st.write(f"**Telefone:** ({ddd}) {telefone}")
                    with col2:
                        st.write(f"**Email:** {df_detalhes['correio_eletronico'].iloc[0] if pd.notna(df_detalhes['correio_eletronico'].iloc[0]) else 'N/A'}")
                    
                else:
                    st.warning("Nenhuma empresa encontrada com este CNPJ.")
                    
            except Exception as e:
                st.error(f"Erro ao buscar detalhes: {e}")

# Rodapé
st.markdown("---")
st.caption("Sistema de consulta empresarial - Desenvolvido com Streamlit e PostgreSQL")