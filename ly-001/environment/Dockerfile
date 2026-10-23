FROM python:3.13.15-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1

COPY wheels/ /tmp/verifier-wheels/
RUN mkdir -p /opt/verifier-site \
    && python3 -m zipfile -e /tmp/verifier-wheels/iniconfig-2.3.1-py3-none-any.whl /opt/verifier-site \
    && python3 -m zipfile -e /tmp/verifier-wheels/packaging-26.3-py3-none-any.whl /opt/verifier-site \
    && python3 -m zipfile -e /tmp/verifier-wheels/pluggy-1.6.0-py3-none-any.whl /opt/verifier-site \
    && python3 -m zipfile -e /tmp/verifier-wheels/pygments-2.19.2-py3-none-any.whl /opt/verifier-site \
    && python3 -m zipfile -e /tmp/verifier-wheels/pytest-8.4.1-py3-none-any.whl /opt/verifier-site \
    && cp -R /opt/verifier-site/. /usr/local/lib/python3.13/site-packages/ \
    && rm -rf /opt/verifier-site \
    && rm -rf /tmp/verifier-wheels

COPY data/ /data/
COPY app/ /app/
COPY generate_data.py /opt/task/generate_data.py

RUN useradd --create-home --uid 10001 auditor \
    && mkdir -p /output /logs/verifier \
    && chown -R auditor:auditor /app /output /logs/verifier \
    && chmod 0755 /app/secondary_heat_audit.py /opt/task/generate_data.py

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
USER auditor
CMD ["sleep", "infinity"]
