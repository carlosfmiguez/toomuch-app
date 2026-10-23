# Cadastrar um produto do zero até o catálogo

Tudo acontece na **ficha do produto** (menu **Produtos** → clique no produto), que funciona como um assistente:
o painel de cima mostra as 6 etapas e a tela mostra só a etapa atual. **Avançar** e **Voltar** (no rodapé) ou um
clique no painel trocam de etapa; ao abrir a ficha ela já cai na próxima etapa pendente. Custo e preço sugerido são recalculados sozinhos sempre que algo
que muda o custo é salvo — não existe mais botão "Recalcular" obrigatório.

## Antes de começar (uma vez só)

- **Filamento com custo**: **Estoque → Compras → Adicionar** (filamento, gramas, valor). O custo por grama vem da compra.
- **Embalagem** (opcional): **Estoque → Componentes** com "é embalagem" marcado, ou o *custo padrão de embalagem* em **Parâmetros globais**.

## O caminho

1. **Novo produto** (botão em Produtos): nome, categoria e origem do design. O SKU-base é gerado se ficar em branco.
   O produto nasce como rascunho com uma peça chamada "Peça única" e o assistente abre na etapa 1 (dados:
   descrição curta, medidas, licença) → **Salvar e avançar**.
2. **Peças e 3MF fatiado**: renomeie a peça se quiser e, na própria linha, escolha o `.gcode.3mf`
   (Bambu Studio → Arquivo → Exportar → Exportar arquivo fatiado da placa) → **Enviar 3MF**. A linha mostra gramas,
   tempo e a miniatura da placa. Produto com várias peças impressas separadas: **Outra peça**.
   Peça que já existe em outro produto (uma base, os pés): **Usar peça já cadastrada** → **Compartilhar** (a mesma
   peça e o mesmo 3MF nos dois produtos; trocar o arquivo vale para ambos) ou **Copiar** (cópia independente, já
   com o 3MF). Um 3MF que subiu solto para a biblioteca (importação em lote) entra com **Usar como peça**.
3. **Variante**: tamanho, cor e filamento → **Criar variante e calcular custo**. O SKU sai sozinho
   (`SKU-base-TAMANHO-COR`) e a composição entra com as peças do produto. Minutos de mão de obra, componentes
   (kit elétrico, caixa) e quantidade de peças ficam em **Editar** na variante.
4. **Aprovar preço**: no cartão da variante, cada canal mostra o sugerido, o campo de preço de venda e a margem,
   que muda enquanto você digita. Clique no sugerido para usá-lo. **Aprovar** (um canal) ou **Aprovar todos**.
   Com todos aprovados, o assistente segue sozinho para Fotos. Os preços pendentes de todos os produtos também
   aparecem em **Aprovar preços** no menu.
5. **Fotos**: escolha uma ou várias e **Enviar fotos**. A primeira vira a principal; a estrela troca.
6. **Publicar**: **Ativar produto**. Com tudo verde ele entra no catálogo em PDF, na loja e no envio ao Tiny.

### 3MF com várias placas

Quando o 3MF fatiado tem mais de uma placa, o sistema pergunta o que elas são (a etapa de peças fica
pendente até responder; até lá o custo soma as placas):

- **Tamanhos diferentes do mesmo objeto** (ex.: placas "100%" e "70%"): cada placa vira uma peça com o
  custo só daquela placa e "só nos tamanhos" daquele tamanho, e cada tamanho vira uma variante (copiando
  filamentos e componentes da variante que já existe, ou com o filamento escolhido).
- **Partes do mesmo objeto** (ex.: corpo numa placa, asas na outra): a peça continua uma só e o custo
  soma as placas.

### Peça sem 3MF: estimativa e tamanhos

Em **Estimativa sem 3MF e tamanhos**, na linha da peça: gramas e tempo estimados (o custo usa até subir o
3MF) e "só nos tamanhos" (ex.: G ou P, M) para a peça entrar só nas variantes daquele tamanho.

### Coleções (linha de design)

A coleção agrupa peças de funções e categorias diferentes que dividem o mesmo desenho, por exemplo a
**Linha Taylor**: vaso, porta-maquiagem e luminária. Uma peça pode estar em mais de uma coleção
(Linha Taylor e Natal 2026).

- **Na ficha, etapa Dados**: marque as coleções do produto ou digite o nome em "nova coleção" para criar
  e já incluir. Separe várias por vírgula.
- **No menu Coleções**: nome, descrição (aparece no portal), "mostrar no portal", ordem e a lista de produtos,
  para montar a coleção inteira de uma vez.
- **Filtro**: em Produtos, "Todas as coleções".
- **Portal**: cada coleção visível tem página própria (`/loja/colecao/linha-taylor/`) e aparece em "Coleções"
  na home. A página da categoria ganha filtro por coleção, e a página do produto mostra "Da coleção…" com as
  outras peças da linha. Desmarque "mostrar no portal" para usar a coleção só como agrupador interno.
- **Importação em lote**: coluna opcional `Coleção` no `produtos.csv` (várias separadas por `|`).

O admin continua disponível em **Cadastros avançados** e no link "Abrir no modo avançado" da ficha; ao salvar lá,
o custo é recalculado e você volta para a ficha.

## Produzir (quando vender)

**Produção → Nova ordem**: variante, quantidade, prazo. Na fila, **Iniciar** e **Concluir** baixam o filamento;
**Falhou?** lança refugo e reabre a impressão.
