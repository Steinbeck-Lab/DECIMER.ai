# decimer-service

Shared HTTP skeleton for the DECIMER model services: `/healthz`, `/readyz` (a tiny real inference), a bounded `/infer`, JSON logs, and the `python -m` entry point (`decimer_service.server.serve`). The chem service also uses `Gate`, the bounded queue behind `/infer`.

`/readyz` runs the selftest outside the `max_concurrency` limit of `/infer` (a probe must not queue behind a slow request), from another thread, so a selftest can run while `infer` is running. A model that cannot run two calls at once, like the TensorFlow models, must guard itself with a lock; seg, ocsr and cls do.
