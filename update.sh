#!/bin/bash

set -e

# Vai para a pasta onde o script está
cd "$(dirname "$0")"

echo "======================================"
echo " Atualizando aplicação..."
echo "======================================"

echo
echo "1/5 - Atualizando repositório..."
git pull origin main

echo
echo "2/5 - Baixando novas imagens (se houver)..."
docker compose pull

echo
echo "3/5 - Recriando containers..."
docker compose up -d --build

echo
echo "4/5 - Limpando imagens antigas..."
docker image prune -f

echo
echo "5/5 - Containers em execução:"
docker compose ps

echo
echo "======================================"
echo " Deploy concluído com sucesso!"
echo "======================================"