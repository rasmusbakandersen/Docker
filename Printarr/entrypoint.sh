#!/bin/bash
set -e

# ── Start dbus (required by cups) ──
if [ ! -d /run/dbus ]; then
  mkdir -p /run/dbus
fi
dbus-daemon --system --nofork &
sleep 1

# ── Configure CUPS ──
if [ ! -f /etc/cups/cupsd.conf.bak ]; then
  cp /etc/cups/cupsd.conf /etc/cups/cupsd.conf.bak 2>/dev/null || true
fi

cat > /etc/cups/cupsd.conf << 'CUPSCONF'
LogLevel warn
MaxLogSize 0
Listen 0.0.0.0:631
Listen /run/cups/cups.sock
Browsing On
BrowseLocalProtocols dnssd
DefaultAuthType Basic
WebInterface Yes

<Location />
  Order allow,deny
  Allow all
</Location>
<Location /admin>
  Order allow,deny
  Allow all
</Location>
<Location /admin/conf>
  Order allow,deny
  Allow all
</Location>
<Policy default>
  <Limit Send-Document Send-URI Hold-Job Release-Job Restart-Job Purge-Jobs Set-Job-Attributes Create-Job-Subscription Renew-Subscription Cancel-Subscription Get-Notifications Reprocess-Job Cancel-Current-Job Suspend-Current-Job Resume-Job Cancel-My-Jobs Close-Job CUPS-Move-Job CUPS-Get-Document>
    Order deny,allow
    Allow all
  </Limit>
  <Limit CUPS-Add-Modify-Printer CUPS-Delete-Printer CUPS-Add-Modify-Class CUPS-Delete-Class CUPS-Set-Default CUPS-Get-Devices>
    AuthType Basic
    Require user @SYSTEM
    Order deny,allow
    Allow all
  </Limit>
  <Limit All>
    Order deny,allow
    Allow all
  </Limit>
</Policy>
CUPSCONF

# Start CUPS daemon
/usr/sbin/cupsd
echo "Waiting for CUPS..."
for i in $(seq 1 10); do
  if lpstat -r 2>/dev/null | grep -q "running"; then
    echo "CUPS is running."
    break
  fi
  sleep 1
done

# ── Auto-detect USB printer and add to CUPS ──
PNAME="${PRINTER_NAME:-Epson-XP2155}"
echo "Searching for USB printer..."

USB_URI=""
for i in $(seq 1 5); do
  USB_URI=$(lpinfo -v 2>/dev/null | grep "^direct usb://" | grep -i "epson" | head -1 | awk '{print $2}')
  if [ -n "${USB_URI}" ]; then
    break
  fi
  echo "  Attempt $i: USB printer not found yet, waiting..."
  sleep 2
done

if [ -n "${USB_URI}" ]; then
  echo "Found USB printer: ${USB_URI}"

  PPD=$(lpinfo -m 2>/dev/null | grep -i "xp-2100\|xp-2150\|xp-2155" | head -1 | awk '{print $1}')

  if [ -n "${PPD}" ]; then
    echo "Using driver: ${PPD}"
    lpadmin -p "${PNAME}" -E -v "${USB_URI}" -m "${PPD}" 2>/dev/null || true
  else
    echo "No specific PPD found, using generic driver"
    lpadmin -p "${PNAME}" -E -v "${USB_URI}" -m "drv:///sample.drv/generic.ppd" 2>/dev/null || true
  fi

  lpadmin -p "${PNAME}" -o usb-unidir-default=true 2>/dev/null || true
  lpadmin -d "${PNAME}" 2>/dev/null || true
  echo "Printer ${PNAME} configured as default."
else
  echo "Warning: No USB printer found. Add it manually via CUPS at :631"
fi

# ── Configure SANE ──
if ! grep -q "^epsonds" /etc/sane.d/dll.conf 2>/dev/null; then
  echo "epsonds" >> /etc/sane.d/dll.conf
fi
if ! grep -q "^epson2" /etc/sane.d/dll.conf 2>/dev/null; then
  echo "epson2" >> /etc/sane.d/dll.conf
fi

echo ""
echo "══════════════════════════════════════════"
echo "  Printarr ready on http://0.0.0.0:8400"
echo "  CUPS admin UI on http://0.0.0.0:631"
echo "══════════════════════════════════════════"
echo ""

# ── Start FastAPI ──
exec uvicorn app:app --host 0.0.0.0 --port 8400 --log-level info
