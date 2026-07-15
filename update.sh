#!/usr/bin/env bash

set -Eeuo pipefail

# Garante que o script seja executado a partir da raiz do projeto
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

echo "========================================"
echo " Intelligent Document Extraction API"
echo " Atualização"
echo "========================================"
echo

echo "1/6 - Verificando Docker..."

if ! command -v docker >/dev/null 2>&1; then
    echo
    echo "ERRO: Docker não encontrado."
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo
    echo "ERRO: Docker está instalado, mas não está em execução."
    exit 1
fi

echo "Docker encontrado e em execução."
echo

echo "2/6 - Verificando Docker Compose..."

if ! docker compose version >/dev/null 2>&1; then
    echo
    echo "ERRO: Docker Compose não encontrado."
    exit 1
fi

echo "Docker Compose encontrado."
echo

echo "3/6 - Atualizando o repositório..."

git pull --ff-only origin main

echo
echo "4/6 - Validando e atualizando imagens..."

docker compose config --quiet
docker compose pull

echo
echo "5/6 - Reconstruindo e iniciando os containers..."

docker compose up -d --build --remove-orphans

echo
echo "6/6 - Limpando imagens antigas..."

docker image prune -f

echo
echo "Estado dos containers:"
echo

docker compose ps

echo
echo "========================================"
echo " Atualização concluída com sucesso!"
echo "========================================"