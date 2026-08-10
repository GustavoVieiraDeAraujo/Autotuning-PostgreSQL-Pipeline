#!/bin/bash
set -e

SCALE=${TPCH_SCALE:-1}
DIR=/opt/tpch

echo "[tpch] Gerando dados (scale factor: $SCALE)..."
cd "$DIR"
./dbgen -s "$SCALE" -f

echo "[tpch] Removendo pipe final de cada linha nos arquivos .tbl..."
for f in "$DIR"/*.tbl; do
    sed -i 's/|$//' "$f"
done

echo "[tpch] Criando schema..."
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$DIR/dss.ddl"

echo "[tpch] Carregando tabelas..."
for table in region nation supplier customer part partsupp orders lineitem; do
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -c "\COPY ${table} FROM '${DIR}/${table}.tbl' WITH (FORMAT csv, DELIMITER '|')"
    echo "  [ok] $table"
done

echo "[tpch] Criando chaves primárias e estrangeiras..."
# dss.ri contém "CONNECT TO TPCD;" (ESQL/C), inválido no psql: filtra antes de executar
grep -v "^CONNECT" "$DIR/dss.ri" | \
    sed 's/TPCD\.//g' | \
    sed 's/ADD FOREIGN KEY \([A-Z0-9_]*\) (/ADD CONSTRAINT \1 FOREIGN KEY (/g' | \
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"

echo "[tpch] ANALYZE..."
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ANALYZE;"

echo "[tpch] Setup concluído."
