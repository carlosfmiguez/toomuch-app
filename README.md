# Sistema Toomuch

Web app de produtos, custo, filamento, produção e catálogo da Toomuch Art, construído a partir da
especificação *Sistema Toomuch — Guia de Referência e Especificação* (20/10/2026). Django 5 + Postgres
(SQLite para desenvolver), back-office sobre o [django-unfold](https://unfoldadmin.com) (menu lateral por
área, busca global ⌃K, menu do usuário, modo escuro) com a identidade da Toomuch, e telas próprias para
painel, produtos, produção, lista de preço, calculadora, catálogo, importação e Tiny — todas dentro do
mesmo shell.

## O que já existe (etapas E0 a E3 do roadmap, mais parte da E5)

| Módulo | Estado |
| --- | --- |
| Biblioteca de arquivos | `Asset` com tipo, versão, hash SHA-256, impressora-alvo, origem/licença no produto; leitura automática de **3MF fatiado** (gramas por filamento, tempo, placas, thumbnail), 3MF de projeto (dimensões) e STL (dimensões, volume, peso estimado); thumbnail de fotos |
| Produtos e variantes | `Product` (origem e licença obrigatórias, checklist de pronto para catálogo), `Variant` (SKU, tamanho, cores, mapeamento slot → filamento, minutos por etapa), `Part` (peça impressa, um 3MF por impressora), `BOMItem`, `Component` |
| Inventário | `FilamentSKU` (marca, fabricante, cor oficial, custo médio ponderado, saldo), `Spool` opcional, `Purchase` (lança no estoque e recalcula o custo médio), `StockMovement` imutável |
| Custo e preço | Motor puro em `apps/costing/engine.py` com as fórmulas da especificação; `GlobalParams` e `ChannelFee` com vigência; `PriceByChannel` com histórico, aprovação e alerta de margem mínima; **calculadora rápida** e **lista de preço** |
| Produção | `ProductionOrder` explode em `PrintJob` por peça × quantidade; concluir baixa o filamento (consumo ou refugo) e abre reimpressão em caso de falha |
| Publicação | **Catálogo PDF** (capa, sumário e uma página A4 por produto, no modelo visual da marca, com WeasyPrint), prévia em HTML, página individual por produto; **planilhas de anúncios** para Mercado Livre e Shopee (CSV ou XLSX) com título dentro do limite, descrição estruturada, preço do canal, medidas e fotos; `Publication` registra o que foi enviado |
| Dados iniciais | `seed_base` cria parâmetros (energia 0,95; mão de obra 30; desperdício 2 %; falha 5 %; Simples 4 %), impressoras a R$ 3.000, canais Site/ML/Shopee e cores; `seed_demo` cria a Solaris de exemplo |

| Loja pública | `/loja/` sem login: home por categoria, página de categoria e página de produto no modelo desenhado (a luminária acende ao passar o mouse, swatches de cor, botão de WhatsApp com mensagem pronta); só produtos com checklist completo e preço do canal Site |
| Importação em lote | Tela **Produtos → Importar em lote** (ZIP ou pasta no servidor) com revisão de nome, categoria, SKU e origem antes de criar; ou `manage.py import_folder` com planilha de mapeamento XLSX; idempotente por hash |

| Interface | Shell do **django-unfold** com cores da marca (`config/unfold.py`): sidebar em grupos (Operação, Custo e preço, Estoque, Cadastros, Integrações, Administração) com contadores de pendências e impressões, busca global, avatar e menu do usuário, modo claro/escuro, login com foto de produto; painel com KPIs e alertas em `templates/admin/index.html`; telas próprias estendem `templates/shell.html`; CSS em `static/css/toomuch-admin.css`; traduções do shell em `locale/pt_BR` |
| Tiny ERP (E7) | Tela **Tiny** no menu: cadastro do aplicativo (Client ID/Secret), conexão OAuth 2 com renovação automática do token (4 h) e do refresh (1 dia), **envio de produtos** prontos para catálogo (cria ou atualiza pelo SKU, com preço do canal escolhido, medidas, peso, fotos e estoque pronto; só reenvia o que mudou) e **importação de pedidos** nas situações configuradas (padrão 3 = Aprovada) virando ordens de produção, uma vez por pedido; `manage.py tiny_sync` para agendar |

Ainda não existe: Bambu ao vivo e relatórios (E8), perfis de permissão.

## Rodar em desenvolvimento

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # opcional; sem .env usa SQLite
python manage.py migrate
python manage.py seed_demo      # parâmetros, impressoras, canais e a Solaris de exemplo
python manage.py createsuperuser
python manage.py runserver
```

Abra http://127.0.0.1:8000/ — a raiz leva ao painel (`/admin/`), que é a home do sistema.

Traduções do shell: edite `locale/pt_BR/LC_MESSAGES/django.po` e rode `python scripts/compile_messages.py`
(o Docker e o CI já fazem isso no build; o `.mo` não vai para o git).

## Deploy no Render (push → deploy automático)

O `render.yaml` é um *Blueprint*: cria o serviço web (Docker), o Postgres, o disco dos uploads e as variáveis.

1. [dashboard.render.com](https://dashboard.render.com) → **New → Blueprint** → conecte o GitHub e escolha `toomuch-app`.
2. O Render lê o `render.yaml` e pede só três valores: e-mail e senha do admin e o WhatsApp (DDI+DDD+número).
   `SECRET_KEY` é gerada automaticamente. Clique em **Apply**.
3. Primeiro deploy (3–5 min): o start roda migrações, dados iniciais e cria o admin. Teste em
   `https://toomuch-app.onrender.com/healthz` e `/admin/`.
4. Serviço web → **Settings → Custom Domains** → `app.toomuch.art` e crie o CNAME indicado no DNS do toomuch.art.

Custo aproximado: US$ 7 (web) + US$ 6 (Postgres) + US$ 1,25 (disco de 5 GB) ≈ US$ 14/mês. Para rodar um
comando no ambiente: serviço web → **Shell** (no painel) → `python manage.py seed_demo`.

## Deploy no Railway (push → deploy automático)

1. Crie o repositório no GitHub e conecte-o em [railway.app](https://railway.app): *New Project → Deploy from
   GitHub repo*. O `railway.toml` já define build pelo Dockerfile, o comando de início (`scripts/start.sh`:
   migrações, dados iniciais e admin a cada deploy) e o healthcheck em `/healthz`.
2. No projeto, *+ New → Database → PostgreSQL*. No serviço web, *Variables*:

   | Variável | Valor |
   | --- | --- |
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (referência ao serviço Postgres) |
   | `SECRET_KEY` | uma chave longa e aleatória |
   | `DEBUG` | `False` |
   | `ALLOWED_HOSTS` | `app.toomuch.art` (o domínio `*.up.railway.app` entra sozinho) |
   | `CSRF_TRUSTED_ORIGINS` | `https://app.toomuch.art` |
   | `DJANGO_SUPERUSER_USERNAME` / `EMAIL` / `PASSWORD` | o primeiro admin (criado no primeiro deploy) |
   | `TOOMUCH_WHATSAPP` | número com DDI, só dígitos |
   | `MEDIA_ROOT` | `/app/media` |

3. *Settings → Volumes → Add volume* montado em `/app/media` (uploads). Alternativa: Cloudflare R2 com as
   variáveis `STORAGE_*`.
4. *Settings → Networking → Custom domain* → `app.toomuch.art` e crie o CNAME indicado no DNS do toomuch.art.
5. Primeiro acesso: `https://app.toomuch.art/admin/` com o usuário das variáveis. Rode `seed_demo` só se quiser a
   Solaris de exemplo (Actions → Operações, ou `railway run python manage.py seed_demo`).

Para o workflow *Operações* (rodar comandos no ambiente a partir do GitHub), cadastre os segredos
`RAILWAY_TOKEN` (Project → Settings → Tokens) e `RAILWAY_SERVICE` (nome do serviço web) no repositório.

## Rodar com Docker

```bash
cp .env.example .env            # ajuste SECRET_KEY e DEBUG=False em produção
docker compose up --build
docker compose exec web python manage.py seed_base
docker compose exec web python manage.py createsuperuser
```

## Testes

```bash
python manage.py test
```

Os testes reproduzem o exemplo da especificação: Luminária Solaris G custa **R$ 72,19** e sai a
**R$ 118,90** no site, **R$ 165,90** na Shopee e **R$ 177,90** no Mercado Livre com margem líquida de 30 %.

## Como o custo é calculado

```
C_fil   = Σ gramas × (1 + desperdício) × custo médio por grama
c_h     = preço ÷ vida útil + manutenção/h + (W ÷ 1000) × tarifa      (2 casas)
C_maq   = Σ horas × c_h
C_mdo   = minutos ÷ 60 × custo/hora
C_total = C_fil + C_maq + C_mdo + componentes + embalagem + licença + falha × (C_fil + C_maq)
Preço   = (C_total + fixo + frete subsidiado) ÷ (1 − taxa − imposto − margem-alvo), arredondado para ,90
```

As gramas e o tempo de cada peça vêm do `.gcode.3mf` exportado pelo Bambu Studio ou OrcaSlicer
(`Metadata/slice_info.config`). Suba o arquivo na peça como *3MF fatiado*, marque *arquivo de produção*
e escolha a impressora para a qual foi fatiado; a leitura acontece no upload.

## Fluxo de uso no back-office

1. **Filamentos**: cadastre o SKU (marca, fabricante, tipo, cor oficial) e registre uma **compra** — o custo
   médio e o saldo vêm dela.
2. **Produto**: crie o produto, as **peças impressas** e suba o 3MF fatiado de cada peça.
3. **Variante**: SKU completo, cores, filamento de cada slot, minutos de pós/montagem/embalagem e a
   **composição** (peças, componentes, embalagem).
4. **Recalcular** (ação na variante ou botão na ficha do produto): custo e preços sugeridos por canal.
5. **Preços por canal**: aprove o sugerido ou informe o vigente.
6. **Produção**: abra uma ordem (explode em trabalhos), inicie e conclua cada trabalho — o filamento é
   baixado automaticamente.
7. **Catálogo** (menu Catálogo): escolha o canal da lista de preço, baixe o PDF ou a planilha de anúncios.
   Entram os produtos ativos com o checklist completo; "incluir todos" mostra também os incompletos.
8. **Loja** (`/loja/`): os mesmos produtos prontos, públicos. Configure `TOOMUCH_WHATSAPP` no `.env`
   (só dígitos com DDI) para o botão de pedido funcionar.

## Importar os modelos existentes

Organize uma pasta com uma subpasta por modelo (STL/3MF/STEP/F3D + fotos) — arquivos soltos na raiz são
agrupados pelo nome (`vaso-face.stl` + `vaso-face-1.jpg`). Depois:

```bash
python manage.py import_folder /caminho/modelos --mapear mapeamento.xlsx   # gera a planilha para revisar
# revise nome, categoria, SKU-base, origem/licença e a coluna "Importar?"
python manage.py import_folder /caminho/modelos --aplicar mapeamento.xlsx  # cria os produtos em rascunho
```

Ou pela tela **Produtos → Importar em lote**, enviando a pasta compactada em ZIP. Cada modelo vira um
produto em rascunho com uma variante "Único", uma peça por arquivo 3D, fotos com thumbnail e a leitura de
dimensões do STL. Falta então: fatiar e subir o 3MF de produção, mapear filamento, aprovar preço.

## Catálogo PDF

O PDF usa as fontes Fraunces e Manrope empacotadas em `static/fonts/` (licença SIL OFL, arquivos `OFL-*.txt`)
e o wordmark em `static/img/`. A geração usa o WeasyPrint, que precisa das bibliotecas Pango/Cairo no sistema
(já presentes na imagem Docker; no Mac, `brew install pango`). As fotos vêm do `thumbnail` de cada `Asset` de
tipo Foto (800 px), gerado no upload.

## Tiny ERP

1. No Tiny: *Configurações → Preferências gerais → Outras configurações → Aplicativos → + novo aplicativo*, com a
   URL de redirecionamento mostrada na tela **Tiny** do sistema (`https://<domínio>/integracoes/tiny/callback/`)
   e permissões de produtos, estoque e pedidos. Copie o Client ID e o Client Secret.
2. Na tela **Tiny**: cole as credenciais, escolha o canal cujo preço vai ao Tiny (Site) e clique em **Conectar ao Tiny**.
3. **Enviar produtos** manda as variantes prontas para catálogo; **Importar pedidos** cria ordens de produção para
   pedidos aprovados dos últimos N dias. Para rodar sozinho, agende `python manage.py tiny_sync --base-url https://app.toomuch.art`
   a cada 15 minutos (Render: *Cron Job*; Railway: serviço com cron).

Referência da API: https://api-docs.erp.olist.com (v3, OAuth 2 em accounts.tiny.com.br).

## Estrutura

```
config/            settings, urls
apps/catalog       produto, variante, peça, BOM, componente, arquivo (+ leitura no upload)
apps/inventory     filamento, carretel, compra, movimento (+ serviços de consumo e pesagem)
apps/costing       engine.py (fórmulas), parâmetros, impressoras, canais, preços, calculadora, lista
apps/production    ordem de produção, trabalho de impressão
apps/publishing    publicações
apps/files3d       leitores de 3MF/STL
apps/integrations/tiny  cliente da API v3, OAuth, envio de produtos, pedidos → ordens, tela e comando
apps/core          painel (dashboard.py), usuários no visual do Unfold, seeds, testes de fluxo
config/unfold.py   shell: cores, menu lateral, badges, login
templates/shell.html       base das telas próprias (sidebar + cabeçalho do Unfold)
templates/admin/index.html painel (substitui o índice do admin)
static/css/toomuch-admin.css  identidade visual sobre o Unfold (Fraunces + Manrope, componentes tm-*)
locale/pt_BR       traduções do shell (compiladas por scripts/compile_messages.py)
```

## Próximos passos (roadmap)

- E8: status ao vivo das Bambu (MQTT em modo LAN) e relatórios de margem e giro.
- Hospedagem em app.toomuch.art (VPS + Docker Compose + HTTPS) e backup diário.
