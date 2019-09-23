FROM node:10 as frontend-builder

WORKDIR /frontend
COPY package.json package-lock.json /frontend/
RUN npm install

COPY client /frontend/client
COPY webpack.config.js /frontend/
RUN npm run build

FROM python:3.6-slim

EXPOSE 5000

RUN useradd --create-home redash

# Ubuntu packages
RUN apt-get update && \
  apt-get install -y \
    curl \
    gnupg \
    build-essential \
    pwgen \
    libffi-dev \
    sudo \
    git-core \
    wget \
    # Postgres client
    libpq-dev \
    # for SAML
    xmlsec1 \
    # Additional packages required for data sources:
    libssl-dev \
    default-libmysqlclient-dev \
    freetds-dev \
    libsasl2-dev && \
  apt-get clean && \
  rm -rf /var/lib/apt/lists/*

WORKDIR /app


# Controls whether to install extra dependencies needed for all data sources.
ARG skip_ds_deps
ARG skip_override_pip_conf


COPY . /app
COPY --from=frontend-builder /frontend/client/dist /tmp/dist

RUN if [ "x$skip_override_pip_conf" = "x" ] ; then mkdir -p /root/.pip && cp -f pip.conf /root/.pip/pip.conf ; else echo "Skipping override pip conf" ; fi

# We first copy only the requirements file, to avoid rebuilding on every file
# change.
COPY requirements.txt requirements_bundles.txt requirements_dev.txt requirements_all_ds.txt ./
RUN pip install -r requirements.txt -r requirements_dev.txt
RUN if [ "x$skip_ds_deps" = "x" ] ; then pip install -r requirements_all_ds.txt ; else echo "Skipping pip install -r requirements_all_ds.txt" ; fi

RUN pip install -r requirements_beer.txt

RUN chown -R redash /app
USER redash

ENTRYPOINT ["/app/bin/docker-entrypoint"]
CMD ["server"]
