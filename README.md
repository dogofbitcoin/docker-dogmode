# docker-dogmode

DOG Mode `bitcoind` as a Docker image, built from source at one pinned commit of
[bitcoindogmode/bitcoin](https://github.com/bitcoindogmode/bitcoin). Made for the Umbrel package
maintained by the Dog of Bitcoin Foundation. Not an official release of the DOG Mode project.

Why from source: the DOG Mode project has not cut a release yet, so there is no tarball to
download and no signature to verify. This image compiles the client itself and says so. The
`Dockerfile` names the repository and the exact commit (checked after checkout), builds the
dependencies with the repository's own `depends` system (static, the way release binaries are
made), and prints the binaries' SHA256 sums, which also ship inside the image at `/SHA256SUMS`.
When the project publishes a signed release, this image switches to download-and-verify like
`getumbrel/docker-bitcoind`.

Built by GitHub Actions on native amd64 and arm64 runners; one tag for both architectures.
Pin the manifest-list digest, printed at the end of each run, wherever the image is consumed.

| | |
|---|---|
| Commit | `75032400914250c7ad857dc29043761680a66485` (the merge of DOG Mode pull request 3 into `31.1-dogmode`) |
| Base | Bitcoin Core 31.1 plus the DOG Mode relay policy (3,900,000 WU standard transactions, a global 1 sat dust limit, preferential peering on service bit 14) |
| Binaries | `bitcoind`, `bitcoin-cli`, `bitcoin`, `bitcoin-node` (the multiprocess node, for the IPC interface) |
| Contact | contact@dogofbitcoin.org |

## Try it without touching mainnet

For a first run, follow the [local Docker testing guide](doc/local-docker-testing.md), contributed by
J25dunn (#2). It uses regtest with networking disabled and a Docker volume of its own, and covers digest
pinning, CLI access, shutdown, persistence, cleanup, and where wallet or Qt changes belong.

## Run a real node

> **This joins Bitcoin mainnet.** The node downloads and verifies the entire chain, which needs well over
> 600 GB of disk and can take days, and it accepts peers on port 8333. The container runs as root, so on a
> Linux host the files it writes into the directory you mount are owned by root. Stop any other node that
> uses the same directory first: two nodes on one data directory will corrupt it.

```sh
docker run --name dogmode -v $HOME/.bitcoin:/data/.bitcoin -p 8333:8333 ghcr.io/dogofbitcoin/bitcoin:31.1-dogmode-7503240
```

This mounts `$HOME/.bitcoin`, Bitcoin Core's usual data directory, so a chain Bitcoin Core already synced can
be reused while Bitcoin Core is stopped. For a node of its own, mount a new directory instead.

## Check it yourself: rebuild and compare

The sums inside an image only show that its programs match the sums shipped beside them; anyone able to
change the programs could change that file too. The independent check is to build this repository yourself
and compare your sums with the published ones below.

Builds of this commit have reproduced. Three builds produced identical sums for all four programs on both
architectures: the two published on 2026-09-05 (read from `/SHA256SUMS` inside each published image) and one
on 2026-09-14 (read from its build log).

| program | amd64 | arm64 |
|---|---|---|
| `bitcoind` | `388bc5d54a728b8aae8a923a6f7a515c5a42a49b48d0001ceb3057a74ae17411` | `1a8e4ea9f1744280749a276f6d613a912cc1ab4ee1d3bc7934394171a48c3dc5` |
| `bitcoin-cli` | `420eafae1ee9c79d368f98bf1843d0a00e2a175dd45eac388272755059aac210` | `1de60ed69ab5bdb17b6c4fccc6d7fe728a6ef9a4b626194f5fe517d4445bdf4b` |
| `bitcoin` | `186559caaf054c6ece5708be4a285e677286d0224ccd78a6fd9032975898aa2c` | `62d9cf0e1bccfd25c51b1a93798549a05d7f93a7f17654060b75ef941995bcc9` |
| `bitcoin-node` | `2be9baf942a28d45091a459d3b6e767376b56e7b7e6a81830fe40e5cf0bf41b4` | `264e4799a8106b6cc9fe690f7a0ef36b39dfa9ce7b5fafeeb9747a1128538059` |

Compare program sums, not image digests. The image digest changes from build to build because the layers
record build times, even when every program is identical. That happened to `31.1-dogmode-7503240`: it was
published twice on 2026-09-05, first as manifest list
`sha256:d2a6fb8d52457bcf1b5caa30be4f0b4a7ed24ac0880090cd4086a5fdf7355e21` (the one the Umbrel package pins),
then as `sha256:70c5c5823dddaafcf38d1f8c331239a29fc008358efb651da1942734127c3dac`, with the same programs inside.

The workflow publishes this commit as `31.1-dogmode-7503240-r2`: the same programs, with their license files
added (below).

## Credit and licenses

DOG Mode is the DOG Mode project's client ([bitcoindogmode/bitcoin](https://github.com/bitcoindogmode/bitcoin)),
built on Bitcoin Core by the Bitcoin Core developers. This image only compiles it, is maintained by the Dog of
Bitcoin Foundation, and is not an official DOG Mode release.

From `31.1-dogmode-7503240-r2` on, inside the image at `/usr/share/doc/dogmode/`:

| | |
|---|---|
| `COPYING` | Bitcoin Core's MIT license, which covers the client |
| `licenses/source-tree/` | the license files of the libraries bundled in the client's own source tree and built into the binaries, by their path there: leveldb and crc32c (BSD-3-Clause), secp256k1, minisketch, ctaes and libmultiprocess (MIT), each with its own copyright notice |
| `licenses/depends/` | the license files at the top of each archive the build's `depends` step used (boost, libevent, ZeroMQ, Cap'n Proto), read by `licenses.py` from the exact archives the build checked by hash; an archive with none, like SQLite's (public domain), gets a note saying so |
| `SOURCES` | those archives' names and SHA-256 hashes, so anyone can fetch the identical sources (ZeroMQ's MPL-2.0 asks for this); `src-ipc-libmultiprocess.tar` is `depends`' own tarball of a directory in the client's tree, so its hash is the build's |
| `BUILD` | the repository and commit the client was built from |

The `Dockerfile`, the workflow, `licenses.py` and this README are MIT licensed; see `LICENSE`. The testing guide
in `doc/` is J25dunn's contribution (#2).

## Testing changes

Pull requests build and smoke-test the image on native amd64 and arm64 runners
without publishing it. Pushed tags and manual runs in the upstream repository
publish each architecture only after its smoke test passes, then assemble the
combined image tag. Forks can run the checks without publishing upstream images.

To run the same checks locally (Docker is required):

```sh
docker build -t dogmode:test .
bash test/smoke.sh dogmode:test
```

The test checks binary checksums and the four advertised executables, starts an
isolated regtest node, verifies the DOG_MODE service flag, generates three blocks
through RPC, shuts down cleanly, and recreates the container through
`bitcoin -m node` to check the launcher, multiprocess node, and persisted chain.
It creates and removes its own Docker volume, has no network access, and does not
use your existing Bitcoin data. On failure it prints the node logs before cleanup.

This is a packaging smoke test, not the upstream unit/functional suite or a full
multiprocess IPC integration test.
