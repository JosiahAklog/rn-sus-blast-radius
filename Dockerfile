FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ADD https://codeload.github.com/tobymao/sqlglot/tar.gz/eb11aec4be8cb2122afe713a4816662c3f0fb2fa /tmp/source.tar.gz
RUN mkdir /opt/sqlglot && tar -xzf /tmp/source.tar.gz --strip-components=1 -C /opt/sqlglot && rm /tmp/source.tar.gz
WORKDIR /app
COPY selector.py classes.json.gz map.json.gz ./
CMD ["python", "selector.py"]
