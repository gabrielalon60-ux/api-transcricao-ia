#!/usr/bin/env bash

set -Eeuo pipefail

# Garante que o script seja executado a partir da raiz do projeto
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

echo "========================================"
echo " Intelligent Document Extraction API"
echo " Instalador"
echo "========================================"
echo

echo "1/6 - Verificando Docker..."

if ! command -v docker >/dev/null 2>&1; then
    echo
    echo "ERRO: Docker não encontrado."
    echo "Instale o Docker antes de continuar."
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo
    echo "ERRO: Docker está instalado, mas não está em execução."
    echo "Inicie o Docker e execute este instalador novamente."
    exit 1
fi

echo "Docker encontrado e em execução."
echo

echo "2/6 - Verificando Docker Compose..."

if ! docker compose version >/dev/null 2>&1; then
    echo
    echo "ERRO: Docker Compose não encontrado."
    echo "Instale o Docker Compose antes de continuar."
    exit 1
fi

echo "Docker Compose encontrado."
echo

echo "3/6 - Criando diretórios persistentes..."

mkdir -p wuzapi/files
mkdir -p wuzapi/dbdata

echo "Diretórios criados ou já existentes."
echo

echo "4/6 - Verificando configuração..."

if [ ! -f ".env" ]; then
    if [ ! -f ".env.example" ]; then
        echo
        echo "ERRO: O arquivo .env.example não foi encontrado."
        exit 1
    fi

    cp .env.example .env

    echo
    echo "Arquivo .env criado com base no .env.example."
    echo
    echo "A instalação foi pausada para configuração."
    echo
    echo "Edite o arquivo:"
    echo
    echo "  .env"
    echo
    echo "Preencha os valores reais e execute novamente:"
    echo
    echo "  ./install.sh"
    echo

    exit 0
fi

echo "Arquivo .env encontrado."
echo

echo "5/6 - Provisionando banco e aplicação inicial..."

docker compose build api-transcricao

docker compose run --rm --no-deps \
    -v "$(pwd)/.env:/app/.env" \
    api-transcricao \
    python scripts/provision.py

echo
echo "Provisionamento concluído."
echo

echo "6/6 - Iniciando os containers..."

docker compose config --quiet
docker compose up -d --build --remove-orphans

echo
echo "Estado dos containers:"
echo

docker compose ps

echo
echo "========================================"
echo " Instalação concluída!"
echo "========================================"
echo
echo "API:"
echo "  http://localhost:8000"
echo
echo "Documentação:"
echo "  http://localhost:8000/docs"
echo
echo "WUZAPI:"
echo "  http://localhost:8080"
echo