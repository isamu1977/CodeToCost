# **Master Blueprint: Canal B2B de Arquitetura de Automação, SaaS & IA (Global / Inglês)**  
**Objetivo do Documento:** Servir como especificação técnica, operacional e de negócios (Guia Mestre) para implementação do canal secundário no YouTube, integrando a **JingJing (Hermes Agent no MacBook Pro)** à esteira programática existente (LLM + TTS + Whisper + Imagens IA + Remotion).  
   
## **1. Posicionamento Editorial e Tese de Monetização**  
- **Idioma Primário:** Inglês (en-US), focado em tráfego de busca de alta intenção nos mercados Tier 1 (EUA, Reino Unido, Canadá, Austrália e Europa Ocidental).  
-    
- **Arquitetura Multi-Idioma (Pronta para Clonagem):** O código visual do Remotion será **100% orientado a dados**. Se um vídeo em inglês validar alta conversão, bastará a JingJing traduzir o bloco de locução e os rótulos do JSON para Português ou Japonês e re-renderizar o vídeo em 5 minutos, sem tocar no código React.  
-    
- **Proposta de Valor Única (UVP):** *"Clear Architecture, Real Math, Zero Fluff."* Enquanto outros canais fazem tutoriais lentos de 25 minutos clicando na tela ou reviews superficiais patrocinados, este canal entrega em **5 a 8 minutos** a arquitetura visual do sistema, a matemática real de custos em escala (limites ocultos de API/SaaS) e o veredito técnico direto.  
-    
- **Avatar Comprador:** Fundadores de micro-SaaS, donos de agências de automação, engenheiros de software, líderes de DX/TI e *prosumers* técnicos que tomam decisões de compra de software e infraestrutura.  
-    
## **2. O Papel da JingJing (Hermes Agent no MacBook Pro)**  
Por rodar localmente no seu MacBook Pro com acesso ao terminal (Bash), sistema de arquivos, navegação web e memória persistente, a **JingJing** não deve ser usada apenas como uma "geradora de texto", mas como a **Engenheira de Produção e Analista de Inteligência do Canal**.  
   
### **As 5 Skills (Módulos de Trabalho) da JingJing neste Projeto**  
1. skill_market_scraper **(Motor Anti-Alucinação & Fact-Checking):**  
2.    
  - Antes de qualquer roteiro existir, a JingJing acessa as páginas oficiais de /pricing, /limits e documentações das ferramentas (via curl + Jina Reader \[https://r.jina.ai/\](https://r.jina.ai/)\<url>, Firecrawl ou browser local), além de buscar discussões reais no Reddit (r/selfhosted, r/n8n, r/SaaS, r/LocalLLaMA) sobre dores e custos ocultos.  
  -    
  - Ela salva um dossiê factual atualizado em Markdown (research_dossier.md) com preços exatos do dia.  
  -    
3. skill_cost_simulator **(Calculadora Quantitativa via Python):**  
4.    
  - A JingJing executa um script Python local para calcular os pontos de inflexão matemática (ex.: *"Com 10.000 execuções/mês a ferramenta A custa US\$ 20, mas com 100.000 execuções ela salta para US\$ 290, enquanto a arquitetura B em VPS mantém custo fixo de US\$ 12"*). Esses números alimentam os gráficos animados do Remotion.  
  -    
5. skill_script_compiler **(Compilador de Roteiro + Cena + Fonética):**  
6.    
  - Transforma o dossiê em um arquivo estruturado video_spec.json, separando estritamente:  
  -    
    - spoken_text: Texto otimizado foneticamente para o TTS em inglês não errar siglas.  
    -    
    - visual_scene: Qual componente React do Remotion será renderizado na tela e quais dados ele receberá.  
    -    
7. skill_pipeline_runner **(Orquestradora Local no macOS):**  
8.    
  - Pelo terminal do MacBook Pro, a JingJing dispara sequencialmente:  
  -    
    9. O motor de TTS local gerando os arquivos .wav por cena.  
    10.    
    11. O faster-whisper extraindo os *timestamps* exatos palavra por palavra.  
    12.    
    13. O script de *merge* que injeta os tempos do Whisper no timeline.json.  
    14.    
    15. O comando npx remotion render usando a aceleração de hardware do Apple Silicon.  
    16.    
9. skill_asset_packager **(Fábrica de Ativos de Monetização):**  
10.    
  - Para cada vídeo produzido, a JingJing gera automaticamente o **Ativo Complementar** daquele vídeo (ex.: um docker-compose.yml pronto para produção, um template JSON de workflow ou uma calculadora de custos) e gera o texto da descrição do YouTube com os links de afiliados corretos e *timestamps* de capítulos.  
  -    
## **3. Estrutura de Diretórios e Contrato de Dados** (video_spec.json)  
Para que o projeto escale de forma limpa e organizada no seu MacBook Pro, estruture o repositório principal assim:  
 youtube-b2b-engine/  
├── agent_workspace/               # Área de operação da JingJing (Hermes Agent)  
│   ├── skills/                    # Instruções operacionais da JingJing  
│   ├── phonetic_dictionary.json   # Regras de pronúncia em inglês para o TTS  
│   └── affiliate_catalog.json     # Links de afiliados, cupons e CTAs mapeados por ferramenta  
├── projects/                      # Cada vídeo é uma pasta isolada e reproduzível  
│   └── 001-n8n-vs-make-scale/  
│       ├── 01_research_dossier.md # Dados reais raspados pela JingJing  
│       ├── 02_video_spec.json     # Roteiro + Props dos componentes Remotion  
│       ├── 03_audio/              # Áudios gerados pelo TTS + JSON do Whisper  
│       ├── 04_assets/             # Logos SVG e backgrounds estilizados  
│       ├── 05_lead_magnet/        # Blueprint/Template gerado para venda ou captura  
│       └── output/                # video_final.mp4 + youtube_metadata.md  
├── pipeline/                      # Scripts Python de automação (TTS, Whisper, Merge)  
└── remotion-app/                  # Projeto React/Remotion com os componentes visuais  
###    
###    
### **O Contrato JSON** (02_video_spec.json)  
Este é o coração técnico que conecta a JingJing ao Remotion. Observe a separação entre spoken_text (para o TTS) e component_props (para o React):  
   
{  
  "video_id": "001-n8n-vs-make-scale",  
  "language": "en-US",  
  "title": "n8n vs Make (2026): The Real Cost at 100,000 Operations",  
  "primary_affiliate": "hostinger_vps",  
  "secondary_affiliate": "make_pro",  
  "scenes": \[  
    {  
      "scene_id": "hook_01",  
      "spoken_text": "At ten thousand operations a month, Make dot com looks like a bargain at ten dollars and fifty nine cents. But the moment you add multi-step A-I agents, your bill quietly explodes.",  
      "display_subtitle": "At 10,000 ops/mo, Make looks like a bargain (\$10.59). Add multi-step AI agents, and your bill explodes.",  
      "visual_component": "CostExplosionChart",  
      "component_props": {  
        "headline": "Monthly Cost Projection (USD)",  
        "data_points": \[  
          { "volume": "10k Ops", "make_cost": 10.59, "n8n_self_hosted": 12.00 },  
          { "volume": "50k Ops", "make_cost": 53.00, "n8n_self_hosted": 12.00 },  
          { "volume": "250k Ops", "make_cost": 212.00, "n8n_self_hosted": 18.00 }  
        \],  
        "highlight_index": 2  
      }  
    },  
    {  
      "scene_id": "arch_02",  
      "spoken_text": "Here is why: Make charges per module execution, while N-eight-N charges per full workflow run—or zero dollars if you self-host it on a Docker V-P-S with Postgres.",  
      "display_subtitle": "Make charges per module step. n8n charges per full workflow execution—or \$0 on a Docker VPS.",  
      "visual_component": "ArchitectureFlow",  
      "component_props": {  
        "mode": "split_comparison",  
        "left_stack": { "title": "Make (Per-Step Billing)", "nodes": \["Webhook (\$)", "JSON Parse (\$)", "OpenAI API (\$)", "Postgres Insert (\$)"\] },  
        "right_stack": { "title": "Self-Hosted n8n (Fixed Cost)", "nodes": \["Docker VPS (\$12/mo)", "Unlimited Steps", "PostgreSQL Queue", "Worker Nodes"\] }  
      }  
    }  
  \]  
}  
   
###    
### **Camada de Normalização Fonética em Inglês** (phonetic_dictionary.json)  
Para que o seu TTS local soe como um engenheiro nativo americano e nunca tropece em jargões, o script Python deve aplicar este dicionário antes de sintetizar a voz:  
   
- "n8n" \$\rightarrow\$ "N-eight-N"  
-    
- "PostgreSQL" \$\rightarrow\$ "Postgres Q-L"  
-    
- "SQLite" \$\rightarrow\$ "S-Q-Lite"  
-    
- "SaaS" \$\rightarrow\$ "Sass"  
-    
- "CLI" \$\rightarrow\$ "C-L-I"  
-    
- "GUI" \$\rightarrow\$ "gooey"  
-    
- "RAG" \$\rightarrow\$ "rag" (ou "R-A-G" na primeira ocorrência)  
-    
- "\$12/mo" \$\rightarrow\$ "twelve dollars a month"  
-    
- "100k" \$\rightarrow\$ "one hundred thousand"  
-    
## **4. Engenharia Visual no Remotion (Os 6 Componentes Mestres)**  
Em vez de criar animações do zero a cada vídeo, você programará **apenas 6 componentes React altamente polidos** (tema *Dark Mode* moderno, inspirado na estética da Vercel/Linear/Raycast, usando Tailwind CSS + spring() do Remotion + ícones SVG oficiais via simple-icons). As imagens geradas por IA entrarão apenas no fundo com opacity: 0.18 e backdrop-filter: blur(8px) para dar profundidade e textura.  
   
1. \<ArchitectureFlow/>**:** Renderiza caixas de sistemas (APIs, Bancos de Dados, LLMs, Filas) conectadas por linhas pontilhadas animadas que acendem na ordem em que o narrador explica o fluxo.  
2.    
3. \<PricingCurveChart/>**:** Gráfico de barras ou linhas animado mostrando onde uma ferramenta barata fica mais cara que a concorrente conforme o tráfego cresce.  
4.    
5. \<SpecComparisonTable/>**:** Tabela comparativa lado a lado onde cada linha entra sincronizada com o áudio, marcando com check verde (✓), alerta amarelo (⚠) ou erro vermelho (✗).  
6.    
7. \<BottleneckCallout/>**:** Um card de alerta técnico em destaque para mostrar limites ocultos de documentação (ex.: *Rate Limits*, *Timeout de 30s*, *Vendor Lock-in*).  
8.    
9. \<BlueprintPromoBanner/>**:** Um *lower-third* (banner inferior) elegante que entra por 6 segundos aos 35% do vídeo dizendo: *"Grab the ready-to-deploy Docker Compose & JSON Workflow in the description"*.  
10.    
11. \<VerdictMatrix/>**:** O quadro final do vídeo (*"Use A if... / Use B if..."*), que segura a retenção até os últimos segundos e direciona para o clique nos links da descrição.  
12.    
## **5. A Pilha de Monetização (Do Dia 1 ao Dia 90)**  
Como o canal não terá AdSense no primeiro dia, toda a receita inicial virá da combinação de **Links de Afiliados de Alta Conversão** + **Venda de Blueprints Técnicos**.  
   
### **1. O Portfólio Inicial de Afiliados (Cadastro na Semana 1)**  
Você não precisa de dezenas de links. Comece com 4 categorias que aparecem em quase todos os vídeos desse nicho:  
   
- **Infraestrutura VPS / Cloud (Comissão alta por venda única):** Hostinger VPS, DigitalOcean, Vultr, Hetzner ou Railway (sempre que o vídeo mostrar como economizar fazendo *self-hosting* com Docker).  
-    
- **Automação & Integração (Comissão recorrente em USD):** Make.com (paga até 20% recorrente por 24 meses), n8n Cloud Affiliate, Zapier.  
-    
- **Bancos de Dados, Backend & Scraping (B2B Developer Tools):** Supabase, Firecrawl, Apify, BrightData, Airbyte.  
-    
- **Ferramentas de IA & Produtividade:** ElevenLabs, HeyGen, OpenRouter / provedores de API e ferramentas No-Code (Softr, Glide, Retool).  
-    
### **2. O Produto Próprio de Entrada ("The Architecture Vault")**  
Abra uma conta no **LemonSqueezy** ou **Gumroad** (ambos processam pagamentos globais em dólar sem burocracia e entregam arquivos digitais automaticamente).  
   
- **Oferta Individual (US\$ 19):** O pacote de implementação do vídeo (ex.: *"Production-Ready Self-Hosted Automation Stack: Docker Compose + Traefik SSL + Postgres Backups + 5 Ready Workflows"*).  
-    
- **Oferta Bundle (US\$ 49):** Acesso à biblioteca completa com todos os diagramas, calculadoras de custo e templates de código do canal.  
-    
- **Isca Gratuita (Lead Magnet):** Uma planilha/calculadora interativa gratuita (*"The SaaS vs Self-Hosted Cost Calculator"*) em troca do e-mail do usuário. Assim você constrói uma lista de e-mails de decisores técnicos em paralelo ao YouTube.  
   
   
### **3. Template de Descrição de Alta Conversão (Gerado pela JingJing)**  
As primeiras 3 linhas da descrição aparecem acima do botão *"Mostrar mais"* no YouTube e precisam conter os links de ação direta:  
 ⚡ Grab the Production Docker Stack & Workflow Blueprints from this video: https://seu-link.com/blueprint  
🚀 Deploy this exact architecture on a \$6/mo High-Performance VPS (Discount Link): https://seu-link.com/vps  
🔄 Try Make Pro with 10,000 Free Operations: https://seu-link.com/make  
   
In this architectural breakdown, we calculate the real inflection point between...  
\[00:00\] The \$10/mo Pricing Illusion  
\[01:15\] Per-Step vs Per-Workflow Billing Math  
...  
## **6. Passo a Passo de Implementação (4 Sprints Práticos)**  
### **Sprint 1: Fundação Visual e Contrato de Dados (Dias 1 a 7)**  
- \[ \] Clonar o seu repositório atual de geração de vídeos (LLM + TTS + Whisper + Remotion) para uma nova pasta dedicada ao projeto em inglês.  
-    
- \[ \] Definir o schema TypeScript do 02_video_spec.json dentro do projeto Remotion.  
-    
- \[ \] Criar os **6 Componentes Mestres** em React no Remotion (começando por \<PricingCurveChart/>, \<ArchitectureFlow/> e \<SpecComparisonTable/>).  
-    
- \[ \] Configurar o dicionário de normalização fonética (phonetic_dictionary.json) no script Python antes da chamada ao TTS local e testar a pronúncia em inglês de 20 termos técnicos.  
-    
### **Sprint 2: Configuração das Skills da JingJing no MacBook Pro (Dias 8 a 14)**  
- \[ \] Criar a pasta de workspace da JingJing e configurar o prompt de sistema (veja a seção 7 abaixo) para que ela opere como Arquiteta de Soluções e Produtora Executiva do canal.  
-    
- \[ \] Testar a skill_market_scraper: pedir para a JingJing extrair os preços atuais e limites técnicos de duas ferramentas reais (ex.: *Make vs n8n* ou *Supabase vs Firebase*) e salvar o 01_research_dossier.md.  
-    
- \[ \] Testar a geração ponta a ponta do Vídeo Piloto #001 no MacBook Pro: Dossiê \$\rightarrow\$ JSON \$\rightarrow\$ TTS + Whisper \$\rightarrow\$ Renderização no Remotion.  
-    
### **Sprint 3: Infraestrutura de Monetização e Lote Semente (Dias 15 a 30)**  
- \[ \] Criar o canal no YouTube configurado com idioma padrão **English (United States)** e identidade visual limpa e técnica.  
-    
- \[ \] Cadastrar-se nos 5 a 8 programas de afiliados principais das ferramentas que serão abordadas nos primeiros vídeos e salvar os links em affiliate_catalog.json.  
-    
- \[ \] Configurar a página no Gumroad ou LemonSqueezy com o primeiro pacote de Blueprints/Templates gerado com apoio da JingJing.  
-    
- \[ \] Produzir e publicar a **Trinca Inicial de 5 Vídeos** focados 100% em buscas comparativas de fundo de funil:  
-    
  1. *n8n vs Make vs Zapier (2026): Real Cost Breakdown at 10k, 50k, and 250k Ops*  
  2.    
  3. *How to Replace \$350/mo in SaaS Subscriptions with 1 Docker VPS (\$12/mo Architecture)*  
  4.    
  5. *Supabase vs Self-Hosted PostgreSQL on Coolify: When Does Cloud Get Too Expensive?*  
  6.    
  7. *Local LLMs vs OpenAI/Anthropic APIs: The Real Hardware & Token Math for Small Businesses*  
  8.    
  9. *5 Open-Source Alternatives to Retool & Internal Tool Builders (2026 Comparison)*  
  10.    
### **Sprint 4: Ritmo de Cruzeiro e Otimização (Dias 31 a 90)**  
- \[ \] Manter cadência sustentável de **2 vídeos horizontais por semana** (6 a 8 minutos cada). Com a JingJing fazendo a pesquisa, o cálculo e o JSON inicial, seu trabalho humano será apenas **revisar o dossiê/roteiro por 15 minutos**, ajustar sua opinião técnica pessoal e aprovar a renderização.  
-    
- \[ \] A cada 15 dias, passar o CSV do YouTube Analytics para a JingJing analisar quais termos de busca estão gerando maior CTR e retenção, gerando a pauta dos próximos 4 vídeos com base nos vencedores.  
-    
## **7. Prompt de Inicialização para a JingJing (Hermes Agent)**  
Você pode copiar e salvar o bloco abaixo como o arquivo SKILL.md ou instrução de projeto da JingJing no seu MacBook Pro para iniciar os trabalhos imediatamente:  
 # Project Role: Lead Solutions Architect & Executive Producer (YouTube B2B Engine)  
   
\## Mission  
You are responsible for researching, fact-checking, scripting, and orchestrating the production of high-retention B2B technical videos in English (\`en-US\`) focused on Automation Architecture, SaaS Cost Engineering, and AI Workflows.  
   
\## Core Operating Rules  
1\. \*\*Zero Hallucination Policy:\*\* Never rely on training memory for SaaS pricing, rate limits, or API tiers. Always scrape the live \`/pricing\` and \`/docs\` pages first and save the raw facts to \`01_research_dossier.md\`.  
2\. \*\*Real Math Over Hype:\*\* Every video must include concrete cost simulations across at least 3 scale tiers (Starter, Growth, Scale) calculated via Python before writing the script.  
3\. \*\*Anti-Fluff Scripting:\*\* Never start a script with generic introductions ("In today's fast-paced digital world..."). Start at second 0 with a hard number, a pricing trap, or an architectural bottleneck.  
4\. \*\*Dual-Layer Output (\`02_video_spec.json\`):\*\*  
   - \`spoken_text\`: Must be written in natural American English, applying phonetic normalization for all acronyms, symbols, and currency values so the local TTS engine reads them flawlessly.  
   - \`visual_component\` & \`component_props\`: Every scene must map to one of the 6 Remotion React components (\`ArchitectureFlow\`, \`PricingCurveChart\`, \`SpecComparisonTable\`, \`BottleneckCallout\`, \`BlueprintPromoBanner\`, \`VerdictMatrix\`) with structured data.  
5\. \*\*Companion Asset Generation:\*\* For every video, generate a practical boilerplate file in \`05_lead_magnet/\` (e.g., \`docker-compose.yml\`, JSON workflow template, or cost calculator) and write the YouTube description with timestamps and mapped affiliate links.  
###    
### **Como Usaremos Este Guia a Partir de Agora?**  
Sempre que você quiser dar o próximo passo comigo (Gemini) ou com a JingJing, basta se referir à etapa deste documento.  
   
Qual frente você quer atacar primeiro agora: **desenhar o código dos componentes visuais no Remotion**, **estruturar o script Python que conecta a JingJing ao TTS + Whisper**, ou **detalhar o primeiro vídeo piloto do zero**?  
   
