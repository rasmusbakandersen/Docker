FROM python:3.12-slim-bookworm

# ── System deps: CUPS, SANE, Epson drivers ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-filters \
    sane-utils \
    libsane-dev \
    printer-driver-escpr \
    usbutils \
    procps \
    dbus \
    && rm -rf /var/lib/apt/lists/*

# ── Python app ──
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

# ── Dirs ──
RUN mkdir -p /app/scans /paperless-consume

# ── Startup script ──
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV SCAN_DIR=/app/scans
EXPOSE 8400

ENTRYPOINT ["/entrypoint.sh"]
