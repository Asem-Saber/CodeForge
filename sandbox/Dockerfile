FROM python:3.12-slim

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends tini && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    numpy \
    pandas \
    matplotlib \
    requests \
    beautifulsoup4 \
    scipy \
    scikit-learn \
    pillow \
    tabulate \
    fastapi \
    httpx \
    pytest

RUN groupadd -r sandbox && \
    useradd -r -g sandbox -d /sandbox -s /usr/sbin/nologin sandbox

WORKDIR /sandbox
RUN chown sandbox:sandbox /sandbox

RUN rm -f /usr/bin/wget /usr/bin/curl 2>/dev/null; \
    rm -f /usr/bin/nc /usr/bin/ncat 2>/dev/null; \
    rm -f /usr/bin/apt-get /usr/bin/dpkg 2>/dev/null; \
    true

USER sandbox

ENTRYPOINT ["tini", "--"]
CMD ["sleep", "infinity"]
