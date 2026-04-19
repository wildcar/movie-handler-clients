# Deployment (systemd, single host)

Both services run side by side on the same Ubuntu host. The Telegram bot talks
to the metadata MCP over localhost HTTP — no public exposure of the MCP
endpoint is needed.

## Layout

```
/opt/movie-handler/           # home dir of user 'movie', holds update.sh + ~/.local/bin/uv
/opt/movie-metadata-mcp/      # cloned repo + .venv  (owned by movie)
/opt/movie-handler-clients/   # cloned repo + .venv  (owned by movie)
/etc/movie-handler/
    metadata.env              # TMDB / OMDb / poiskkino keys, MCP_AUTH_TOKEN
    telegram.env              # TELEGRAM_BOT_TOKEN, MCP_AUTH_TOKEN, MOVIE_METADATA_MCP_URL
```

## First-time setup

The repositories are private. The `movie` user needs read access to both —
either via a deploy key per repo (SSH URL) or via a fine-scoped personal
access token stored in `~movie/.git-credentials`.

```bash
# 1. System user with /opt/movie-handler as its home directory.
sudo useradd -r -m -d /opt/movie-handler -s /bin/bash movie
sudo install -d -o root -g movie -m 0750 /etc/movie-handler

# 2. Install uv for the movie user (lands in ~movie/.local/bin/uv).
sudo -u movie bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'

# 3. Clone both repositories as user movie.
sudo -u movie git clone https://github.com/wildcar/movie-metadata-mcp.git    /opt/movie-metadata-mcp
sudo -u movie git clone https://github.com/wildcar/movie-handler-clients.git /opt/movie-handler-clients

# 4. Create a .venv with pinned deps inside each repo.
sudo -u movie bash -lc 'cd /opt/movie-metadata-mcp    && ~/.local/bin/uv sync --frozen'
sudo -u movie bash -lc 'cd /opt/movie-handler-clients && ~/.local/bin/uv sync --frozen'

# 5. Drop the env files (root-owned, readable only by group movie).
sudo install -m 0640 -o root -g movie /dev/null /etc/movie-handler/metadata.env
sudo install -m 0640 -o root -g movie /dev/null /etc/movie-handler/telegram.env
sudoedit /etc/movie-handler/metadata.env    # fill in TMDB/OMDb/poiskkino + MCP_AUTH_TOKEN
sudoedit /etc/movie-handler/telegram.env    # fill in TELEGRAM_BOT_TOKEN + the same MCP_AUTH_TOKEN
```

`/bin/bash` as the shell (instead of `nologin`) keeps the `-lc` invocations
above working and is required for the `update.sh` automation below.

## Install the services

```bash
sudo cp /opt/movie-handler-clients/deploy/movie-metadata-mcp.service      /etc/systemd/system/
sudo cp /opt/movie-handler-clients/deploy/movie-handler-telegram.service  /etc/systemd/system/
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
# /opt/movie-handler already exists as the movie user's home dir (see First-time setup).
sudo install -o movie -g movie -m 0750 \
    /opt/movie-handler-clients/deploy/update.sh \
    /opt/movie-handler/update.sh

sudo -u movie /opt/movie-handler/update.sh           # pull + uv sync
sudo -u movie /opt/movie-handler/update.sh --restart # also restart services
```

`--restart` needs a narrow sudoers entry for the `movie` user (see the
header of `update.sh`). Restarting services requires root, so the script
never does it implicitly.

Runs can be automated with a systemd timer — create
`/etc/systemd/system/movie-handler-update.service` (`User=movie`,
`ExecStart=/opt/movie-handler/update.sh`) plus a matching `.timer`.
