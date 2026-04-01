# PrintScan

Self-hosted web interface for printing and scanning via CUPS and SANE.  
Built for network printers like the Epson XP-2155.

## Quick Start

1. **Find your printer's IP** (check your DHCP leases or pfSense):
   ```bash
   avahi-browse -rt _ipp._tcp
   ```

2. **Edit `docker-compose.yml`** — replace `192.168.86.X` with your printer's IP in both `PRINTER_URI` and `SCANNER_HOST`.

3. **Build and run:**
   ```bash
   docker compose up -d --build
   ```

4. **Open** `http://<host>:8400`

## Configuration

| Variable | Description | Example |
|----------|-------------|---------|
| `PRINTER_URI` | IPP URI of your printer | `ipp://192.168.86.50:631/ipp/print` |
| `PRINTER_NAME` | Display name in CUPS | `Epson-XP2155` |
| `SCANNER_HOST` | IP for SANE network scanning | `192.168.86.50` |
| `SCAN_DIR` | Path inside container for scans | `/app/scans` |

### Finding the IPP URI

For Epson network printers, the URI is usually one of:
- `ipp://<IP>:631/ipp/print`
- `ipp://<IP>/ipp/print`

You can also check the printer's built-in web UI at `http://<IP>` for the exact path.

### CUPS Admin

If you need to manually configure the printer (driver selection, options), uncomment the `631:631` port mapping in `docker-compose.yml` and visit `http://<host>:631`.

## Caddy + Authelia

Add to your Caddyfile:

```
printscan.sanxer.dk {
    forward_auth authelia:9091 {
        uri /api/authz/forward-auth
        copy_headers Remote-User Remote-Groups Remote-Email
    }
    reverse_proxy printscan:8400
}
```

And add to the `printscan` service in docker-compose:

```yaml
networks:
  - printscan_network
  - caddy_network  # your existing Caddy network
```

## Features

- **Scan** — color/grayscale/lineart, 75-1200 DPI, PNG/JPEG/TIFF
- **Print** — upload PDF/images/text, choose copies, duplex, pages, color/mono
- **History** — browse, download, delete saved scans
- **Status** — printer/scanner status, CUPS/SANE health

## Volumes

| Volume | Purpose |
|--------|---------|
| `printscan_scans` | Persisted scan output files |

## Troubleshooting

### Scanner not found
- Verify the printer IP is reachable: `ping 192.168.86.X`
- Some Epson models need `epsonds` backend. Check inside the container:
  ```bash
  docker exec -it printscan scanimage -L
  ```
- For the XP-2155, try adding to `/etc/sane.d/epsonds.conf` inside the container:
  ```
  net 192.168.86.X
  ```

### Printer not added
- Check CUPS logs: `docker exec -it printscan cat /var/log/cups/error_log`
- Try adding manually via CUPS web UI at `:631`
- The `everywhere` driver (driverless IPP) works with most modern Epson printers

### Permission issues
- If using USB: uncomment the `devices` line in docker-compose
- The container runs as root (needed for CUPS daemon) — mitigated by `no-new-privileges`
