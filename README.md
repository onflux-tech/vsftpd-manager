# vsftpd-manager

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-teal.svg)](https://fastapi.tiangolo.com/)
[![Alpine](https://img.shields.io/badge/Alpine-3.19-blue.svg)](https://alpinelinux.org/)

**Containerized FTP server with REST API and web dashboard for user management.**

> **Languages**: [English](#english) | [Portugues](#portugues)

---

## English

### Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Integration Examples](#integration-examples)
- [Security](#security)
- [Contributing](#contributing)
- [Support](#support)
- [License](#license)

### Overview

vsftpd-manager is a production-ready, containerized FTP server built on **vsftpd** (Alpine Linux) with a **FastAPI**-powered REST API and web dashboard for managing FTP users. Users are persisted in a JSON file and survive container recreations. Each user is chrooted to their own home directory for isolation.

### Key Features

- **vsftpd FTP Server** -- Lightweight, secure FTP over Alpine Linux
- **REST API** -- Full CRUD for user management via FastAPI
- **Web Dashboard** -- Built-in UI with dark/light mode for managing users
- **Persistent Users** -- JSON-based persistence across container restarts
- **Per-User Chroot** -- Each user is confined to their home directory
- **Token Authentication** -- API protected by constant-time token comparison
- **Rate Limiting** -- In-memory per-IP rate limiting (30 req/min)
- **Security Headers** -- X-Frame-Options, CSP, XSS protection, and more
- **Traefik Ready** -- Labels included for HTTPS via Traefik + Cloudflare
- **Multi-arch** -- Docker images built for linux/amd64 and linux/arm64

### Quick Start

#### Using the pre-built image (recommended)

```bash
docker pull ghcr.io/onflux-tech/vsftpd-manager:latest
```

#### Using Docker Compose

```yaml
services:
  ftp-server:
    image: ghcr.io/onflux-tech/vsftpd-manager:latest
    container_name: ftp-server
    restart: unless-stopped
    environment:
      USERS: "admin|StrongPassword123|/ftp/admin"
      ADMIN_TOKEN: change-this-to-a-strong-token
      ADDRESS: ftp.example.com
      MIN_PORT: 50000
      MAX_PORT: 50100
      MANAGER_PORT: 8080
      TZ: America/Sao_Paulo
    volumes:
      - ./ftp-data:/ftp
      - ./data:/data
    ports:
      - "21:21"
      - "50000-50100:50000-50100"
      - "8080:8080"
```

```bash
docker compose up -d
```

Access the web dashboard at `http://localhost:8080`.

#### Building from source

```bash
git clone https://github.com/onflux-tech/vsftpd-manager.git
cd vsftpd-manager
docker compose up -d --build
```

### Configuration

#### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `USERS` | (empty) | Bootstrap users in `name\|password\|home` format, space-separated |
| `ADMIN_TOKEN` | `changeme` | Authentication token for API and dashboard |
| `ADDRESS` | (empty) | Hostname for FTP passive address |
| `MIN_PORT` | `50000` | Minimum passive port range |
| `MAX_PORT` | `50100` | Maximum passive port range |
| `MANAGER_PORT` | `8080` | API/dashboard listen port |
| `TZ` | `America/Sao_Paulo` | Container timezone |

#### Ports

| Port | Service |
|------|---------|
| 21 | FTP (vsftpd) |
| 50000-50100 | FTP passive range |
| 8080 | REST API + Web dashboard |

#### Project Structure

```
.
├── Dockerfile            # Multi-stage build (pidproxy + vsftpd + Python venv)
├── docker-compose.yml    # Orchestration with volumes and optional Traefik labels
├── manager_api.py        # REST API + HTML dashboard (FastAPI/Uvicorn)
├── start_vsftpd.sh       # Entrypoint: creates env users + restores JSON + starts vsftpd
├── vsftpd.conf           # vsftpd configuration
└── data/
    └── users.json        # Persisted user database (created automatically)
```

### API Reference

All API routes (except `/api/health`) require authentication via one of:

```
X-API-Key: <ADMIN_TOKEN>
```

or

```
Authorization: Bearer <ADMIN_TOKEN>
```

#### Health Check

```bash
curl http://localhost:8080/api/health
```

```json
{"status": "ok"}
```

#### List Users

```bash
curl -H "X-API-Key: YOUR_TOKEN" http://localhost:8080/api/users
```

```json
[
  {
    "username": "demo",
    "uid": 1000,
    "gid": 1000,
    "home": "/ftp/demo",
    "managed": true
  }
]
```

The `managed` field indicates whether the user was created via the API (`true`) or via the `USERS` environment variable (`false`).

#### Create User

```bash
curl -X POST http://localhost:8080/api/users \
  -H "X-API-Key: YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "client1", "password": "StrongPass123!", "home": "/ftp/client1"}'
```

The `home` field is optional. If omitted, defaults to `/ftp/<username>`.

```json
{"username": "client1", "home": "/ftp/client1"}
```

#### Change Password

```bash
curl -X PUT http://localhost:8080/api/users/client1/password \
  -H "X-API-Key: YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password": "NewPassword456!"}'
```

```json
{"username": "client1", "updated": true}
```

#### Delete User

```bash
curl -X DELETE -H "X-API-Key: YOUR_TOKEN" http://localhost:8080/api/users/client1
```

Response: `204 No Content`

### Integration Examples

#### Python

```python
import requests

BASE = "http://localhost:8080"
HEADERS = {"X-API-Key": "your-token-here"}

# List users
users = requests.get(f"{BASE}/api/users", headers=HEADERS).json()
print(f"Total: {len(users)} users")

# Create user
requests.post(f"{BASE}/api/users", headers=HEADERS, json={
    "username": "new_client",
    "password": "Pass123!",
    "home": "/ftp/new_client"
})

# Change password
requests.put(f"{BASE}/api/users/new_client/password", headers=HEADERS, json={
    "password": "AnotherPass456!"
})

# Delete user
requests.delete(f"{BASE}/api/users/new_client", headers=HEADERS)
```

#### JavaScript (fetch)

```javascript
const BASE = "http://localhost:8080";
const headers = {
  "X-API-Key": "your-token-here",
  "Content-Type": "application/json",
};

// List users
const users = await fetch(`${BASE}/api/users`, { headers }).then(r => r.json());
console.log(`Total: ${users.length} users`);

// Create user
await fetch(`${BASE}/api/users`, {
  method: "POST",
  headers,
  body: JSON.stringify({
    username: "new_client",
    password: "Pass123!",
    home: "/ftp/new_client",
  }),
});

// Change password
await fetch(`${BASE}/api/users/new_client/password`, {
  method: "PUT",
  headers,
  body: JSON.stringify({ password: "AnotherPass456!" }),
});

// Delete user
await fetch(`${BASE}/api/users/new_client`, {
  method: "DELETE",
  headers,
});
```

### Security

- Mandatory token for all routes (except `/api/health`)
- Constant-time token comparison via `hmac.compare_digest` (timing-attack resistant)
- Per-IP rate limiting (30 requests per minute)
- Security headers: `X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`
- Chroot enabled: each FTP user is confined to their home directory
- Dashboard login screen (token stored in sessionStorage, not persisted across sessions)
- HTML output sanitization against XSS

### Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Support

- **Issues**: [GitHub Issues](https://github.com/onflux-tech/vsftpd-manager/issues)
- **Questions**: Open an issue with the "question" label

### License

This project is licensed under the MIT License -- see the [LICENSE](LICENSE) file for details.

---

## Portugues

### Indice

- [Visao Geral](#visao-geral)
- [Principais Recursos](#principais-recursos)
- [Inicio Rapido](#inicio-rapido)
- [Configuracao](#configuracao-1)
- [Referencia da API](#referencia-da-api)
- [Exemplos de Integracao](#exemplos-de-integracao)
- [Seguranca](#seguranca)
- [Contribuindo](#contribuindo-1)
- [Suporte](#suporte)
- [Licenca](#licenca)

### Visao Geral

vsftpd-manager e um servidor FTP containerizado e pronto para producao, construido sobre **vsftpd** (Alpine Linux) com uma API REST e painel web alimentados por **FastAPI** para gerenciamento de usuarios FTP. Os usuarios sao persistidos em arquivo JSON e sobrevivem a recriacoes do container. Cada usuario e confinado (chroot) ao seu proprio diretorio.

### Principais Recursos

- **Servidor FTP vsftpd** -- Leve e seguro sobre Alpine Linux
- **API REST** -- CRUD completo para gerenciamento de usuarios via FastAPI
- **Painel Web** -- Interface integrada com modo escuro/claro para gerenciar usuarios
- **Usuarios Persistentes** -- Persistencia em JSON entre reinicializacoes do container
- **Chroot por Usuario** -- Cada usuario fica restrito ao seu diretorio home
- **Autenticacao por Token** -- API protegida com comparacao de token em tempo constante
- **Rate Limiting** -- Limitacao de requisicoes por IP em memoria (30 req/min)
- **Headers de Seguranca** -- X-Frame-Options, CSP, protecao XSS e mais
- **Pronto para Traefik** -- Labels incluidas para HTTPS via Traefik + Cloudflare
- **Multi-arch** -- Imagens Docker construidas para linux/amd64 e linux/arm64

### Inicio Rapido

#### Usando a imagem pre-construida (recomendado)

```bash
docker pull ghcr.io/onflux-tech/vsftpd-manager:latest
```

#### Usando Docker Compose

```yaml
services:
  ftp-server:
    image: ghcr.io/onflux-tech/vsftpd-manager:latest
    container_name: ftp-server
    restart: unless-stopped
    environment:
      USERS: "admin|SenhaForte123|/ftp/admin"
      ADMIN_TOKEN: troque-por-um-token-forte
      ADDRESS: ftp.exemplo.com
      MIN_PORT: 50000
      MAX_PORT: 50100
      MANAGER_PORT: 8080
      TZ: America/Sao_Paulo
    volumes:
      - ./ftp-data:/ftp
      - ./data:/data
    ports:
      - "21:21"
      - "50000-50100:50000-50100"
      - "8080:8080"
```

```bash
docker compose up -d
```

Acesse o painel web em `http://localhost:8080`.

#### Construindo a partir do codigo-fonte

```bash
git clone https://github.com/onflux-tech/vsftpd-manager.git
cd vsftpd-manager
docker compose up -d --build
```

### Configuracao

#### Variaveis de Ambiente

| Variavel | Padrao | Descricao |
|----------|--------|-----------|
| `USERS` | (vazio) | Usuarios iniciais no formato `nome\|senha\|home` separados por espaco |
| `ADMIN_TOKEN` | `changeme` | Token de autenticacao da API e painel |
| `ADDRESS` | (vazio) | Hostname para endereco passivo do FTP |
| `MIN_PORT` | `50000` | Porta minima do range passivo |
| `MAX_PORT` | `50100` | Porta maxima do range passivo |
| `MANAGER_PORT` | `8080` | Porta da API/painel web |
| `TZ` | `America/Sao_Paulo` | Timezone do container |

#### Portas

| Porta | Servico |
|-------|---------|
| 21 | FTP (vsftpd) |
| 50000-50100 | FTP range passivo |
| 8080 | API REST + Painel web |

#### Estrutura do Projeto

```
.
├── Dockerfile            # Build multi-stage (pidproxy + vsftpd + Python venv)
├── docker-compose.yml    # Orquestracao com volumes e labels opcionais do Traefik
├── manager_api.py        # API REST + dashboard HTML (FastAPI/Uvicorn)
├── start_vsftpd.sh       # Entrypoint: cria usuarios env + restaura JSON + inicia vsftpd
├── vsftpd.conf           # Configuracao do vsftpd
└── data/
    └── users.json        # Base de usuarios persistida (criado automaticamente)
```

### Referencia da API

Todas as rotas da API (exceto `/api/health`) exigem autenticacao via:

```
X-API-Key: <ADMIN_TOKEN>
```

ou

```
Authorization: Bearer <ADMIN_TOKEN>
```

#### Verificar Saude

```bash
curl http://localhost:8080/api/health
```

```json
{"status": "ok"}
```

#### Listar Usuarios

```bash
curl -H "X-API-Key: SEU_TOKEN" http://localhost:8080/api/users
```

```json
[
  {
    "username": "demo",
    "uid": 1000,
    "gid": 1000,
    "home": "/ftp/demo",
    "managed": true
  }
]
```

O campo `managed` indica se o usuario foi criado pela API (`true`) ou pela variavel de ambiente `USERS` (`false`).

#### Criar Usuario

```bash
curl -X POST http://localhost:8080/api/users \
  -H "X-API-Key: SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "cliente1", "password": "SenhaForte123!", "home": "/ftp/cliente1"}'
```

O campo `home` e opcional. Se omitido, sera `/ftp/<username>`.

```json
{"username": "cliente1", "home": "/ftp/cliente1"}
```

#### Alterar Senha

```bash
curl -X PUT http://localhost:8080/api/users/cliente1/password \
  -H "X-API-Key: SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password": "NovaSenha456!"}'
```

```json
{"username": "cliente1", "updated": true}
```

#### Excluir Usuario

```bash
curl -X DELETE -H "X-API-Key: SEU_TOKEN" http://localhost:8080/api/users/cliente1
```

Resposta: `204 No Content`

### Exemplos de Integracao

#### Python

```python
import requests

BASE = "http://localhost:8080"
HEADERS = {"X-API-Key": "seu-token-aqui"}

# Listar usuarios
users = requests.get(f"{BASE}/api/users", headers=HEADERS).json()
print(f"Total: {len(users)} usuarios")

# Criar usuario
requests.post(f"{BASE}/api/users", headers=HEADERS, json={
    "username": "novo_cliente",
    "password": "Senha123!",
    "home": "/ftp/novo_cliente"
})

# Alterar senha
requests.put(f"{BASE}/api/users/novo_cliente/password", headers=HEADERS, json={
    "password": "OutraSenha456!"
})

# Excluir usuario
requests.delete(f"{BASE}/api/users/novo_cliente", headers=HEADERS)
```

#### JavaScript (fetch)

```javascript
const BASE = "http://localhost:8080";
const headers = {
  "X-API-Key": "seu-token-aqui",
  "Content-Type": "application/json",
};

// Listar usuarios
const users = await fetch(`${BASE}/api/users`, { headers }).then(r => r.json());
console.log(`Total: ${users.length} usuarios`);

// Criar usuario
await fetch(`${BASE}/api/users`, {
  method: "POST",
  headers,
  body: JSON.stringify({
    username: "novo_cliente",
    password: "Senha123!",
    home: "/ftp/novo_cliente",
  }),
});

// Alterar senha
await fetch(`${BASE}/api/users/novo_cliente/password`, {
  method: "PUT",
  headers,
  body: JSON.stringify({ password: "OutraSenha456!" }),
});

// Excluir usuario
await fetch(`${BASE}/api/users/novo_cliente`, {
  method: "DELETE",
  headers,
});
```

### Seguranca

- Token de acesso obrigatorio para todas as rotas (exceto `/api/health`)
- Comparacao de token com `hmac.compare_digest` (resistente a timing attacks)
- Rate limiting por IP (30 requisicoes por minuto)
- Headers de seguranca: `X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`
- Chroot habilitado: cada usuario FTP fica restrito ao seu diretorio home
- Painel web com tela de login (token em sessionStorage, nao persiste entre sessoes)
- Sanitizacao de saida HTML contra XSS

### Contribuindo

Contribuicoes sao bem-vindas! Siga os passos abaixo:

1. Faca um fork do repositorio
2. Crie sua branch de feature (`git checkout -b feature/funcionalidade-incrivel`)
3. Faca commit das suas mudancas (`git commit -m 'Adiciona funcionalidade incrivel'`)
4. Faca push para a branch (`git push origin feature/funcionalidade-incrivel`)
5. Abra um Pull Request

### Suporte

- **Issues**: [GitHub Issues](https://github.com/onflux-tech/vsftpd-manager/issues)
- **Duvidas**: Abra uma issue com a label "question"

### Licenca

Este projeto esta licenciado sob a Licenca MIT -- veja o arquivo [LICENSE](LICENSE) para detalhes.

---

Made with ❤️ by the [OnFlux Tech](https://github.com/onflux-tech) team.

Star ⭐ this repository if you find it helpful!
