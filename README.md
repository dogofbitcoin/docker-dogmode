# docker-dogmode

DOG Mode `bitcoind` as a Docker image, built from source at one pinned commit of
[bitcoindogmode/bitcoin](https://github.com/bitcoindogmode/bitcoin). Made for the Umbrel package
maintained by the Dog of Bitcoin Foundation. Not an official release of the DOG Mode project.

Why from source: the DOG Mode project has not cut a release yet, so there is no tarball to
download and no signature to verify. This image compiles the client itself and says so. The
`Dockerfile` names the repository and the exact commit (checked after checkout), builds the
dependencies with the repository's own `depends` system (static, the way release binaries are
made), and prints the binaries' SHA256 sums, which also ship inside the image at `/SHA256SUMS`.
Anyone can rebuild and compare. When the project publishes a signed release, this image switches to
download-and-verify like `getumbrel/docker-bitcoind`.

```
docker run --name dogmode -v $HOME/.bitcoin:/data/.bitcoin -p 8333:8333 ghcr.io/dogofbitcoin/bitcoin:31.1-dogmode-7503240
```

Built by GitHub Actions on native amd64 and arm64 runners; one tag for both architectures.
Pin the manifest-list digest, printed at the end of each run, wherever the image is consumed.

| | |
|---|---|
| Commit | `75032400914250c7ad857dc29043761680a66485` (the merge of DOG Mode pull request 3 into `31.1-dogmode`) |
| Base | Bitcoin Core 31.1 plus the DOG Mode relay policy (3,900,000 WU standard transactions, a global 1 sat dust limit, preferential peering on service bit 14) |
| Binaries | `bitcoind`, `bitcoin-cli`, `bitcoin`, `bitcoin-tx`, `bitcoin-util`, `bitcoin-wallet` |
| Contact | dev@dogswap.io |
