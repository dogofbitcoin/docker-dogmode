#!/usr/bin/env bash
# Exercise the shipped image without public networking or an existing Bitcoin datadir.
set -euo pipefail

image=${1:?Usage: test/smoke.sh IMAGE}
name="dogmode-smoke-$$-${RANDOM}"
volume="${name}-data"

cleanup() {
    status=$?
    trap - EXIT
    if (( status != 0 )) && docker inspect "$name" >/dev/null 2>&1; then
        docker logs "$name" >&2 || true
    fi
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker volume create "$volume" >/dev/null
# Checksums are relative to the directory containing the installed binaries.
docker run --rm --network none --entrypoint sh "$image" -ec '
    cd /bin
    sha256sum -c /SHA256SUMS
    for binary in bitcoind bitcoin-cli bitcoin bitcoin-node; do
        test -x "/bin/$binary"
        "$binary" --version
    done
'

cli() {
    docker exec "$name" bitcoin-cli -regtest -rpcclienttimeout=5 "$@"
}

wait_for_rpc() {
    for (( attempt=0; attempt<60; attempt++ )); do
        if cli getblockcount >/dev/null 2>&1; then
            return 0
        fi
        if [[ $(docker inspect --format '{{.State.Running}}' "$name") != true ]]; then
            echo 'Node exited before RPC became ready' >&2
            return 1
        fi
        sleep 1
    done
    echo 'RPC did not become ready within 60 seconds' >&2
    return 1
}

start_node() {
    docker run -d --name "$name" --network none \
        -v "$volume:/data/.bitcoin" "$@" \
        -regtest -server -listen=0 -dnsseed=0 -discover=0 -connect=0 \
        -disablewallet -printtoconsole >/dev/null
    wait_for_rpc
}

stop_node() {
    cli stop
    for (( attempt=0; attempt<60; attempt++ )); do
        if [[ $(docker inspect --format '{{.State.Running}}' "$name") == false ]]; then
            test "$(docker inspect --format '{{.State.ExitCode}}' "$name")" = 0
            return 0
        fi
        sleep 1
    done
    echo 'Node did not stop within 60 seconds' >&2
    return 1
}

start_node "$image"
test "$(cli getblockcount)" = 0
# OP_TRUE avoids requiring a wallet or importing keys just to mine regtest blocks.
cli generatetodescriptor 3 'raw(51)' >/dev/null
test "$(cli getblockcount)" = 3
block_hash=$(cli getbestblockhash)
network_info=$(cli getnetworkinfo)
printf '%s\n' "$network_info"
if [[ "$network_info" != *'"DOG_MODE"'* ]]; then
    echo 'Node does not advertise DOG_MODE' >&2
    exit 1
fi
stop_node
# Recreate the container to prove the named volume, not its writable layer, holds data.
docker rm "$name" >/dev/null
# Also exercise the shipped launcher and multiprocess node.
start_node --entrypoint bitcoin "$image" -m node
test "$(cli getblockcount)" = 3
test "$(cli getbestblockhash)" = "$block_hash"
stop_node
printf 'Smoke tests passed for %s\n' "$image"
