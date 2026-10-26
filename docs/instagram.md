# Instagram da Triangula

Todo dia, depois da edição da manhã, o workflow **Instagram**
(`.github/workflows/instagram.yml`) faz duas coisas sozinho:

1. gera as imagens e as legendas do dia a partir do JSON da edição;
2. publica tudo pela API oficial do Instagram, cada post no seu horário.

Ninguém precisa abrir o Instagram. A configuração abaixo é feita **uma vez**.

## O que sai e quando

| Horário (Brasília) | Formato | Conteúdo |
|---|---|---|
| 07:00 seg–sex · 08:30 sáb–dom | Carrossel de 8 slides | **Em 1 minuto**: capa com a manchete, 5 fatos do dia (com a editoria e as fontes), mercado e fechamento |
| 07:10 seg–sex · 08:40 sáb–dom | 4 stories | Bom dia + clima, manchete, mercado e uma chamada para o carrossel |
| 18:30 seg–sex | Carrossel de 7 slides | **Raio-X da cobertura**: o assunto em que a imprensa mais se dividiu (lado A, lado B, o espectro dos veículos, a conclusão e por que importa) |
| Dias sem IA | 1 imagem | **Terminal da manhã**: os 13 indicadores, no lugar do "Em 1 minuto" (sem Raio-X) |

Os horários e prazos ficam em `config/site.yaml`, no bloco `instagram`.

- Cada publicação sai na primeira execução depois do horário. Logo depois da edição, o workflow espera até 75 min pelo horário-alvo. Mais tarde, o agendamento de hora em hora cuida do resto.
- Se o horário passar do prazo (`expires`, 13:00 de manhã e 22:30 à noite), a publicação não sai. Notícia velha não vai ao ar.
- Entre dois posts de feed há sempre pelo menos 3 h. Stories não contam.
- **Período eleitoral** (`election_until`, hoje até 27/10/2026): pesquisa de intenção de voto não sai, o Raio-X não trata de política e o "Em 1 minuto" tem no máximo um item de política.

Todo texto vem do JSON da edição; nenhuma IA reescreve legenda. Cada slide leva texto alternativo e cada legenda leva quatro hashtags. O design é só tipografia e dados: nada de foto de jornal.

## Configuração (uma vez, ~15 min)

### 1. Conta do Instagram

No celular, no perfil da Triangula:

1. Abra **Configurações** › **Tipo de conta e ferramentas** › **Mudar para conta profissional** › **Empresa**.
2. Escolha a categoria **Site de notícias e mídia**.
3. Ative a autenticação em dois fatores.

O nome, a bio, a foto e os destaques sugeridos estão no [kit do perfil](#kit-do-perfil).

### 2. App na Meta

1. Em [developers.facebook.com](https://developers.facebook.com/), entre com a sua conta do Facebook e conclua o cadastro de desenvolvedor (confirmação por telefone).
2. Clique em **Criar app**.
   - Caso de uso: **Gerenciar mensagens e conteúdo no Instagram**. Se pedir o tipo, escolha **Empresa**.
   - Nome: `Triangula Publisher`. A Meta não aceita "Instagram", "Insta" ou "IG" no nome.
3. Em **Funções do app** › **Funções** › **Adicionar pessoas**, adicione o @ da Triangula como **Testador do Instagram**.
4. Aceite o convite em [instagram.com](https://www.instagram.com/) › **Configurações** › **Apps e sites** › **Convites de testador**. É o passo mais esquecido: sem ele o token não funciona.
5. De volta ao painel do app, abra **Instagram** › **Configuração da API com login do Instagram** › **Gerar tokens de acesso** › **Adicionar conta**, entre no Instagram e autorize.
6. Copie o **token** e o **ID da conta** que aparecem. O token vale 60 dias, e o próprio workflow o renova toda semana (passo 3).

### 3. Token do GitHub para a renovação automática

O token do Instagram vence em 60 dias se não for renovado. O workflow renova toda semana e guarda o novo no segredo `IG_ACCESS_TOKEN`. Para isso ele precisa de um token do GitHub com permissão de gravar segredos.

1. Abra [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new).
2. Preencha:
   - **Token name**: `triangula-segredos`
   - **Expiration**: *No expiration*
   - **Repository access**: *Only select repositories* › `qi-journal`
   - **Permissions** › **Repository permissions** › **Secrets**: *Read and write*. Nada mais.
3. Clique em **Generate token** e copie.

### 4. Segredos no GitHub

Em **github.com/pbcvphyton/qi-journal** › **Settings** › **Secrets and variables** › **Actions** › **New repository secret**, crie:

| Segredo | Valor |
|---|---|
| `IG_ACCESS_TOKEN` | o token do passo 2.6 |
| `IG_USER_ID` | o ID da conta do passo 2.6 |
| `GH_SECRETS_TOKEN` | o token do passo 3 |

Na aba **Variables** (opcional):

| Variável | Valor |
|---|---|
| `IG_API_VERSION` | versão da API indicada no painel da Meta, por exemplo `v25.0` (padrão: `v23.0`) |
| `IG_PAUSE` | `true` para pausar as publicações sem mexer em nada mais |

### 5. Primeiro teste

1. Abra **Actions** › **Instagram** › **Run workflow**.
2. Veja o passo **Publicar no Instagram**. O log mostra `conectado como @…` e o que foi publicado.
3. Abra o perfil numa janela anônima, sem login. Se o post **não** aparecer para quem está deslogado:
   - preencha no painel do app a URL de política de privacidade, por exemplo a página inicial do site;
   - mude o app de **Desenvolvimento** para **Ao vivo**. O Standard Access não exige revisão da Meta.

Depois disso, tudo roda sozinho.

## Quando algo exige você

O workflow abre uma issue **"Instagram: ação necessária"** no repositório, e o GitHub manda e-mail. Isso acontece quando:

- o token expirou ou foi revogado, por exemplo se a senha do Instagram mudou. Refaça os passos 2.5–2.6 e atualize o segredo `IG_ACCESS_TOKEN`;
- o token vai vencer em menos de 14 dias. Sinal de que a renovação automática não está funcionando: confira o segredo `GH_SECRETS_TOKEN`.

A issue é fechada sozinha quando o Instagram volta a publicar.

## Como funciona por dentro

- `python -m qijournal instagram gerar` lê a edição do dia (`data/AAAA-MM-DD.json`) e renderiza os slides. Os templates ficam em `qijournal/instagram/templates`, e a renderização usa o Chromium do Playwright com as fontes de `assets/fonts`. A fila é montada em `fila.json`.
- As imagens e a fila ficam na branch **`instagram`**, reescrita a cada execução só com os últimos 7 dias, o que não aumenta o histórico da `main`. A API do Instagram baixa as imagens de `raw.githubusercontent.com`. O nome de cada imagem leva o hash do conteúdo, então uma imagem refeita nunca reaproveita cache.
- `python -m qijournal instagram publicar` publica o que venceu, com estas proteções contra post em dobro:
  - o container é gravado na branch antes do `media_publish`;
  - a publicação nunca é repetida às cegas;
  - a primeira linha da legenda é conferida nas publicações recentes;
  - a cota de 24 h é verificada antes de publicar.
- Sem os segredos, as imagens são geradas mesmo assim (dá para ver na branch `instagram`), mas nada é publicado.

Para gerar localmente (sem publicar):

```bash
pip install -r requirements.txt -r requirements-instagram.txt
python -m playwright install chromium
python -m qijournal instagram gerar --dir . --media-dir /tmp/instagram --force
```

## Kit do perfil

- **Nome** (aparece na busca): `Triangula | Notícias e Mercado`
- **@**, nesta ordem de preferência: `@triangula`, `@triangula.jornal`, `@triangulajornal`, `@triangula.br`
- **Categoria**: Site de notícias e mídia. Sem botões de contato: ninguém atende.
- **Bio**: Seu terminal financeiro diário 🔺 O essencial da manhã em 1 minuto e como cada jornal contou a mesma notícia. Resumo por IA, com fontes ↓
- **Link**: https://pbcvphyton.github.io/qi-journal/ (sempre a edição de hoje)
- **Foto do perfil**: `assets/instagram/avatar.png`
- **Destaques** (capas em `assets/instagram/destaques/`): Comece aqui · Raio-X · Método · Terminal · Editorias · Correções
- **Moderação**, ligada uma vez no app:
  - "Ocultar comentários ofensivos" e "Palavras ocultas";
  - resposta automática de DM: "Conta automática, não lemos mensagens. A edição de hoje está no link da bio."

Fixar posts e montar os destaques são as duas únicas coisas que a API não faz. São feitas à mão, uma vez.
