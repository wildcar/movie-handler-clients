# Deployment (systemd, single host)

Both services run side by side on the same Ubuntu host. The Telegram bot talks
to the metadata MCP over localhost HTTP — no public exposure of the MCP
endpoint is needed.

## Layout

```
/opt/movie-metadata-mcp/      # cloned repo + .venv (uv sync --frozen)
/opt/movie-handler-clients/   # cloned repo + .venv (uv sync --frozen)
/etc/movie-handler/
    metadata.env              # TMDB / OMDb / poiskkino keys, MCP_AUTH_TOKEN
    telegram.env              # TELEGRAM_BOT_TOKEN, MCP_AUTH_TOKEN, MOVIE_METADATA_MCP_URL
```

Create the `movie` system user once:

```bash
sudo useradd -r -s /usr/sbin/nologin -d /opt/movie-handler-clients movie
sudo chown -R movie:movie /opt/movie-metadata-mcp /opt/movie-handler-clients
sudo install -d -o root -g movie -m 0750 /etc/movie-handler
```

## Install

```bash
sudo cp deploy/movie-metadata-mcp.service      /etc/systemd/system/
sudo cp deploy/movie-handler-telegram.service  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now movie-metadata-mcp.service
sudo systemctl enable --now movie-handler-telegram.service
```

## Health checks

```bash
systemctl status movie-metadata-mcp.service
systemctl status movie-handler-telegram.service
journalctl -u movie-handler-telegram.service -f
```

`MCP_AUTH_TOKEN` **must be identical** in both env files.

## Updating from git

`deploy/update.sh` pulls both checkouts from their remotes and re-syncs
dependencies. It must run as the `movie` user:

```bash
sudo install -d -o movie -g movie -m 0750 /opt/movie-handler
sudo install -o movie -g movie -m 0750 deploy/update.sh /opt/movie-handler/update.sh

sudo -u movie /opt/movie-handler/update.sh          # pull + uv sync
sudo -u movie /opt/movie-handler/update.sh --restart # also restart services
```

`--restart` needs a narrow sudoers entry for the `movie` user (see the
header of `update.sh`). Restarting services requires root, so the script
never does it implicitly.

Runs can be automated with a systemd timer — create
`/etc/systemd/system/movie-handler-update.service` (`User=movie`,
`ExecStart=/opt/movie-handler/update.sh`) plus a matching `.timer`.
