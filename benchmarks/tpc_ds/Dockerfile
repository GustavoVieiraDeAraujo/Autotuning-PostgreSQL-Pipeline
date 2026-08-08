FROM postgres:17

ARG TPCDS_SCALE=1
ENV TPCDS_SCALE=${TPCDS_SCALE}
ENV POSTGRES_DB=tpcds
ENV POSTGRES_USER=postgres
ENV POSTGRES_PASSWORD=postgres

# Move PGDATA fora do volume para que "docker commit" capture os dados
ENV PGDATA=/var/lib/postgresql/pgdata

# Dependências de compilação: gcc, make, flex, bison necessários para o dsdgen
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git ca-certificates flex bison \
    && rm -rf /var/lib/apt/lists/*

# Clona o tpcds-kit (Databricks fork) e compila o dsdgen.
# CFLAGS deve incluir -DLINUX explicitamente: ao sobrescrever CFLAGS na linha
# de comando do make, o -D$(OS) do Makefile original é perdido, e sem -DLINUX
# o porting.h não define HUGE_TYPE/ds_key_t causando erro de compilação.
# -w suprime todos os warnings de compatibilidade do GCC moderno (>=12).
RUN git clone https://github.com/databricks/tpcds-kit /opt/tpcds \
    && cd /opt/tpcds/tools \
    && make OS=LINUX CFLAGS="-DLINUX -g -O -w -std=gnu89 -fcommon" \
    && chown -R postgres:postgres /opt/tpcds

COPY init_tpcds.sh /docker-entrypoint-initdb.d/01_init_tpcds.sh
RUN chmod +x /docker-entrypoint-initdb.d/01_init_tpcds.sh
