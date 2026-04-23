# Dashboard de Fechamento de FIIs — Setup

Guia de configuração da planilha do zero. Você tem dois caminhos:

- **Caminho A (rápido):** cole `Code.gs`, rode `setupPlanilha` — cria tudo automaticamente.
- **Caminho B (manual):** execute os passos abaixo para entender cada peça.

---

## 1. Criar a planilha e colar o script

1. Crie uma nova Planilha Google em branco.
2. Menu **Extensões → Apps Script**.
3. Apague o conteúdo de `Code.gs` e cole o conteúdo do arquivo `Code.gs` deste repositório.
4. Salve (💾).
5. Volte para a planilha e **recarregue a aba** do navegador. Um menu **🏢 Fechamento FIIs** aparecerá.

### Caminho A — automático

- Menu **🏢 Fechamento FIIs → 🛠️ Setup inicial (criar abas e validações)**.
- Autorize quando o Google pedir permissão.
- Pule para a seção **6. Gatilhos** e depois **7. Cadastro de responsáveis**.

### Caminho B — manual: siga as seções 2 a 7.

---

## 2. Criar as 3 abas

Renomeie/crie as abas exatamente com estes nomes (os emojis fazem parte do nome):

1. `🖥️ Dashboard`
2. `📊 Operacional`
3. `⚙️ Config & Logs`

---

## 3. Aba `📊 Operacional`

### 3.1 Cabeçalhos (linha 1)

| Col | Cabeçalho | Bloco |
|---|---|---|
| A | ID | Identificação (cinza escuro `#424242`) |
| B | Fundo (FII) | Identificação |
| C | Nome do Imóvel | Identificação |
| D | Data Base Fechamento | Identificação |
| E | Responsável (Admin) | Admin Fiduciária (azul escuro `#1a3a6c`) |
| F | Link Drive (Pasta/Laudo) | Admin Fiduciária |
| G | Status do Laudo | Admin Fiduciária |
| H | Data Recebimento Laudo | Admin Fiduciária |
| I | Responsável (Precificação) | Precificação (verde escuro `#1e5631`) |
| J | Valor Contábil Anterior | Precificação |
| K | Novo Valor (Laudo) | Precificação |
| L | Variação % | Precificação |
| M | Comentários/Justificativas | Precificação |
| N | Última Atualização | Auditoria (roxo escuro `#4a148c`) |
| O | Status Final | Auditoria |

Fonte da linha 1: branco, negrito, centralizado, altura 38.

### 3.2 Congelamento e formatos

- **Exibir → Congelar → 1 linha** e **3 colunas**.
- `D:D` e `H:H` → formato **Data** `dd/mm/aaaa`.
- `J:K` → formato **Moeda** `R$ #.##0,00`.
- `L:L` → formato **Porcentagem** `0,00%`.
- `N:N` → formato **Data e hora** `dd/mm/aaaa hh:mm`.

### 3.3 Fórmulas (linha 2 — depois arraste para baixo)

- **L2** (Variação %):
  ```
  =IFERROR(IF(AND(ISNUMBER(J2),ISNUMBER(K2),J2<>0),(K2-J2)/J2,""),"")
  ```
- **O2** (Status Final):
  ```
  =IF(G2="[AUDITORIA] Pronto para Fechamento","Pronto para Fechamento","Pendente")
  ```

> `N` (Última Atualização) é preenchida pelo script em `updateTimestamp_`.

### 3.4 Validação de Dados — coluna G (Status do Laudo)

Selecione `G2:G` → **Dados → Validação de dados** → critério **Lista de itens**:

```
[ADMIN] Aguardando Laudo,[ADMIN] Laudo em Conferência,[PRECIFICAÇÃO] Disponível para Precificar,[PRECIFICAÇÃO] Em Análise de Valor,[PRECIFICAÇÃO] Valor Final Definido,[AUDITORIA] Pronto para Fechamento
```

Marque **Rejeitar entrada**.

### 3.5 Formatação Condicional

Em `G2:G`, crie uma regra para cada status (**Texto é exatamente**):

| Status | Fundo | Texto |
|---|---|---|
| `[ADMIN] Aguardando Laudo` | `#e0e0e0` | `#212121` |
| `[ADMIN] Laudo em Conferência` | `#fff2cc` | `#7f6000` |
| `[PRECIFICAÇÃO] Disponível para Precificar` | `#cfe2f3` | `#0b5394` |
| `[PRECIFICAÇÃO] Em Análise de Valor` | `#fce5cd` | `#b45f06` |
| `[PRECIFICAÇÃO] Valor Final Definido` | `#d9ead3` | `#38761d` |
| `[AUDITORIA] Pronto para Fechamento` | `#1e5631` | `#ffffff` |

Em `L2:L` (Variação):
- **A fórmula personalizada é**: `=OR(L2>0.1,L2<-0.1)`
- Fundo `#f4cccc`, texto `#990000`, negrito.

Em `A2:O` (linha concluída hachurada):
- **A fórmula personalizada é**: `=$G2="[AUDITORIA] Pronto para Fechamento"`
- Fundo `#f5f5f5`, texto `#9e9e9e`.

---

## 4. Aba `⚙️ Config & Logs`

Parte superior — listas auxiliares:

| A | B | C | D | E |
|---|---|---|---|---|
| **FIIs (Fundos)** | **Status disponíveis** | **Responsável (Nome)** | **E-mail** | **Tipo (Admin/Precificação)** |
| KNRI11 | `[ADMIN] Aguardando Laudo` | Ana Lima | ana@...  | Admin |
| HGLG11 | `[ADMIN] Laudo em Conferência` | Pedro Silva | pedro@... | Precificação |
| … | … e os outros 4 status | … | … | … |

Na linha **20** coloque o marcador literal `=== LOG DE AUDITORIA ===`
(essa string é usada pelo script para localizar onde começar a gravar).

Linha **21** (cabeçalho do log):

| A | B | C | D | E | F | G | H | I |
|---|---|---|---|---|---|---|---|---|
| Timestamp | Usuário | Aba | Linha | ID | Fundo | Imóvel | Campo alterado | Valor Anterior → Novo |

Proteja a aba (**Dados → Proteger intervalos**) e, opcionalmente, oculte-a
(botão direito na aba → **Ocultar planilha**).

---

## 5. Aba `🖥️ Dashboard`

1. **Exibir → Linhas de grade** desligado.
2. **B2**: `🏢 Dashboard de Fechamento de FIIs` — fonte 20, negrito, cor `#1a3a6c`.
3. **B3**: `="Atualizado em "&TEXT(NOW(),"dd/mm/yyyy hh:mm")`

### 5.1 Cards de KPI (linha 5 = título, linha 6 = valor)

| Célula | Fórmula |
|---|---|
| `B6` Total | `=COUNTA('📊 Operacional'!A2:A)` |
| `D6` % concluídos (formato `0%`) | `=IFERROR(COUNTIF('📊 Operacional'!O2:O,"Pronto para Fechamento")/COUNTA('📊 Operacional'!A2:A),0)` |
| `F6` Aguardando laudo | `=COUNTIF('📊 Operacional'!G2:G,"[ADMIN] Aguardando Laudo")` |
| `H6` Disponíveis p/ precificar | `=COUNTIF('📊 Operacional'!G2:G,"[PRECIFICAÇÃO] Disponível para Precificar")` |

### 5.2 Base do gráfico de pizza (Status)

`B10` = `Contagem por Status`.
De `B11` a `B16`, liste os 6 status na mesma ordem da validação.
Em `C11:C16`, para cada status:
```
=COUNTIF('📊 Operacional'!G2:G,"<status exato>")
```

Selecione `B11:C16` → **Inserir → Gráfico → Pizza**.

### 5.3 Gráfico de imóveis por Fundo

Em uma área auxiliar (ex.: `B19`):
```
=QUERY('📊 Operacional'!B2:B,"select B, count(B) where B is not null group by B label count(B) 'Imóveis'",0)
```
Selecione o intervalo gerado → **Inserir → Gráfico → Colunas**.

### 5.4 Painel de Alertas

`F10`: `⚠️ Painel de Alertas (próximos 5 dias)` (fundo `#f4cccc`, texto `#990000`).

`F11`:
```
=IFERROR(FILTER({'📊 Operacional'!B2:D,'📊 Operacional'!G2:G,'📊 Operacional'!E2:E},
'📊 Operacional'!O2:O<>"Pronto para Fechamento",
'📊 Operacional'!D2:D-TODAY()<=5,
'📊 Operacional'!D2:D<>""),"Sem pendências críticas")
```

### 5.5 Botão "Gerar Relatório de Auditoria"

1. **Inserir → Desenho** → desenhe um retângulo arredondado com texto
   "📋 Gerar Relatório de Auditoria". Salve.
2. Clique no desenho → menu de 3 pontos → **Atribuir script** → digite
   `verificarPendencias` → OK.

---

## 6. Gatilhos (Triggers)

O `onEdit(e)` que está no código é um **gatilho simples** — dispara sozinho e não
precisa de cadastro. Mas ele roda com permissões limitadas e **não envia e-mails
nem acessa outras planilhas**. Portanto, para o `MailApp` e o log de auditoria
funcionarem, use um **gatilho instalável**:

1. No editor do Apps Script: ícone de **relógio** (à esquerda) → **+ Adicionar gatilho**.
2. Configure:
   - Função a executar: `onEdit`
   - Implantação: `Head`
   - Origem do evento: `Da planilha`
   - Tipo de evento: `Em edição`
3. Salve.
4. O Google pedirá para autorizar — revise as permissões e aceite
   (Sheets + Gmail + UI).

> **Importante:** ao criar o gatilho instalável com o nome `onEdit`, ele
> **coexiste** com o simples. Para evitar execução duplicada, depois que
> o instalável estiver funcionando, renomeie a função do script para algo
> como `onEditInstalado` e atualize o gatilho, ou simplesmente remova o
> gatilho simples apagando o nome (não aplicável aqui — apenas mantenha
> um gatilho). O caminho prático: **mantenha só o instalável** apontando
> para `onEdit`.

### Autorizações a conceder na primeira execução

Execute manualmente **uma vez cada** para disparar o consentimento:

- `setupPlanilha` (se ainda não rodou) — autoriza Sheets.
- `testarNotificacao` com uma linha selecionada — autoriza MailApp.
- `verificarPendencias` — autoriza UI.

---

## 7. Cadastro de responsáveis

Na aba `⚙️ Config & Logs`, preencha as colunas **C (Nome)**, **D (E-mail)** e
**E (Tipo)** com cada pessoa da Admin e da Precificação. O nome digitado na
coluna **I** da aba Operacional precisa bater **exatamente** com a coluna C
aqui — por isso é boa prática transformar `I2:I` da Operacional em validação
por intervalo apontando para `⚙️ Config & Logs!C2:C`.

---

## 8. Teste ponta a ponta

1. Cadastre 1 imóvel na Operacional com Data Base daqui a 3 dias.
2. Mude o Status para `[PRECIFICAÇÃO] Disponível para Precificar`.
3. Confira:
   - E-mail recebido pelo responsável da Precificação.
   - Linha registrada na seção de log (`⚙️ Config & Logs`, linha ≥ 22).
   - Coluna **N (Última Atualização)** carimbada.
4. Clique no botão **📋 Gerar Relatório de Auditoria** no Dashboard — deve
   listar o imóvel como pendente.
5. Mude o Status para `[AUDITORIA] Pronto para Fechamento` e repita o botão
   — deve aparecer "Tudo em ordem".

---

## 9. Solução de problemas

| Sintoma | Causa provável | Correção |
|---|---|---|
| E-mail não chega | Gatilho ainda é o simples | Criar gatilho instalável (seção 6) |
| Log não é gravado | Nome da aba diferente ou marcador ausente | Confirme `⚙️ Config & Logs` e a string `=== LOG DE AUDITORIA ===` em A20 |
| `buscarEmail_` devolve `null` | Nome na Operacional ≠ nome no Config | Padronize ou use validação por intervalo |
| Fórmula L2 retorna `#REF!` ao arrastar | Linhas em branco com valores 0 | A fórmula já trata isso com `IFERROR` |
