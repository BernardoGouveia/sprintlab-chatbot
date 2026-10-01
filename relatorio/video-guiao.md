# Guião do Vídeo de Demonstração (~4 min)

> ⚠️ **GRAVA COM ÁUDIO.** O erro mais comum (e penalizado) é entregar uma captura de ecrã
> muda. Narra cada passo. Usa o tema **claro** para ler melhor, fala devagar.
>
> Formato: captura de ecrã do chatbox + a tua narração. Tem dois GitLabs prontos antes de
> gravar (ex.: SprintLab + lp2-jogo) para mostrar o multi-tenant.

---

### 0:00 – 0:25 · Introdução (o problema)

> **Narração:** "Em equipas que usam o GitLab, gerir um projeto obriga a sair do Teams,
> abrir o navegador e navegar manualmente — é fragmentado e exige saber usar o GitLab.
> O **SprintLab Chatbox** traz isso para dentro do Teams, em linguagem natural."

**Ecrã:** mostra o chatbox embebido (ou a página inicial com os chips de sugestão).

### 0:25 – 0:55 · Multi-tenant (liga vários GitLabs)

> **Narração:** "Funciona com vários GitLabs — qualquer instância acessível por https. Já
> tenho aqui dois ligados — o projeto SprintLab e um projeto Java. Cada um é um espaço de
> trabalho próprio."

**Ecrã:** clica entre os dois *workspaces* na barra lateral.

### 0:55 – 1:30 · Pergunta em linguagem natural (+ determinismo)

> **Narração:** "Pergunto em português normal..."

**Ecrã:** escreve `quantas issues estão abertas?` e depois `quem tem mais commits?`.

> **Narração (importante):** "Repara: estes números — 2318 commits — **não são inventados
> pela IA**. Vêm diretamente do GitLab. O mesmo número aparece no painel à direita. A IA
> nunca conta; só interpreta."

### 1:30 – 2:10 · Relatório do projeto (determinístico)

**Ecrã:** clica no chip **📊 Relatório do projeto**.

> **Narração:** "Gera um relatório completo — estado, commits, contribuidores — tudo
> calculado em código. Repara no rodapé: *'relatório 100% determinístico, nenhum número
> gerado por IA'*. Posso exportá-lo."

### 2:10 – 2:55 · Investigação de código (git blame + IA)

**Ecrã:** escreve `analisa o erro em <ficheiro> linha <N>`.

> **Narração:** "Aqui está o diferenciador: a caixa azul são **factos** do git blame —
> que commit alterou esta linha, quem, quando, com link para o GitLab. A caixa âmbar é a
> **hipótese da IA** sobre o bug, claramente separada. Facto e palpite nunca se confundem."

### 2:55 – 3:40 · Commit por IA (a parte "wow", segura)

**Ecrã:** escreve `cria um ficheiro NOTAS.md com um resumo e faz commit`.

> **Narração:** "Peço à IA para gerar código e fazer commit — no GitLab predefinido do
> servidor, só quem tem a **chave de acesso** pode escrever. Mas — repara — **nada é escrito
> ainda**. Mostra-me uma pré-visualização. Eu confirmo... e a IA cria uma **branch separada
> e um Merge Request**. A branch principal nunca é tocada."

**Ecrã:** clica **Abrir o Merge Request** → mostra o MR no GitLab.

> **Narração:** "O código gerado por IA vai para revisão humana. O commit leva *skip ci*:
> nenhum pipeline corre este código antes dessa revisão. A IA propõe; eu aprovo."

### 3:40 – 4:00 · Fecho (anti-workslop)

> **Narração:** "Em resumo: o SprintLab nunca me dá um facto que eu tenha de verificar — os
> números são do GitLab — nem uma ação que eu tenha de desfazer — tudo é confirmado, e o que
> é irreversível, como apagar, é avisado antes. É uma IA que **reduz** o meu trabalho, em vez
> de me dar trabalho de revisão.
> Obrigado."

---

### Checklist técnico antes de gravar
- [ ] Deploy da **nova versão** feito no Space (`server.py` + pasta `src/` + frontend) e
      *secret* `APP_ACCESS_KEY` definido
- [ ] Chave de acesso introduzida em ⚙️ **Definições** antes de começar a gravar (sem ela o
      commit por IA com o token do servidor é recusado) — **não a mostres no ecrã**
- [ ] Áudio a funcionar (testa 5 segundos primeiro!)
- [ ] Tema **claro** ativo
- [ ] 2 GitLabs ligados (multi-tenant)
- [ ] Um ficheiro real à mão para o blame (`src/...`)
- [ ] Resolução legível (zoom do browser ~110-125%)
- [ ] Esconde tokens/segredos do ecrã
