FROM postgres:17

ARG TPCH_SCALE=1
ENV TPCH_SCALE=${TPCH_SCALE}
ENV POSTGRES_DB=tpch
ENV POSTGRES_USER=postgres
ENV POSTGRES_PASSWORD=postgres

# A imagem oficial do postgres declara VOLUME /var/lib/postgresql/data, e
# "docker commit" não captura volumes. Mudamos PGDATA para um caminho fora
# do volume para que os dados TPC-H sejam incluídos na imagem commitada.
ENV PGDATA=/var/lib/postgresql/pgdata

# build-essential inclui gcc, cc, make, libc-dev: necessários para compilar o dbgen
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/databricks/tpch-dbgen /opt/tpch \
    && cd /opt/tpch \
    && make CC=gcc \
    && chown -R postgres:postgres /opt/tpch

COPY init_tpch.sh /docker-entrypoint-initdb.d/01_init_tpch.sh
RUN chmod +x /docker-entrypoint-initdb.d/01_init_tpch.sh
