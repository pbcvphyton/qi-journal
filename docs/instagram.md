# Instagram da Triangula

Todo dia, depois da edição da manhã, o workflow **Instagram**
(`.github/workflows/instagram.yml`) faz duas coisas sozinho:

1. gera as imagens e as legendas do dia a partir do JSON da edição;
2. publica tudo pela API oficial do Instagram, cada post no seu horário.

Ninguém precisa abrir o Instagram. A configuração abaixo é feita **uma vez**.

## O que sai e quando

A especificação completa (princípios, regras de texto, cada formato) está em
[docs/instagram-onda1.md](instagram-onda1.md). Resumo: **3 posts de feed por dia, sempre na mesma
ordem de cor** (marinho, laranja, papel), cada dia uma linha do grid, mais os stories da manhã.

| Horário (Brasília) | Formato | Série | Substituto |
|---|---|---|---|
| 07:00 seg–sex · 08:30 sáb–dom | **Em 1 minuto**, carrossel de 8 em marinho: capa com o número da manchete, 5 fatos com o selo de cobertura, Mercado em linha e a pauta solitária do dia | `EM 1 MINUTO Nº 031` | **Terminal da manhã** (1 imagem com os indicadores) nos dias sem IA, se o Em 1 minuto não juntar 3 fatos ou se for reprovado |
| 07:10 seg–sex · 08:40 sáb–dom | **Stories da manhã**, até 4 telas: bom dia com o número vivo do dia e a agenda do que foi gerado, manchete, mercado com o acumulado e a chamada para o carrossel (sem clima) | — | — (reprovados, não saem) |
| 12:00 | **Número do dia**, carrossel de 3 com a capa laranja chapada: o número, a prova (gráfico) e como medimos | `NÚMERO DO DIA Nº 004` | o próximo passo da cadeia de escolha; no fim dela, o maior acumulado do mercado |
| 18:30 | **Raio-X**, carrossel de 8 em papel: o assunto em que a imprensa mais se dividiu, com o Relógio da notícia e o Placar do dia | `RAIO-X Nº 014` | **Placar do dia** (4 slides, mesma série) sem assunto elegível; **Mercado em linha** (1 imagem, sem número) com base < 10, sem IA ou com o Raio-X/Placar reprovado |
| uma vez | **Manifesto**, carrossel de 6 em laranja: "Mesmo fato, várias versões." | — | — |

Os horários e prazos ficam em `config/site.yaml`, no bloco `instagram` (`slots`: `manha`, `stories`,
`numero`, `noite`; os limiares das contas em `limiares`).

- Cada publicação sai na primeira execução depois do horário. Logo depois da edição, o workflow espera até 75 min pelo horário-alvo. Mais tarde, o agendamento de hora em hora cuida do resto.
- Se o horário passar do prazo (`expires`: 11:00 a manhã, 16:00 o Número, 22:30 a noite), a publicação não sai. Notícia velha não vai ao ar.
- Entre dois posts de feed há sempre pelo menos 3 h. Stories não contam.
- **Os três posts do dia nunca estampam a mesma matéria.** A escolha segue a ordem: primeiro a noite
  (a mais escassa), depois o Número (sem a manchete nem a matéria da noite, sem repetir um número da
  semana), depois a manhã (a capa não é a matéria da noite nem a do Número) e, por fim, os stories.
  Na fila, a ordem é manhã, stories, número, noite.
- **Séries numeradas.** O número de cada série (`Fila.series`, em `fila.json`) só avança quando o
  post é publicado: refazer o dia mantém o número, e um post que expirou não gasta número. O Placar do
  dia é da série do Raio-X; o Mercado em linha, o Terminal e os stories não têm número.
- **Lint antes de sair.** Toda peça passa por `redacao.lint` (palavras proibidas, verbos que julgam
  veículo, léxico eleitoral no período, limites de caracteres e texto que transbordou no render).
  Reprovada, cai para o substituto da tabela e o motivo vai para o log.
- **Período eleitoral** (`election_until`, até 27/10/2026): política e assunto eleitoral ficam fora
  do Raio-X, do Placar, do Número e da capa do Em 1 minuto (eleição de outro país no título também);
  no Em 1 minuto, no máximo um item de política, sempre o último. Sempre, fora do período também: o
  laranja e o gancho nunca levam nome de partido ou de político (`instagram.eleicao.nomes`), e nenhum
  veículo é rotulado com campo político.
- **Assinatura.** Toda arte leva "▲ @handle" com o `instagram.handle` de `config/site.yaml`; enquanto
  ele for `null`, sai "▲ triangula" e nada falha.

Todo texto vem do JSON da edição, de contas e de moldes; nenhuma IA nova escreve legenda. Cada slide
leva texto alternativo (com a frase do gráfico) e cada legenda leva 3 ou 4 hashtags. O design é só
tipografia e dados: o gráfico faz o papel da foto.

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

- `python -m qijournal instagram gerar` lê a edição do dia (`data/AAAA-MM-DD.json`) e os 90 dias
  anteriores de `data/` (séries do mercado, véspera, histórico do manifesto), monta a grade do dia
  (`gerar.build_pieces`, com os formatos de `qijournal/instagram/formatos/`) e renderiza cada peça
  antes de aprová-la no lint. Os templates ficam em `qijournal/instagram/templates`, e a renderização
  usa o Chromium do Playwright com as fontes de `assets/fonts`. Os posts do dia entram em `fila.json`
  com `Fila.replace_day`: refazer a edição tira da fila o que a geração nova não tem mais (menos o que
  já saiu ou tem container criado).
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
# um dia específico de data/ (prévias e testes; sem --now, vale o começo do dia):
python -m qijournal instagram gerar --dir . --media-dir /tmp/instagram --date 2026-10-21
```

## Kit do perfil

- **Nome** (aparece na busca): `Triangula | Notícias e Mercado`
- **@**, nesta ordem de preferência: `@triangula`, `@triangula.jornal`, `@triangulajornal`, `@triangula.br`
- **Categoria**: Site de notícias e mídia. Sem botões de contato: ninguém atende.
- **Bio**: MESMO FATO, VÁRIAS VERSÕES. ▲ ~1.500 notícias por dia (média), de dezenas de veículos: o fato, o mercado e como cada um contou. Por IA, com fontes ↓
- **Link**: https://pbcvphyton.github.io/qi-journal/ (sempre a edição de hoje)
- **Foto do perfil**: `assets/instagram/avatar.png`
- **Destaques** (capas em `assets/instagram/destaques/`): Comece aqui · Raio-X · Método
- **Fixado**: o Manifesto (sai uma vez, no primeiro dia com a conta conectada); "Como ler um Raio-X" e "Nossos números" chegam na Onda 2
- **Moderação**, ligada uma vez no app:
  - "Ocultar comentários ofensivos" e "Palavras ocultas";
  - resposta automática de DM: "Conta automática, não lemos mensagens. A edição de hoje está no link da bio."

Fixar posts e montar os destaques são as duas únicas coisas que a API não faz. São feitas à mão, uma vez.
