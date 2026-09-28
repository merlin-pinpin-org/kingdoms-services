# Contracts (ADR-0020)

gRPC contracts between the Kingdoms processes: `bot-discord ↔ svc-core`
and `svc-core ↔ ext-*`. Owned by the developer; regenerated stubs are
committed so CI and deploys never need protoc.

Regenerate after editing a `.proto`:

```
make contracts
```

In-process source of truth remains pydantic/Protocol models; proto types
are converted at the seam only.
