#!/bin/bash

set -e

cd "$(dirname "$0")"

echo "========================================"
echo " Intelligent Document Extraction API"
echo " Instalador"
echo "========================================"

echo
echo "1/5 - Verificando Docker..."

# Verifica Docker
if ! command -v docker &> /dev/null; then
    echo "Docker não encontrado."
    exit 1
fi

echo
echo "2/5 - Verificando Docker Compose..."

# Verifica Docker Compose
if ! docker compose version &> /dev/null; then
    echo "Docker Compose não encontrado."
    exit 1
fi

echo
echo "3/5 - Criando arquivos de configuração..."

# Cria .env da API
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Arquivo .env criado."
fi

echo
echo "4/5 - Criando diretórios..."

# Cria .env do Wuzapi
if [ ! -f "wuzapi/.env" ]; then
    cp wuzapi/.env.example wuzapi/.env
    echo "Arquivo wuzapi/.env criado."
fi

echo
echo "5/5 - Construindo containers..."

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