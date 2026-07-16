# Intelligent Document Extraction API

Plataforma para extração estruturada de dados de documentos e imagens usando inteligência artificial, com integração ao WhatsApp por meio do WUZAPI.

A solução executa dois serviços no mesmo Docker Compose:

- **API FastAPI**: recebe documentos, consulta o Google Gemini, retorna JSON estruturado e registra requisições e consumo.
- **WUZAPI**: recebe mensagens e imagens do WhatsApp, encaminha o conteúdo para a API e envia a resposta ao usuário.

---

## Sumário

1. [Arquitetura](#arquitetura)
2. [Requisitos](#requisitos)
3. [Visão geral da instalação](#visão-geral-da-instalação)
4. [Criar a máquina virtual na Oracle Cloud](#1-criar-a-máquina-virtual-na-oracle-cloud)
5. [Abrir as portas na Oracle Cloud](#2-abrir-as-portas-na-oracle-cloud)
6. [Acessar o Ubuntu pelo PowerShell](#3-acessar-o-ubuntu-pelo-powershell)
7. [Preparar o Ubuntu](#4-preparar-o-ubuntu)
8. [Instalar Docker e Docker Compose](#5-instalar-docker-e-docker-compose)
9. [Criar e configurar o Supabase](#6-criar-e-configurar-o-supabase)
10. [Obter a chave do Google Gemini](#7-obter-a-chave-do-google-gemini)
11. [Clonar e instalar o projeto](#8-clonar-e-instalar-o-projeto)
12. [Preencher o arquivo `.env`](#9-preencher-o-arquivo-env)
13. [Provisionar o banco e iniciar os containers](#10-provisionar-o-banco-e-iniciar-os-containers)
14. [Configurar o WUZAPI e o WhatsApp](#11-configurar-o-wuzapi-e-o-whatsapp)
15. [Testar a instalação](#12-testar-a-instalação)
16. [Comandos de operação](#comandos-de-operação)
17. [Atualização](#atualização)
18. [Backup](#backup)
19. [Solução de problemas](#solução-de-problemas)
20. [Endpoints](#endpoints)
21. [Estrutura do projeto](#estrutura-do-projeto)

---

# Arquitetura

```text
WhatsApp
   │
   ▼
WUZAPI
   │  webhook interno
   ▼
FastAPI
   ├── Google Gemini
   └── PostgreSQL / Supabase
```

A comunicação entre os containers ocorre pela rede interna do Docker:

```text
http://api-transcricao:8000
http://wuzapi:8080
```

Esses endereços internos não devem ser substituídos por `localhost`.

---

# Requisitos

Para seguir este guia completo:

- Conta na Oracle Cloud Infrastructure;
- Conta no Supabase;
- Chave da API do Google Gemini;
- Computador Windows com PowerShell e cliente OpenSSH;
- Repositório Git acessível;
- Um número de WhatsApp para conectar ao WUZAPI.

O projeto foi validado com:

- Ubuntu Server;
- Docker Engine;
- Docker Compose Plugin;
- PostgreSQL hospedado no Supabase;
- Oracle Cloud Compute;
- WUZAPI;
- Google Gemini.

---

# Visão geral da instalação

O processo completo é:

1. Criar uma máquina Ubuntu na Oracle Cloud.
2. Liberar as portas necessárias.
3. Acessar a VM por SSH.
4. Instalar Docker, Docker Compose e Git.
5. Criar um projeto no Supabase.
6. Copiar a string **Session pooler** do Supabase.
7. Clonar este repositório.
8. Executar `install.sh` pela primeira vez.
9. Preencher o `.env`.
10. Executar `install.sh` novamente.
11. Criar uma instância no WUZAPI.
12. Conectar o WhatsApp pelo QR Code.
13. Configurar o webhook.
14. Enviar uma imagem e validar o fluxo completo.

---

# 1. Criar a máquina virtual na Oracle Cloud

## 1.1 Acessar o painel

1. Entre no Console da Oracle Cloud.
2. Abra o menu principal no canto superior esquerdo.
3. Acesse **Compute**.
4. Clique em **Instances**.
5. Clique em **Create instance**.

## 1.2 Configurações principais

Preencha:

- **Name**: um nome identificável, por exemplo `api-transcricao`.
- **Compartment**: mantenha o compartimento usado pelo seu projeto.

## 1.3 Selecionar o sistema operacional

Na área de imagem:

1. Clique em **Change image**.
2. Selecione **Canonical Ubuntu**.
3. Prefira uma versão LTS suportada, como Ubuntu 24.04 LTS.
4. Confirme a seleção.

## 1.4 Selecionar a máquina

Na área de shape:

1. Clique em **Change shape**.
2. Escolha uma configuração compatível com sua conta e região.
3. Para um ambiente controlado e com pouco volume, uma VM pequena costuma ser suficiente.
4. Confirme que a arquitetura escolhida é suportada pelas imagens Docker utilizadas.

A disponibilidade de shapes gratuitos e recursos varia por região e por conta.

## 1.5 Configurar a rede

Na área de networking:

1. Selecione ou crie uma **VCN**.
2. Use uma **subnet pública**.
3. Ative a atribuição de **Public IPv4 address**.
4. Mantenha o acesso SSH pela porta `22`.

Anote o endereço IPv4 público da instância após a criação.

## 1.6 Configurar a chave SSH

Na área de chaves SSH, escolha uma opção:

- gerar um novo par de chaves;
- enviar sua chave pública;
- colar uma chave pública existente.

Para uma primeira instalação:

1. Escolha a geração automática.
2. Baixe a **private key**.
3. Guarde o arquivo em local seguro.
4. Não envie essa chave para o GitHub, e-mail, chat ou repositório.

## 1.7 Criar a instância

1. Revise as informações.
2. Clique em **Create**.
3. Aguarde o estado mudar para **Running**.
4. Copie o **Public IPv4 address**.

---

# 2. Abrir as portas na Oracle Cloud

O projeto utiliza atualmente:

| Porta | Serviço | Uso |
|---|---|---|
| `22` | SSH | Administração da VM |
| `8000` | FastAPI | API e Swagger |
| `8080` | WUZAPI | Painel e API do WhatsApp |

> Em produção pública, o ideal é não expor diretamente `8000` e `8080`. Use proxy reverso e HTTPS nas portas `80` e `443`. Para a fase atual, restrinja as portas ao seu IP sempre que possível.

## 2.1 Encontrar a Security List

1. No Console da Oracle, abra a instância.
2. Clique na subnet ou VCN associada.
3. No menu de recursos, abra **Security Lists**.
4. Clique na Security List associada à subnet pública.
5. Clique em **Add Ingress Rules**.

## 2.2 Regra SSH

Adicione ou confirme:

- **Source type**: CIDR
- **Source CIDR**: seu IP público seguido de `/32`, por exemplo `203.0.113.10/32`
- **IP Protocol**: TCP
- **Destination port range**: `22`

Usar seu IP é mais seguro do que liberar SSH para toda a internet.

## 2.3 Regra da API

Adicione:

- **Source type**: CIDR
- **Source CIDR**: seu IP público com `/32`
- **IP Protocol**: TCP
- **Destination port range**: `8000`
- **Description**: `FastAPI`

Para um teste temporário acessível de qualquer rede, pode-se usar `0.0.0.0/0`, mas isso aumenta a exposição.

## 2.4 Regra do WUZAPI

Adicione:

- **Source type**: CIDR
- **Source CIDR**: seu IP público com `/32`
- **IP Protocol**: TCP
- **Destination port range**: `8080`
- **Description**: `WUZAPI`

## 2.5 Firewall do Ubuntu

Depois de acessar a VM, verifique:

```bash
sudo ufw status
```

Se o UFW estiver ativo, permita SSH antes de qualquer outra alteração:

```bash
sudo ufw allow OpenSSH
```

Para liberar as portas globalmente:

```bash
sudo ufw allow 8000/tcp
sudo ufw allow 8080/tcp
```

Forma mais segura, limitando ao seu IP:

```bash
sudo ufw allow from SEU_IP_PUBLICO to any port 8000 proto tcp
sudo ufw allow from SEU_IP_PUBLICO to any port 8080 proto tcp
```

Confira:

```bash
sudo ufw status numbered
```

---

# 3. Acessar o Ubuntu pelo PowerShell

O Windows moderno normalmente já inclui o cliente OpenSSH.

## 3.1 Verificar o SSH

Abra o PowerShell:

```powershell
ssh -V
```

Se o comando não existir:

1. Abra **Configurações**.
2. Acesse **Sistema** → **Recursos opcionais**.
3. Procure **Cliente OpenSSH**.
4. Instale o recurso.
5. Abra novamente o PowerShell.

## 3.2 Organizar a chave privada

Exemplo:

```text
C:\Users\Gabriel\.ssh\oracle-api.key
```

Não coloque a chave dentro do repositório do projeto.

## 3.3 Corrigir permissões da chave no Windows

Quando o SSH recusar a chave por permissões abertas, execute no PowerShell:

```powershell
icacls "C:\Users\Gabriel\.ssh\oracle-api.key" /inheritance:r
icacls "C:\Users\Gabriel\.ssh\oracle-api.key" /grant:r "$($env:USERNAME):(R)"
```

## 3.4 Conectar

```powershell
ssh -i "C:\Users\Gabriel\.ssh\oracle-api.key" ubuntu@IP_PUBLICO_DA_VM
```

Exemplo:

```powershell
ssh -i "C:\Users\Gabriel\.ssh\oracle-api.key" ubuntu@203.0.113.50
```

Na primeira conexão, confirme a fingerprint digitando:

```text
yes
```

## 3.5 Problemas comuns

### `Permission denied (publickey)`

Verifique:

- usuário correto: normalmente `ubuntu`;
- chave correspondente à chave pública cadastrada na VM;
- caminho correto do arquivo;
- permissões da chave privada.

### `Connection timed out`

Verifique:

- instância em estado `Running`;
- endereço IP correto;
- regra de entrada para a porta `22`;
- firewall local e da rede;
- se seu IP público mudou.

---

# 4. Preparar o Ubuntu

Atualize os pacotes:

```bash
sudo apt update
sudo apt upgrade -y
```

Instale ferramentas básicas:

```bash
sudo apt install -y git curl ca-certificates
```

Confirme:

```bash
git --version
curl --version
```

Opcionalmente, configure o fuso horário:

```bash
sudo timedatectl set-timezone America/Sao_Paulo
timedatectl
```

---

# 5. Instalar Docker e Docker Compose

Use o repositório oficial do Docker.

## 5.1 Remover pacotes conflitantes

```bash
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  sudo apt-get remove -y "$pkg" 2>/dev/null || true
done
```

## 5.2 Adicionar a chave do repositório Docker

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

## 5.3 Adicionar o repositório

```bash
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

## 5.4 Instalar Docker Engine e Compose

```bash
sudo apt-get update
sudo apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin
```

## 5.5 Testar

```bash
sudo docker run --rm hello-world
```

## 5.6 Usar Docker sem `sudo`

```bash
sudo usermod -aG docker "$USER"
```

Aplique a mudança encerrando e abrindo novamente a sessão SSH:

```bash
exit
```

Conecte-se novamente pelo PowerShell e valide:

```bash
docker version
docker compose version
docker info
```

Também é possível aplicar temporariamente com:

```bash
newgrp docker
```

---

# 6. Criar e configurar o Supabase

## 6.1 Criar o projeto

1. Entre no painel do Supabase.
2. Clique em **New project**.
3. Escolha a organização.
4. Informe o nome do projeto.
5. Crie uma senha forte para o banco.
6. Selecione uma região próxima da VM.
7. Clique em **Create new project**.
8. Aguarde o provisionamento.

Guarde a senha do banco. Ela será necessária na string de conexão.

## 6.2 Qual string de conexão usar

No projeto:

1. Clique em **Connect**.
2. Localize as opções de conexão PostgreSQL.
3. Selecione **Session pooler**.
4. Copie a URI que usa a porta `5432`.

Formato aproximado:

```text
postgresql://postgres.PROJECT_REF:SENHA@aws-REGIAO.pooler.supabase.com:5432/postgres
```

### Por que usar Session pooler

A conexão direta geralmente utiliza o host:

```text
db.PROJECT_REF.supabase.co
```

Esse host pode resolver para IPv6. Se a VM Oracle estiver em uma rede apenas IPv4, o erro será semelhante a:

```text
Network is unreachable
connection to server ... IPv6 ... port 5432 failed
```

Nesse cenário, use o **Session pooler**, que é compatível com redes IPv4.

### Não use por engano

Evite usar a **Direct connection** em uma VM sem conectividade IPv6.

Para esta aplicação persistente, prefira o **Session pooler na porta 5432**, não o Transaction pooler usado em cenários serverless de transações curtas.

## 6.3 Inserir a senha

A string copiada pode conter:

```text
[YOUR-PASSWORD]
```

Substitua pelo valor real.

Se a senha tiver caracteres especiais, eles precisam ser codificados para URL.

Exemplos:

| Caractere | Codificação |
|---|---|
| `@` | `%40` |
| `#` | `%23` |
| `%` | `%25` |
| `/` | `%2F` |
| `:` | `%3A` |

Exemplo:

```text
Senha: Minha@Senha#2026
Na URL: Minha%40Senha%232026
```

## 6.4 Não criar tabelas manualmente

O instalador executa o provisionamento automaticamente:

- testa a conexão;
- cria as tabelas ausentes;
- cria índices;
- cria a primeira aplicação;
- gera a primeira API key;
- atualiza `WUZAPI_APPLICATION_ID`.

As tabelas esperadas são:

```text
applications
requests
extractions
usage_logs
```

---

# 7. Obter a chave do Google Gemini

1. Entre no Google AI Studio.
2. Abra a área de API keys.
3. Crie uma chave.
4. Copie a chave.
5. Guarde em local seguro.

Ela será usada em:

```dotenv
GEMINI_API_KEY=
```

Não coloque essa chave no GitHub.

---

# 8. Clonar e instalar o projeto

## 8.1 Criar a pasta de serviços

```bash
mkdir -p ~/services
cd ~/services
```

## 8.2 Clonar o repositório

```bash
git clone https://github.com/gabrielalon60-ux/api-transcricao-ia.git
cd api-transcricao-ia
```

## 8.3 Conferir os arquivos

```bash
ls -la
```

Devem existir, entre outros:

```text
Dockerfile
docker-compose.yml
install.sh
update.sh
.env.example
app/
scripts/
wuzapi/
```

## 8.4 Dar permissão aos scripts

```bash
chmod +x install.sh update.sh
```

## 8.5 Primeira execução

```bash
./install.sh
```

Na primeira execução, o instalador:

1. verifica Docker;
2. verifica Docker Compose;
3. cria as pastas persistentes;
4. copia `.env.example` para `.env`;
5. pausa para configuração.

Resultado esperado:

```text
Arquivo .env criado com base no .env.example.
A instalação foi pausada para configuração.
```

---

# 9. Preencher o arquivo `.env`

Abra:

```bash
nano .env
```

Salve no Nano com:

1. `Ctrl + O`
2. Enter
3. `Ctrl + X`

## 9.1 Modelo comentado

```dotenv
####################################################
# DATABASE
####################################################

# Use a URI Session pooler do Supabase, porta 5432.
DATABASE_URL=postgresql://postgres.PROJECT_REF:SENHA@aws-REGIAO.pooler.supabase.com:5432/postgres


####################################################
# AI PROVIDER
####################################################

GEMINI_API_KEY=SUA_CHAVE_GEMINI


####################################################
# SECURITY
####################################################

# Segredo usado no HMAC-SHA256 das API keys.
# Não é a mesma coisa que a API key do cliente.
API_KEY_HASH_SECRET=SEGREDO_ALEATORIO_FORTE


####################################################
# APPLICATION
####################################################

APP_ENV=production
APP_DEBUG=false
LOG_LEVEL=INFO
MAX_UPLOAD_SIZE_MB=10


####################################################
# WUZAPI CLIENT: API -> WUZAPI
####################################################

# Endereço interno da rede Docker. Não use localhost.
WUZAPI_BASE_URL=http://wuzapi:8080

# Nome/identificação da instância usada pela integração.
WUZAPI_INSTANCE=NOME_DA_INSTANCIA

# Token do usuário/instância criado no WUZAPI.
WUZAPI_TOKEN=TOKEN_DA_INSTANCIA

# Na primeira instalação, mantenha o UUID zerado.
# O provisionador substituirá automaticamente.
WUZAPI_APPLICATION_ID=00000000-0000-0000-0000-000000000000


####################################################
# WUZAPI SERVER
####################################################

# Token administrativo usado para acessar e administrar o WUZAPI.
WUZAPI_ADMIN_TOKEN=TOKEN_ADMINISTRATIVO_FORTE

WUZAPI_PORT=8080
SESSION_DEVICE_NAME=API Transcrição IA
TZ=America/Sao_Paulo
```

## 9.2 Gerar `API_KEY_HASH_SECRET`

Na VM:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copie o resultado para:

```dotenv
API_KEY_HASH_SECRET=
```

Esse valor deve permanecer estável. Se for alterado, os hashes já gravados deixam de corresponder às API keys antigas.

## 9.3 Diferença entre os tokens

### `API_KEY_HASH_SECRET`

Segredo interno da API usado para gerar HMAC das chaves dos clientes.

### API key gerada pelo provisionador

Chave usada no cabeçalho:

```http
Authorization: Bearer sk_live_...
```

Ela é exibida somente quando a aplicação é criada.

### `WUZAPI_ADMIN_TOKEN`

Token administrativo do servidor WUZAPI.

### `WUZAPI_TOKEN`

Token do usuário/instância que a API usa nas chamadas ao WUZAPI.

### `WUZAPI_APPLICATION_ID`

UUID da linha da tabela `applications` usada para associar as requisições do WhatsApp. O provisionador preenche automaticamente na instalação inicial.

---

# 10. Provisionar o banco e iniciar os containers

Execute novamente:

```bash
./install.sh
```

O instalador irá:

1. construir a imagem da API;
2. conectar ao Supabase;
3. executar `SELECT 1`;
4. criar as tabelas e índices ausentes;
5. verificar aplicações existentes;
6. oferecer a criação da primeira aplicação;
7. gerar uma API key;
8. salvar somente o HMAC no banco;
9. atualizar `WUZAPI_APPLICATION_ID`;
10. iniciar os containers.

Quando aparecer:

```text
Deseja criar a aplicação inicial? [S/n]:
```

Pressione Enter ou responda:

```text
s
```

Informe um nome, por exemplo:

```text
API_Transcricao
```

## 10.1 Guardar a API key

O instalador mostrará:

```text
API key:

sk_live_...
```

Guarde a chave imediatamente em um gerenciador de senhas.

Ela:

- será exibida somente uma vez;
- não é armazenada em texto puro;
- não deve ser enviada em logs, prints, tickets ou chats;
- é diferente de `WUZAPI_TOKEN`.

Depois, o instalador iniciará:

```text
api-transcricao
wuzapi
```

## 10.2 Confirmar o UUID automático

```bash
grep '^WUZAPI_APPLICATION_ID=' .env
```

O valor deve ser um UUID real, e não mais o UUID zerado.

---

# 11. Configurar o WUZAPI e o WhatsApp

## 11.1 Abrir o WUZAPI

No navegador:

```text
http://IP_PUBLICO_DA_VM:8080
```

Use o token administrativo configurado em:

```dotenv
WUZAPI_ADMIN_TOKEN=
```

## 11.2 Criar usuário ou instância

No painel do WUZAPI:

1. Crie o usuário/instância.
2. Defina o nome.
3. Defina ou copie o token.
4. Garanta que o token usado pela integração corresponda ao valor de:

```dotenv
WUZAPI_TOKEN=
```

Se alterar o token no `.env`, recrie os containers:

```bash
docker compose up -d --force-recreate
```

## 11.3 Conectar o WhatsApp

1. Abra a instância.
2. Solicite a conexão.
3. Leia o QR Code usando o WhatsApp.
4. Aguarde o status de conexão.
5. Confirme que a sessão permanece conectada.

## 11.4 Configurar o webhook

Configure exatamente:

```text
http://api-transcricao:8000/whatsapp/webhook
```

Atenção:

- use `api-transcricao`, que é o nome do serviço Docker;
- não use o IP público;
- não use `localhost`;
- não duplique o protocolo.

Correto:

```text
http://api-transcricao:8000/whatsapp/webhook
```

Incorreto:

```text
http://localhost:8000/whatsapp/webhook
```

Incorreto:

```text
http://http://api-transcricao:8000/whatsapp/webhook
```

O erro de protocolo duplicado normalmente aparece nos logs como tentativa de resolver o host `http`.

Ative os eventos de mensagens recebidas e mídia. Se a versão do painel apresentar nomes diferentes, habilite os eventos relacionados ao recebimento de mensagens.

---

# 12. Testar a instalação

## 12.1 Estado dos containers

```bash
docker compose ps
```

Esperado:

```text
api-transcricao   Up
wuzapi            Up
```

## 12.2 Health check

```bash
curl -s http://localhost:8000/health
echo
```

Esperado:

```json
{"status":"ok","version":"1.0.0"}
```

## 12.3 Swagger

No navegador:

```text
http://IP_PUBLICO_DA_VM:8000/docs
```

## 12.4 WUZAPI

No navegador:

```text
http://IP_PUBLICO_DA_VM:8080
```

## 12.5 Testar autenticação da API

Use a API key gerada pelo provisionador:

```bash
curl -X GET \
  "http://localhost:8000/requests" \
  -H "Authorization: Bearer SUA_API_KEY"
```

## 12.6 Testar extração

```bash
curl -X POST \
  "http://localhost:8000/extract" \
  -H "Authorization: Bearer SUA_API_KEY" \
  -F "file=@/CAMINHO/DA/IMAGEM.jpg"
```

## 12.7 Testar pelo WhatsApp

1. Abra os logs:

```bash
docker compose logs -f api-transcricao wuzapi
```

2. Envie uma imagem para o número conectado.
3. Confirme no WUZAPI a chamada do webhook.
4. Confirme na API o `POST /whatsapp/webhook`.
5. Verifique a resposta no WhatsApp.
6. Confira no Supabase se foram criados registros em:
   - `requests`;
   - `extractions`;
   - `usage_logs`.

---

# Comandos de operação

Execute os comandos na raiz do projeto:

```bash
cd ~/services/api-transcricao-ia
```

## Status

```bash
docker compose ps
```

## Logs de tudo

```bash
docker compose logs -f
```

## Logs somente da API

```bash
docker compose logs -f api-transcricao
```

## Logs somente do WUZAPI

```bash
docker compose logs -f wuzapi
```

## Reiniciar

```bash
docker compose restart
```

## Recriar após alterar `.env`

```bash
docker compose up -d --force-recreate
```

## Parar sem apagar dados

```bash
docker compose down
```

## Subir novamente

```bash
docker compose up -d
```

## Reconstruir a API

```bash
docker compose up -d --build
```

---

# Atualização

Na raiz do projeto:

```bash
./update.sh
```

O script:

- valida Docker;
- valida Docker Compose;
- atualiza o repositório com fast-forward;
- baixa imagens;
- reconstrói a API;
- remove containers órfãos;
- remove imagens antigas não utilizadas;
- mostra o estado final.

Antes de atualizar uma instalação importante, faça backup do WUZAPI e do `.env`.

---

# Backup

Os dados persistentes do WUZAPI ficam em:

```text
wuzapi/dbdata
wuzapi/files
```

O `.env` contém segredos e também deve ser protegido.

Exemplo:

```bash
cd ~/services/api-transcricao-ia

BACKUP_DIR="$HOME/backups/api-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

cp .env "$BACKUP_DIR/.env"

tar -czf "$BACKUP_DIR/wuzapi-data.tar.gz" \
  wuzapi/dbdata \
  wuzapi/files

chmod -R go-rwx "$BACKUP_DIR"

echo "$BACKUP_DIR"
```

Não envie o backup para o GitHub.

---

# Solução de problemas

## Docker não está rodando

Erro:

```text
failed to connect to the Docker API
```

Na VM:

```bash
sudo systemctl status docker
sudo systemctl start docker
sudo systemctl enable docker
```

No Windows com Docker Desktop, abra o Docker Desktop e aguarde o Engine iniciar.

## Nome de container em uso

Erro:

```text
The container name "/wuzapi" is already in use
```

Confira:

```bash
docker ps -a
```

Em um ambiente descartável, remova o container antigo:

```bash
docker rm -f wuzapi api-transcricao
```

Não execute essa remoção em produção sem confirmar os volumes persistentes.

## API reiniciando por variáveis extras

Erro:

```text
Extra inputs are not permitted
```

A configuração da API deve permitir variáveis compartilhadas no `.env`:

```python
extra = "ignore"
```

## Supabase: `Network is unreachable`

Sintoma:

```text
connection to server ... IPv6 ... failed: Network is unreachable
```

Causa provável:

- uso da Direct connection;
- VM sem rota IPv6.

Correção:

- painel Supabase;
- botão **Connect**;
- selecionar **Session pooler**;
- copiar a URI da porta `5432`;
- atualizar `DATABASE_URL`;
- executar novamente `./install.sh`.

## Supabase: senha inválida

Verifique:

- senha do banco;
- usuário no formato `postgres.PROJECT_REF`;
- host do Session pooler;
- caracteres especiais codificados na URL.

## API não abre externamente

Na VM:

```bash
curl http://localhost:8000/health
```

Se funcionar localmente, mas não no navegador:

- confira a Security List da Oracle;
- confira a porta `8000`;
- confira seu IP/CIDR;
- confira o UFW;
- confira o IP público da VM.

## WUZAPI não abre externamente

Na VM:

```bash
curl -i http://localhost:8080
```

Depois confira:

- Security List para `8080`;
- UFW;
- container `wuzapi`;
- `docker compose logs wuzapi`.

## Mensagem não chega à API

Acompanhe:

```bash
docker compose logs -f api-transcricao wuzapi
```

Confirme que o webhook é exatamente:

```text
http://api-transcricao:8000/whatsapp/webhook
```

Se não aparece nenhum `POST /whatsapp/webhook` na API, o problema ocorre antes da extração, normalmente na configuração do webhook.

## Webhook com `http://http://`

Erro:

```text
lookup http on 127.0.0.11:53
```

Corrija:

```text
http://http://api-transcricao:8000/whatsapp/webhook
```

para:

```text
http://api-transcricao:8000/whatsapp/webhook
```

## `localhost` não funciona entre containers

Dentro do WUZAPI:

```text
localhost
```

significa o próprio container WUZAPI.

Use o nome do serviço:

```text
api-transcricao
```

Da API para o WUZAPI:

```text
http://wuzapi:8080
```

## Alterei o `.env`, mas nada mudou

Recrie os containers:

```bash
docker compose up -d --force-recreate
```

## Verificar comunicação interna

A partir do WUZAPI:

```bash
docker exec wuzapi sh -c \
  "wget -qO- http://api-transcricao:8000/health || true"
```

## Conferir variáveis sem mostrar segredos

```bash
grep -E \
'^(WUZAPI_BASE_URL|WUZAPI_INSTANCE|WUZAPI_APPLICATION_ID)=' \
.env
```

Verificar se o token está preenchido sem exibi-lo:

```bash
grep -q '^WUZAPI_TOKEN=.\+' .env \
  && echo "WUZAPI_TOKEN configurado" \
  || echo "WUZAPI_TOKEN ausente"
```

---

# Autenticação e aplicações

Cada aplicação possui:

- UUID;
- nome;
- hash HMAC-SHA256 da API key;
- status ativo;
- data de criação.

A chave bruta não é armazenada.

Para criar outra aplicação:

```bash
docker compose run --rm --no-deps \
  -v "$(pwd)/.env:/app/.env" \
  api-transcricao \
  python scripts/provision.py --new-app
```

Guarde a nova chave quando ela for exibida.

A criação de uma aplicação adicional não deve substituir automaticamente o `WUZAPI_APPLICATION_ID`.

---

# Endpoints

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/health` | Verifica o estado da API |
| `POST` | `/extract` | Extrai dados de uma imagem |
| `GET` | `/requests` | Lista o histórico da aplicação |
| `GET` | `/usage` | Retorna consumo e custos estimados |
| `POST` | `/whatsapp/webhook` | Recebe eventos do WUZAPI |
| `GET` | `/docs` | Swagger UI |

Endpoints protegidos exigem:

```http
Authorization: Bearer SUA_API_KEY
```

---

# Estrutura do projeto

```text
api-transcricao-ia/
├── .github/
│   └── workflows/
├── app/
│   ├── api/
│   ├── auth/
│   ├── core/
│   ├── database/
│   ├── integrations/
│   ├── prompts/
│   ├── schemas/
│   ├── services/
│   ├── tests/
│   └── main.py
├── scripts/
│   ├── provision.py
│   ├── generate_api_key.py
│   ├── migrate.sql
│   └── run_migration.py
├── wuzapi/
│   ├── dbdata/
│   └── files/
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── README.md
├── docker-compose.yml
├── install.sh
├── requirements.txt
└── update.sh
```

> Os scripts legados de migração podem ser removidos quando a consolidação do provisionamento estiver finalizada.

---

# Segurança

- Nunca envie `.env` ao Git.
- Nunca publique a chave privada SSH.
- Nunca compartilhe a API key bruta.
- Nunca envie logs de instalação que contenham a primeira API key.
- Restrinja as portas `22`, `8000` e `8080` ao seu IP.
- Faça backup antes de atualizar.
- Use senhas e tokens diferentes para cada finalidade.
- Em uma próxima fase, coloque a aplicação atrás de HTTPS e proxy reverso.

---

# Documentação oficial útil

- Oracle Cloud Infrastructure: documentação de Compute, VCN e Security Lists.
- Docker: instalação oficial do Docker Engine no Ubuntu e Docker Compose Plugin.
- Supabase: conexão com PostgreSQL e Session pooler.
- Microsoft: cliente OpenSSH no Windows.
