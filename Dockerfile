# Minimal demo container.
# Listens on :8080. For every inbound HTTP request, it proxies the same
# path+query to http://acme-products.internal (a *virtual* hostname that
# only exists inside this container's network namespace — outbound traffic
# is intercepted by the Worker and routed through Workers VPC into the
# cloudflared tunnel to the private API).
FROM python:3.12-slim

WORKDIR /app

COPY app.py /app/app.py

EXPOSE 8080

CMD ["python", "-u", "/app/app.py"]
