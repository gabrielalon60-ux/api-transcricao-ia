#!/bin/bash

set -e

echo "======================================"
echo "Atualizando aplicação..."
echo "======================================"

echo ""
echo "1/4 - Atualizando repositório..."
git pull origin main

echo ""
echo "2/4 - Baixando novas imagens (se houver)..."
docker compose pull

echo ""
echo "3/4 - Recriando apenas os containers alterados..."
docker compose up -d --build

echo ""
echo "4/4 - Limpando imagens antigas..."
docker image prune -f

echo ""
echo "======================================"
echo "Deploy concluído!"
echo "======================================"

docker compose ps