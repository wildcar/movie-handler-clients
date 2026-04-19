#!/usr/bin/env bash
# Update both movie_handler checkouts from their remotes and re-sync deps.
#
# Install: copy this script to /opt/movie-handler/update.sh, chown to movie,
# chmod 0750. Run manually or from a systemd .timer under User=movie.
#
#   sudo install -d -o movie -g movie -m 0750 /opt/movie-handler
#   sudo install -o movie -g movie -m 0750 deploy/update.sh /opt/movie-handler/update.sh
#
# Restarting the two services requires root, so this script does NOT do it.
# Either run `sudo systemctl restart ...` afterwards, or add a narrow sudoers
# rule for the movie user:
#
#   movie ALL=(root) NOPASSWD: /bin/systemctl restart movie-metadata-mcp.service movie-handler-telegram.service
#
# and pass --restart to let the script perform it.

set -euo pipefail

REPOS=(
    /opt/movie-metadata-mcp
    /opt/movie-handler-clients
)
SERVICES=(
    movie-metadata-mcp.service
    movie-handler-telegram.service
)

BRANCH="${BRANCH:-main}"
RESTART=0

# Resolve uv: explicit UV env var > PATH > common install locations.
if [[ -n "${UV:-}" ]]; then
    :
elif UV="$(command -v uv 2>/dev/null)" && [[ -n "$UV" ]]; then
    :
else
    for candidate in \
        "$HOME/.local/bin/uv" \
        /usr/local/bin/uv \
        /usr/bin/uv \
        /opt/uv/bin/uv
    do
        if [[ -x "$candidate" ]]; then
            UV="$candidate"
            break
        fi
    done
fi

for arg in "$@"; do
    case "$arg" in
        --restart) RESTART=1 ;;
        -h|--help)
            sed -n '1,20p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

if [[ "$(id -un)" != "movie" ]]; then
    echo "must run as user 'movie' (current: $(id -un))" >&2
    exit 1
fi

if [[ -z "${UV:-}" ]] || ! [[ -x "$UV" ]]; then
    echo "uv not found — install it for user 'movie' (e.g. 'curl -LsSf https://astral.sh/uv/install.sh | sh')" >&2
    echo "or set UV=/path/to/uv before running this script" >&2
    exit 1
fi

ts() { date --utc +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '[%s] %s\n' "$(ts)" "$*"; }

update_repo() {
    local dir="$1"
    log "updating $dir"
    if [[ ! -d "$dir/.git" ]]; then
        log "  skip: $dir is not a git checkout"
        return 1
    fi

    local before after
    before="$(git -C "$dir" rev-parse HEAD)"

    git -C "$dir" fetch --quiet --prune origin
    git -C "$dir" checkout --quiet "$BRANCH"
    git -C "$dir" reset --quiet --hard "origin/$BRANCH"

    after="$(git -C "$dir" rev-parse HEAD)"

    if [[ "$before" == "$after" ]]; then
        log "  already up to date ($after)"
        return 0
    fi
    log "  $before → $after"

    (cd "$dir" && "$UV" sync --frozen --no-dev)
    log "  deps synced"
}

rc=0
for repo in "${REPOS[@]}"; do
    if ! update_repo "$repo"; then
        rc=1
    fi
done

if [[ "$RESTART" -eq 1 ]]; then
    log "restarting services"
    sudo -n systemctl restart "${SERVICES[@]}"
    log "  restarted: ${SERVICES[*]}"
fi

exit "$rc"
