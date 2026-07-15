#!/bin/bash

set -e

echo "Atualizando repositório..."
git pull origin main

echo "Reconstruindo containers..."
docker compose down
docker compose build
docker compose up -d

echo "Deploy concluído!"