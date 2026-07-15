#!/bin/bash

set -e

echo "========================================"
echo " Intelligent Document Extraction API"
echo " Instalador"
echo "========================================"

# Verifica Docker
if ! command -v docker &> /dev/null; then
    echo "Docker não encontrado."
    exit 1
fi

# Verifica Docker Compose
if ! docker compose version &> /dev/null; then
    echo "Docker Compose não encontrado."
    exit 1
fi

# Cria .env da API
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Arquivo .env criado."
fi

# Cria .env do Wuzapi
if [ ! -f "wuzapi/.env" ]; then
    cp wuzapi/.env.example wuzapi/.env
    echo "Arquivo wuzapi/.env criado."
fi

mkdir -p wuzapi/files
mkdir -p wuzapi/dbdata

docker compose up -d --build

echo ""
echo "========================================"
echo "Instalação concluída!"
echo "========================================"
echo ""
echo "Agora edite:"
echo ""
echo "  .env"
echo "  wuzapi/.env"
echo ""
echo "Depois execute:"
echo ""
echo "docker compose restart"