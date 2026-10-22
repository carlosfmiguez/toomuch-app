# Cadastrar um produto do zero até o catálogo

Tudo acontece na **ficha do produto** (menu **Produtos** → clique no produto). No topo ela mostra as 6 etapas,
qual é a próxima e um botão **Ir para lá**. Custo e preço sugerido são recalculados sozinhos sempre que algo
que muda o custo é salvo — não existe mais botão "Recalcular" obrigatório.

## Antes de começar (uma vez só)

- **Filamento com custo**: **Estoque → Compras → Adicionar** (filamento, gramas, valor). O custo por grama vem da compra.
- **Embalagem** (opcional): **Estoque → Componentes** com "é embalagem" marcado, ou o *custo padrão de embalagem* em **Parâmetros globais**.

## O caminho

1. **Novo produto** (botão em Produtos): nome, categoria e origem do design. O SKU-base é gerado se ficar em branco.
   O produto nasce como rascunho com uma peça chamada "Peça única" e a ficha abre na etapa 2.
2. **Peças e 3MF fatiado**: renomeie a peça se quiser e, na própria linha, escolha o `.gcode.3mf`
   (Bambu Studio → Arquivo → Exportar → Exportar arquivo fatiado da placa) → **Enviar 3MF**. A linha mostra gramas,
   tempo e a miniatura da placa. Produto com várias peças impressas separadas: **Outra peça**.
3. **Variante**: tamanho, cor e filamento → **Criar variante e calcular custo**. O SKU sai sozinho
   (`SKU-base-TAMANHO-COR`) e a composição entra com as peças do produto. Minutos de mão de obra, componentes
   (kit elétrico, caixa) e quantidade de peças ficam em **Editar** na variante.
4. **Aprovar preço**: no cartão da variante, cada canal mostra o sugerido, o campo de preço de venda e a margem,
   que muda enquanto você digita. Clique no sugerido para usá-lo. **Aprovar** (um canal) ou **Aprovar todos**.
   Todos os preços pendentes de todos os produtos também aparecem em **Aprovar preços** no menu.
5. **Fotos**: escolha uma ou várias e **Enviar fotos**. A primeira vira a principal; a estrela troca.
6. **Publicar**: **Ativar produto**. Com tudo verde ele entra no catálogo em PDF, na loja e no envio ao Tiny.

O admin continua disponível em **Cadastros avançados** e no link "Abrir no modo avançado" da ficha; ao salvar lá,
o custo é recalculado e você volta para a ficha.

## Produzir (quando vender)

**Produção → Nova ordem**: variante, quantidade, prazo. Na fila, **Iniciar** e **Concluir** baixam o filamento;
**Falhou?** lança refugo e reabre a impressão.
