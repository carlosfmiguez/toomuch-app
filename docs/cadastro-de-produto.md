# Cadastrar um produto do zero até o catálogo — exemplo: Sagrada Família

Sistema Toomuch (app.toomuch.art / toomuch-app.onrender.com). Caminho completo: fatiar → cadastrar → 3MF na peça → composição → custo → preço aprovado → foto e status Ativo → catálogo e loja. Leva uns 15 minutos na primeira vez.

## Antes de começar (uma vez só)

**A. Filamento com custo.** O custo do filamento vem da compra, não do cadastro do rolo.
Menu **Estoque → Compras → Adicionar**: filamento *Bambulab PLA LITE Branca*, quantidade `1` (rolo), gramas no total `1000`, valor dos itens (ex.: `89,90`), frete rateado (se houve), data → **Salvar**. A compra entra no estoque na hora e o custo médio por grama é atualizado; o alerta "abaixo do mínimo" some quando o saldo passa do mínimo cadastrado no filamento.

**B. Embalagem (opcional, mas muda o custo).** Menu **Estoque → Componentes → Adicionar**: nome `Caixa + papel de seda + tag`, custo unitário (ex.: `4,50`), marcar **é embalagem**. Alternativa rápida: em **Custo e preço → Parâmetros globais** preencha *custo padrão de embalagem*, que vale para todos os produtos sem componente de embalagem.

## 1. Fatiar no Bambu Studio e exportar o 3MF fatiado

1. Abra o STL da Sagrada Família, fatie para a **A1** com o perfil que vai usar de verdade (altura de camada, preenchimento, suportes).
2. **Arquivo → Exportar → Exportar arquivo fatiado da placa** (*Export plate sliced file*) → gera `sagrada-familia.gcode.3mf`.
3. É esse arquivo que o sistema lê (gramas por filamento, tempo de impressão, imagem da placa). O "Salvar projeto" (.3mf de projeto) não serve para custo.
4. Se as figuras forem impressas juntas na mesma placa, é **uma peça** (ex.: "Conjunto"). Se imprimir cada figura separada, cadastre **uma peça por figura** (Maria, José, Menino Jesus), cada uma com seu 3MF.

## 2. Criar o produto

**Painel → Novo produto** (ou Cadastros → Produtos (admin) → +).

| Campo | Preencher |
| --- | --- |
| Nome | `Sagrada Família` |
| SKU-base | `TM-REL-SAG` (padrão TM-CATEGORIA-MODELO) |
| Categoria | Decoração religiosa |
| Status | Rascunho (vira Ativo no passo 7) |
| Descrição curta | 1 frase para o catálogo e a loja |
| Descrição longa, tags SEO | opcional agora |
| Medidas e prazo | altura, largura, profundidade (mm), peso (g), prazo em dias úteis |
| Origem do design | **Autoral** se o modelo é seu. Se for de terceiros (Thangs, Cults…): *Terceiros — licença comercial* + autor, link, termos e custo de licença por unidade, se houver. *Sem licença de venda* bloqueia o catálogo. |

Mais abaixo, na mesma tela:

- **Peças impressas** → *Adicionar outra*: nome `Conjunto`, quantidade por produto `1`, slot *Cor principal*.
- **Variantes** → *Adicionar outra*: SKU `TM-REL-SAG-U-BRC`, tamanho `Único`, cor principal *Branco*, filamento do slot principal *Bambulab PLA LITE Branca*, marcar *variante padrão*.
- **Fotos, renders e PDFs do produto**: pode deixar para o passo 7.

Clique em **Salvar e continuar editando**. Ao salvar, a variante ganha automaticamente a **composição** com as peças do produto (o sistema avisa no topo).

## 3. Subir o 3MF fatiado da peça

Ainda no produto, na seção **3MF fatiado por peça** (logo abaixo de Peças impressas) → *Adicionar*:

1. **Peça**: *Conjunto* (a peça só aparece aqui depois do produto salvo).
2. Tipo **3MF fatiado (.gcode.3mf)** (já vem marcado), **arquivo** = o exportado no passo 1, impressora-alvo *A1 #1* (ou em branco = qualquer), **arquivo de produção** ✓ (já vem marcado) → **Salvar e continuar editando**.
3. Ao salvar, a linha mostra **gramas** e **tempo** lidos do arquivo (ex.: 85,20 g / 312 min). Se aparecer *erro de leitura*, o arquivo não é um 3MF fatiado — exporte de novo pelo caminho do passo 1.

O mesmo pode ser feito pelo cadastro da peça (link *abrir cadastro completo →* na linha da peça, ou menu **Cadastros → Peças impressas**), na seção *Arquivos da peça*.

## 4. Conferir a composição (BOM) e os minutos da variante

A composição já foi criada no passo 2 (uma linha por peça). Só abra a variante (link *abrir cadastro completo →* na linha da variante, ou **Cadastros → Variantes**) se precisar de:

- **Embalagem ou componentes comprados**: em *Itens de composição*, linha tipo *Componente comprado*, componente *Caixa + papel de seda + tag*, quantidade `1`.
- **Minutos** diferentes do padrão: pós-processamento `10` (tirar suporte, rebarbas), montagem `0`, embalagem `5`.
- **Salvar**.

## 5. Calcular o custo

Menu **Produtos → Sagrada Família** (a ficha) → no cartão da variante, **Recalcular**.

O cartão passa a mostrar o **custo unitário** com a abertura (filamento com desperdício de 2 %, hora-máquina, mão de obra a R$ 30/h, componentes, embalagem, licença, provisão de falha de 5 %) e, ao lado, o **preço sugerido por canal** com a margem resultante.

Exemplo ilustrativo (90 g de PLA a R$ 95/kg, 5 h na A1, 15 min de mão de obra, sem embalagem): filamento R$ 8,72 · máquina R$ 4,35 (R$ 0,87/h) · mão de obra R$ 7,50 · falha R$ 0,65 → **custo R$ 21,22** → sugerido **Site R$ 34,90**, **Mercado Livre R$ 40,90**, **Shopee R$ 54,90** (margem líquida ≈ 30 % em cada, já descontados taxa do canal e Simples).

Se aparecer um aviso no cartão, resolva e recalcule:

| Aviso | O que fazer |
| --- | --- |
| Peça sem 3MF fatiado de produção | passo 3 (tipo 3MF fatiado, peça escolhida, *arquivo de produção* marcado) |
| Filamento sem custo médio | passo A (registrar a compra) |
| Nenhum filamento mapeado para o slot | passo 2, campo *filamento do slot principal* na variante |
| A variante não tem itens de composição | passo 4 |

## 6. Aprovar o preço de venda

Na ficha, clique em **Aprovar ou sobrescrever preços** (ou menu **Custo e preço → Preços por canal**, filtrando pela variante).

- Para aceitar o sugerido: marque as linhas dos 3 canais → ação **Aprovar o preço sugerido como vigente** → *Executar*.
- Para colocar o seu preço: abra a linha, altere **preço vigente** (ex.: `39,90`), salve, e use a ação **Aprovar o preço vigente informado**. O sistema recalcula a margem; abaixo da margem mínima (20 %) ele avisa na ficha, no painel e na lista de preço.

## 7. Foto e status Ativo

De volta ao produto (**Editar** na ficha):

- **Fotos, renders e PDFs do produto** → *Adicionar*: tipo *Foto*, arquivo, marcar **foto principal** (é a que vai para o catálogo e a loja). Pelo menos 1 foto.
- **Status**: *Ativo* → **Salvar**.

Na ficha, o cartão **Pronto para o catálogo?** deve ficar todo verde: 3MF em todas as peças · composição completa · fotos · licença válida · preço aprovado → etiqueta **pronto**.

## 8. Catálogo e loja

- Menu **Catálogo**: canal *Site*, "só os prontos" → a Sagrada Família aparece na lista com *A partir de R$ …* → **Baixar PDF** (capa, sumário e a página do produto). Com canal *Mercado Livre* ou *Shopee* o PDF usa o preço daquele canal, e os botões **Mercado Livre / Shopee** geram a planilha de anúncio.
- **Loja pública** (`/loja/`): o produto entra em *Decoração religiosa* com o preço do canal Site e o botão de WhatsApp com mensagem pronta.
- Com o Tiny conectado (menu **Tiny ERP → Enviar produtos**), a variante é criada lá pelo SKU com o preço do canal escolhido.

## 9. Produzir (quando vender)

Menu **Produção → Nova ordem**: variante `TM-REL-SAG-U-BRC`, quantidade, prioridade, prazo → salvar. A ordem vira trabalhos de impressão por peça; na fila, **Iniciar** e **Concluir** (pesagem opcional) baixam o filamento do estoque; **Falhou?** lança refugo e reabre a impressão.

## Atalhos

- Muitos produtos de uma vez: **Importar em lote** (ZIP com uma pasta por modelo, STL/3MF e fotos dentro) cria os rascunhos com os arquivos anexados; depois é só cumprir os passos 3 a 7 em cada um.
- Orçar antes de cadastrar: **Custo e preço → Calculadora rápida** (gramas, horas, filamento R$/kg) dá custo e preço por canal na hora.
