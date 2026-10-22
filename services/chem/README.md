# decimer-chem

IUPAC names for the structures decimer.ai finds, from [Orthonym](https://github.com/Steinbeck-Lab/Orthonym) (MIT), at its **`valid` tier, labelled** (owner decision D14). TODO; ROADMAP §2–3 ("IUPAC names", "Java only in this service"). It takes SMILES as JSON and returns a name that Orthonym built and OPSIN checked, together with how it was built (`tier`) and how it was checked (`verified`), or no name. It never guesses, and it never ships Orthonym's `best_effort` tier.

Unlike `seg`, `ocsr` and `cls` it has no image upload and no TensorFlow, so it does not use the `../common` skeleton (`decimer_service.create_app` is built around an image upload). It follows the same conventions in its own small app (`healthz`, `readyz` with a real selftest, a bounded queue that answers 503 with `Retry-After`, anything else unexpected as 500 with an `error_id`) and **reuses `decimer_service.logs`** (JSON logs) through the editable path dependency on `../common`, which cost one line in `pyproject.toml`. The 15-line `_Gate` (the 503 queue) is copied rather than imported because it is private in `decimer_service.app`.

Python 3.12 (the API's version; Orthonym needs 3.10+), a Java 21 runtime, and Orthonym pinned to git commit `110ed8153b365973ad90c518a3ea96fbebb13c8b` (2026-10-21, version 1.0.8, the head of `main` when this was written).

## Run

```bash
cd services/chem
uv sync                                  # see "Installing without downloads"
JAVA_HOME=/path/to/jre uv run python scripts/fetch_jars.py        # development only: put the two jars on disk once
JAVA_HOME=/path/to/jre uv run python -m decimer_chem              # listens on 0.0.0.0:8004
JAVA_HOME=/path/to/jre PORT=9000 uv run python -m decimer_chem    # another port
```

Startup takes about **2.5 s** from process start to `/healthz` answering on the 8-core dev box (details under "Measurements"): the naming process starts, checks both jars against their SHA-256, starts the JVM, names caffeine once to warm OPSIN up, and the service then runs the selftest through the whole stack. If the jars are missing or unreadable, Java is missing, an unlisted `ORTHONYM_*` variable or a JVM-options variable is set, or the selftest fails, **startup fails at once** with a message that names the cause (as `seg` does for missing weights).

| Variable | Default | Effect |
|---|---|---|
| `PORT` | `8004` | Listening port. |
| `JAVA_HOME` | unset | Where JPype finds the JVM (`$JAVA_HOME/lib/server/libjvm.so`). Without it JPype falls back to the `java` on `PATH`. Set it in the image. |
| `CHEM_JARS_DIR` | unset: Orthonym's own jar directory (`$ORTHONYM_JAR_DIR`, else `$ORTHONYM_CACHE_DIR/jars`, else `$XDG_CACHE_HOME/orthonym/jars`, else `~/.cache/orthonym/jars`) | Where `opsin-cli-2.9.0-jar-with-dependencies.jar` and `centres-cli-1.2.1.jar` are. The image sets this to the directory it baked them into. |
| `CHEM_ALLOW_JAR_DOWNLOAD` | unset | Exactly `1` lets Orthonym download a *missing* jar into the jar directory (development only; a service without network cannot). Anything else, including `true`, is off. A jar that is present with the wrong checksum is refused either way. |
| `CHEM_NAME_TIMEOUT_S` | `10` | Soft limit for one structure; see "Time limits". |
| `CHEM_BATCH_TIMEOUT_S` | `60` | Budget for one request (`/names`, or `/name`). |
| `ORTHONYM_JVM_XMX` | `512m` (Orthonym's default) | JVM heap cap. The whole naming process stayed under 380 MB in the soak. |
| `ORTHONYM_JAR_DIR`, `ORTHONYM_NO_DOWNLOAD`, `ORTHONYM_CACHE_DIR`, `ORTHONYM_SKIP_JAR_PREFETCH` | unset | The only other `ORTHONYM_*` variables allowed (see below). The service sets the first two itself in the naming process. |

**Every other `ORTHONYM_*` variable makes the service refuse to start**, and the error names them (not their values). Orthonym reads about thirty; several switch checking off or change what is named: `ORTHONYM_ALLOW_REDUCED` names without the jars (no round trip at all), `ORTHONYM_OPSIN_JAR` and `ORTHONYM_CENTRES_JAR` use a jar "as given, without the checksum check", `ORTHONYM_DISABLE_OPSIN_VALIDITY_GATE` and `ORTHONYM_SELF_CONSISTENCY_GATE` turn checks off, `ORTHONYM_DISABLE_JPYPE` runs a `java` process per call. The allow-list is `ALLOWED_ORTHONYM_ENV` in `engine.py`; the names were checked against the pinned source (`JAR_DIR`, `NO_DOWNLOAD` and `CACHE_DIR` are read by `orthonym.jars`, `JVM_XMX` by `orthonym.jvm_bridge`; `SKIP_JAR_PREFETCH` only by the install-time build hook, so it cannot change a name at run time and is allowed so that an image that exports it still starts).

`JAVA_TOOL_OPTIONS`, `_JAVA_OPTIONS` and `JDK_JAVA_OPTIONS` are refused too (they inject JVM options: memory, agents, where crash reports go). Orthonym's lexicon cache is switched off by the service itself, so `ORTHONYM_LEXICON_CACHE` is not allowed either (see "What the service writes to disk").

It also refuses to start if the in-process JVM did not come up, even though Orthonym would fall back to a `java` subprocess per call and still name correctly, slowly: a misconfigured `JAVA_HOME` should be a deploy error, not a latency regression. And inside the naming process `subprocess` **refuses to start `java`** (fail closed) with a `FileNotFoundError`, which is what a missing `java` raises and what Orthonym's fallbacks are written to catch: a `java` process started by Orthonym's fallback would not be covered by the parent-death signal below and could outlive the service.

Production limits (`decimer_chem.app.LIMITS`): one request names at a time (one naming process, one JVM) and **4 more may wait**, each for at most **15 s**; after that, or when more arrive, the answer is 503 with `Retry-After: 5`. The worker sends one `/names` batch per job and runs at most two jobs at once, so one request waits at a time; the rest is headroom for single `/name` calls. A waiting request whose client hung up meanwhile is dropped without naming (answer 499, nobody reads it). Request bodies up to 256 KiB (413 above that, also for chunked bodies without a `Content-Length`); a cached readiness pass for 30 s.

## `POST /name`

```json
{"smiles": "Cn1cnc2c1c(=O)n(C)c(=O)n2C"}
```

`smiles`: 1 to 2000 characters after stripping surrounding whitespace, printable ASCII, **no whitespace inside** (RDKit would read the rest as the molecule's name), no other fields. Anything else is 422.

Response `200`, always exactly these four keys:

```json
{"name": "1,3,7-trimethyl-3,7-dihydro-1H-purine-2,6-dione", "tier": "pin_verified", "verified": "opsin", "reason": null}
{"name": "(5S)-13,15-dihydroxy-5-methyl-3,11-dioxo-4-oxabicyclo[10.4.0]hexadeca-1(16),12,14-triene", "tier": "systematic_verified", "verified": "opsin", "reason": null}
{"name": null, "tier": "abstain", "verified": null, "reason": "UNSUPPORTED_ELEMENT"}
{"name": null, "tier": null, "verified": null, "reason": "timeout"}
```

`tier` and `verified` are in **every** response, so the front end can map them to the hover text.

**`tier`: how the name was built** (Orthonym's own id, from `name_tiered(...)["tier"]`):

| `tier` | Meaning |
|---|---|
| `pin_verified` | The strict path for the Preferred IUPAC Name built it and certified it. |
| `pin_unverified` | A name in preferred-name form, whose preferred status is not certified (a producer outside the strict path built it or a part of it). Shown **only** with a `verified` value from the table below. |
| `systematic_verified` | A correct systematic name that is not the preferred name (the general engine, a table of retained names, a name from an exact-match list). |
| `abstain` | No name (a decline). |
| `null` | The engine never answered: a timeout, an unreadable SMILES, an engine error. |

`best_effort` (the last-resort tier) is **never shown**: a name that arrives in it, or in any tier not listed above, is withheld with `reason: "tier_not_accepted"`.

**`verified`: how the name was checked.** A name is shown only if its `verified` is one of these six; `unverified`, a missing value or anything unknown is withheld with `reason: "not_round_tripped"`:

| `verified` | Meaning |
|---|---|
| `opsin` | **OPSIN read the whole name back to the same structure** (full InChIKey, constitution, charge and stereo). |
| `opsin_split` | OPSIN read the name back to the structure drawn **without its dative bonds** (RDKit gives a drawing with dative bonds no standard InChIKey); a count of atoms and charges ties the two drawings. |
| `opsin_constitution` | OPSIN confirmed the **connectivity**; the stereodescriptors, which OPSIN cannot parse, were checked against their CIP labels by the centres labeller. |
| `opsin_twin` | A PIN spelling OPSIN has no grammar for: an **equivalent spelling** of the same structure was read back instead. |
| `opsin_acids` | A poly-anhydride name OPSIN has no grammar for: OPSIN read the **component acids** the name cites, and the anhydride built from them is the structure. |
| `identity` | An **exact match in a curated list** (natural-product parents, metal complexes), by the structure's exact InChIKey. OPSIN cannot read these names, so there is no read-back at all. |

ROADMAP §2 says every shown name "has passed Orthonym's OPSIN round trip". That is exactly true of `verified: "opsin"` (all 12 Figure 2 names below), a little weaker for the next four, and not true of `identity`; the UI hover can say which.

**`reason`** is `null` for a name. Otherwise:

| `reason` | When |
|---|---|
| Orthonym's `limit_code`: `UNNAMEABLE`, `NO_VERIFIED_PIN`, `UNSUPPORTED_ELEMENT`, `STRUCTURE_TOO_LARGE`, ... | Orthonym declined (`tier: "abstain"`). |
| `no_verified_name` | Orthonym declined without a code. |
| `not_round_tripped` | A name in an allowed tier whose `verified` is not on the list above; withheld. |
| `tier_not_accepted` | A name in a tier that is not shipped (`best_effort`, anything unknown); withheld. |
| `invalid_smiles` | RDKit cannot read the SMILES (in `/name` this is a 422 instead). |
| `timeout` | The structure ran out of time (see "Time limits"). |
| `engine_error` | The engine raised for this structure, or its process died. Logged with the exception type and frames only. |
| `engine_unavailable` | No naming process could be started for this structure (batches only). |

Errors, all with a JSON body:

- `422` (`/name`, `/names`): the body is not JSON, fails the rules above, or (`/name` only) its SMILES RDKit cannot read. One shape for every 422, FastAPI's: `{"detail": [{"loc": ["body", "smiles"], "msg": "...", "type": "..."}]}`.
- `413` body too large; `499` the client left while waiting; `503` busy (queue full or waited 15 s) or `{"detail": "naming engine unavailable"}` when no structure at all could be tried; both with `Retry-After: 5`.
- `500` `{"detail": "naming failed", "error_id": "..."}` for anything unexpected in the service itself (the id is in the log line, which carries the exception type and frames; no internals in the response).

## `POST /names`

For the worker, which names up to 20 structures per job and should reuse one JVM:

```json
{"smiles": ["CCO", "CC(=O)O"]}
→ {"results": [{"name": "ethanol", ...}, {"name": "acetic acid", ...}]}
```

1 to 20 SMILES (same rules each), results **in the same order**, same four keys each. **Whatever goes wrong with one structure is that structure's result, never the request's:** an unreadable SMILES is `invalid_smiles`, a slow one `timeout`, an engine exception `engine_error`, and a naming process that could not be (re)started costs the structures after it `engine_unavailable`. The names that were made are always returned. Only when not one structure could be tried is the answer 503 (the worker should retry later).

## The valid tier, and two allow-lists

`OrthonymEngine._build_namer` calls `orthonym.Orthonym(general_fallback=True)` and passes nothing else: this is `orthonym --emit-tier valid` (`cli._emit_tier_flags("valid")` in the pinned source). `complete` adds `allow_aromatic_general`, `best-effort` adds `general_fallback_unverified` and `full-coverage` adds `full_coverage`; none of those is passed. A test pins the exact keyword arguments and slow tests pin the effect: `trans-cyclohexane-1,4-diol`, `C1=CC=CC=C1.[Fe]` and a cyclodextrin are named only by `best-effort`, and the service declines all three.

Python API used: `Orthonym(general_fallback=True).name_tiered(smiles)`, the row the command line prints with `--provenance` (keys `name`, `tier`, `verified`, `limit_code`, `gate_outcome`, `source`, ...). `name_compound` returns only a string, no tier. For a decline the row's `name` is a label such as `"unknown organic compound"`; the service does not pass it on as a name.

`result_from_row` (`results.py`) is the gate on every row, **fail-closed twice**: the tier must be in `SHIPPED_TIERS` (`pin_verified`, `pin_unverified`, `systematic_verified`) **and** `verified` must be in `ROUND_TRIPPED` (the six above). Both sets are pinned as literal sets in the tests, not read from the code under test.

**Why the second list.** Orthonym's *default* tier ships carve-out names as `pin_unverified` with `verified: "unverified"`, with no round trip at all: `scyllo-inositol` (gate `carveout:inositol`), `1,4(1,4)-dibenzenacyclohexaphane` (`carveout:phane`), `(η⁶-benzene)iron` for `C1=CC=CC=C1.[Fe]` (`carveout:organometallic_additive`). The `valid` tier does not ship them (its README: "the wider tiers ship none of them"): it declines scyllo-inositol and the benzene-iron complex and names the cyclophane `tricyclo[8.2.2.2^4,7]hexadeca-1(13),4(16),5,7(15),10(14),11-hexaene` (`systematic_verified`, `opsin`). But the allow-list is what guarantees it, whatever the engine does: a slow test builds the engine for the *default* tier (a test-only class) and checks that all three real carve-out rows are withheld as `not_round_tripped`.

`valid` is not a superset of the default tier. It declines `trans-cyclohexane-1,4-diol`, which the default tier names `(1s,4s)-cyclohexane-1,4-diol` (`pin_unverified`, `opsin_constitution`), and it names sphingosine `(2S,3R,4E)-2-aminooctadec-4-ene-1,3-diol` (`pin_unverified`, `opsin`) where the default tier gave `(4E)-sphing-4-enine` (`identity`).

## Time limits and the process model

Orthonym is pure Python (CPU bound, holds the GIL) around an in-process JVM. It usually takes 10 to 300 ms per structure, but a few cost a lot (measured with this service at the `valid` tier: vancomycin, a PEG-50 chain and a PAMAM dendrimer all hit the 10 s soft limit; the default tier alone needed about 9 s, 10 s and 30 s to decline them). A thread cannot be interrupted or killed, would starve the event loop that answers `/healthz` and the 503s, and a JVM crash would take the service with it. So the engine and its one JVM run in **a child process** (`supervisor.py`, started with `spawn`):

- The child names **one structure at a time on its main thread**, inside Orthonym's own `wall_clock_limit` (SIGALRM, from `orthonym.wallclock`: made for exactly this, it raises a `BaseException` that no `except Exception` in the engine can swallow, and it puts the engine's process-wide state back afterwards). The soft limit is `CHEM_NAME_TIMEOUT_S` (10 s) or what is left of the request budget, whichever is smaller. If a timeout left state behind that Orthonym could not restore, the child answers and then retires, and its successor takes over.
- The parent waits for each answer with a **hard deadline**: soft limit plus 5 s. A child that misses it (for example stuck in a JNI call, where the signal handler cannot run) is killed with SIGKILL, which takes the JVM with it. The wait, the pipe and the release of a killed child happen in a thread, not on the event loop.
- **A killed, crashed or retired child is replaced at once, in the background**, so the next request does not pay for the JVM start. A request waits for a replacement only as long as **its own budget** has left: the structures it could not wait for are `timeout`, and the replacement keeps starting for the next request. If the replacement fails to start, the structures after it are `engine_unavailable` (not retried for 5 s). A child that dies while naming a structure costs that structure (`engine_error`); two deaths in a row inside one request end the request's attempts (`engine_unavailable` for the rest).
- A request cancelled mid-call also discards the child, so a late answer can never be read as the next request's. After `stop()` nothing is started any more, whatever call is still in flight (no orphaned replacement at shutdown).
- **The pipe carries JSON, never pickle**, in both directions. Orthonym's names can be `str` subclasses (`PrefixName` carries a derivation object; others exist): pickling one makes the parent import its class and with it Orthonym and RDKit (a web process of 42 MB became 104 MB), and a pickle read from a pipe is code. Three layers keep plain strings on the wire: the policy (`result_from_row`) and `NameResult.as_dict` coerce every field to an exact `str`, and the parent accepts a reply only if it is exactly four fields that are plain strings or nulls (anything else is an `engine_error` and the process is replaced). With the pinned Orthonym I could not make a real name come out as a subclass (the silyl ether `C[Si](C)(C)O[Si](C)(C)C` is a plain `str` at the `valid` tier), so the fast tests prove each layer with a subclass of their own, and a slow test checks on the real engine that the web process never has `orthonym` or `rdkit` in `sys.modules`.
- **The child runs in a scratch directory** (`decimer-chem-*` under the temporary directory, created by the parent, removed by `stop()`, the same for every replacement), so that a JVM crash report (`hs_err_pid*.log`, which holds memory contents) lands there and not in the app directory. Relative path settings (`CHEM_JARS_DIR`, `ORTHONYM_JAR_DIR`, `ORTHONYM_CACHE_DIR`, `XDG_CACHE_HOME`, `JAVA_HOME`) are made absolute first.
- On Linux the child asks the kernel to **SIGKILL it when its parent dies** (`prctl(PR_SET_PDEATHSIG)`, the result checked), so a `kill -9` or OOM kill of the service cannot leave a stuck JVM behind (and `java` itself cannot be started from the child, see above).
- **The JVM starts once**, when a child starts, never per request.

**A timeout is a result, not an error: 200 with `name: null`, `tier: null`, `reason: "timeout"`, not a 504.** In a batch a slow structure costs its own entry, not the 19 good names next to it. Once the request budget (`CHEM_BATCH_TIMEOUT_S`, 60 s) is spent, the structures not yet tried are declined as `timeout` as well. The caller must therefore set its HTTP timeout **above the 15 s queue wait plus `CHEM_BATCH_TIMEOUT_S` plus about 10 s** (a hard kill and the next structure), say 90 s. The UI treats a timeout like any other decline ("no verified name"); a later edit asks again.

## Selftest and `/readyz`

`selftest` names caffeine through the whole stack (HTTP layer, supervisor, child, JVM, OPSIN) and requires **exactly** `1,3,7-trimethyl-3,7-dihydro-1H-purine-2,6-dione` (Orthonym's README) with `verified: "opsin"`. A broken JVM, a missing or wrong jar, or reduced mode gives no name or an unverified one, so this fails closed. It runs once at startup (the service does not start if it fails) and in `/readyz`, which reuses a pass younger than 30 s.

**Only a name that OPSIN read back in full (`verified: "opsin"`) refreshes the pass.** Declines, timeouts and names checked another way do not: a service that answers every request with a decline must not stay "ready" while the selftest would fail. A `/readyz` that arrives while a request is in flight answers ready without running the selftest, because the selftest would only queue behind that batch (the deadlines bound the batch, and a dead child fails the next selftest).

## What the service writes to disk

Nothing that is read back, and nothing outside the temporary directory.

- **Orthonym's lexicon cache is off.** Orthonym caches its morpheme table as a pickle under `ORTHONYM_CACHE_DIR`, `XDG_CACHE_HOME` or `~/.cache/orthonym` (`validation/name_morphemes.py`, written with `pickle.dump` at run time and loaded with `pickle.loads` at the next start: arbitrary code execution for anyone who can write to that directory). The naming process sets `ORTHONYM_LEXICON_CACHE=off` before it imports Orthonym, after checking that the operator did not set it. The cost is rebuilding the table (about 0.5 s) the first time a structure needs it in each naming process; it is used only by the general engine (for example Figure 2 compound 4), not by ethanol, aspirin or caffeine. Startup did not get slower (2.5 s). A slow test plants a pickle where Orthonym looks (three variants: `ORTHONYM_CACHE_DIR`, `XDG_CACHE_HOME`, `HOME`), makes `pickle.loads` raise an exception that Orthonym cannot swallow, names a structure that needs the lexicon, and checks that nothing was read, written or changed.
- **Jars**: read only. **The scratch directory**: see the process model; it holds a JVM crash report at worst.
- **`~/.cache/orthonym/jars/.lock`** is opened only when Orthonym downloads (`CHEM_ALLOW_JAR_DOWNLOAD=1`).
- So **a read-only root filesystem works**, with a writable temporary directory; no cache directory, home directory or volume is needed.

## Logs: no structures, no names

JSON lines (`decimer_service.logs`). The service's own lines carry counts and times only (`named`: structures, named, declined, timeouts, ms). Beyond that:

- Orthonym's loggers are set to WARNING (at INFO it logs the name it picked for every molecule), and a **redacting filter** in the naming process reduces whatever still gets through to the logger name, the level and one fixed sentence (`orthonym log message withheld`): no message, arguments, extra fields or traceback.
- An exception is logged as **its type and its frames** (`file:line in function`), never its message, which can carry the SMILES (`log_failure` in `logs.py`). This covers the 500 path (with its `error_id`) and `engine_error`.
- When a timeout leaves Orthonym's state unrestored, only the **keys** of that state are logged.
- RDKit's own log streams are off in the naming process (Orthonym's probing of candidate names makes it print "Can't kekulize" lines).

Startup errors keep their messages (a path, a variable name); they carry no structures.

## Java and jars (for P2-05)

Java is only in this service. The jars are not downloaded at runtime: the service checks both at `CHEM_JARS_DIR` against Orthonym's pinned SHA-256 **before it builds the engine**, and a missing one is a `RuntimeError` that names the path (and says how to supply it), unless `CHEM_ALLOW_JAR_DOWNLOAD=1`. A jar the service cannot read (Orthonym writes them with mode `0600`) is a `RuntimeError` that says so and suggests `chmod 0644` or `chown`. It also switches Orthonym's own first-use download off (`ORTHONYM_NO_DOWNLOAD=1`) in the naming process. A slow test starts the engine and names a structure with dead proxy variables and every socket connection and download function replaced by one that raises.

| Jar | Version | Size | SHA-256 |
|---|---|---|---|
| `opsin-cli-2.9.0-jar-with-dependencies.jar` ([release](https://github.com/dan2097/opsin/releases/download/2.9.0/opsin-cli-2.9.0-jar-with-dependencies.jar)) | OPSIN 2.9.0 | 14,335,652 B | `c2e29326c281f87b59a05d934d8589adac6e9d17b95b984931b3e739111b360f` |
| `centres-cli-1.2.1.jar` ([release](https://github.com/SiMolecule/centres/releases/download/1.2.1/centres.jar), published as `centres.jar`) | centres 1.2.1 | 2,291,742 B | `6c5d5cafa9393ed7b667a2340222348cd15a9bbdd0d1b92518229f26cc5e87fb` |

These are the checksums Orthonym pins in `orthonym/jars.py` (it verified the downloads), and a slow test checks that the files on disk match the ones written here. They were fetched once with `scripts/fetch_jars.py` into `~/.cache/orthonym/jars` on the dev box.

JRE on the dev box (user level, no system packages): **Eclipse Temurin 21.0.13+9 JRE, x64 Linux** (`OpenJDK21U-jre_x64_linux_hotspot_21.0.13_9.tar.gz`, 52,181,297 B), SHA-256 `a535a1df9c6284d9618872984e026ae0352d1f7e9e0605425c0e6114f47351dd`, which matches both the Adoptium API (`api.adoptium.net/v3/assets/latest/21/hotspot?image_type=jre&os=linux&architecture=x64`) and the `.sha256.txt` published next to the file in https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.13%2B9. Unpacked to `~/.local/opt/jre21`, with `~/.local/opt/jre` pointing at it; used through `JAVA_HOME=~/.local/opt/jre` in the shell only (no profile edited). The image should use a JRE base layer (Temurin 21 JRE) instead; Orthonym needs Java 11+.

### Installing without downloads

**Orthonym's build hook downloads the jars at install time unless told not to.** `orthonym` is installed from git, and its `hatch_build.py` fetches both jars into the user cache when the wheel is built (`uv sync` does that; seen: 16 MB fetched during a sync). `pyproject.toml` therefore sets `[tool.uv.extra-build-variables] orthonym = { ORTHONYM_SKIP_JAR_PREFETCH = "1" }` (checked: without it the jars land in the cache during `uv sync`, with it nothing is written), so a plain `uv sync` is safe. A build that does not go through uv's project settings (`pip install` of the lock file, for instance) must export `ORTHONYM_SKIP_JAR_PREFETCH=1` itself, or it downloads in a build step you did not mean to give network access.

Other things P2-05 needs to know:

- The jars are written with mode `0600` (Orthonym creates them through `mkstemp`): `chmod 0644` them or `chown` them to the service user when copying them into the image. The service only reads them. (Orthonym also opens `<jar dir>/.lock` for appending, but only when it downloads.)
- `JAVA_HOME` must point at the JRE (JPype looks for `lib/server/libjvm.so`).
- Do not set any other `ORTHONYM_*` variable in the image (see "Run").
- Memory: see below. A container limit of **1 GB** is comfortable (two processes: about 50 MB for the web process and about 350 MB for the naming process, flat over the soak; Orthonym's JVM heap cap of 512 MB was not reached). A replacement process starts while the old one is still being released, so the peak can briefly hold two.
- No `/tmp/hsperfdata_*` files: Orthonym starts its JVM with `-XX:-UsePerfData`, so killing the child leaks nothing.
- The image can be a normal Python 3.12 image plus the JRE; RDKit, lxml and JPype ship wheels (rdkit 2026.3.5, lxml 6.1.3, jpype1 1.7.1, all pinned by Orthonym).

## Measurements

Dev box (8 cores, no other load), Temurin 21.0.13, `scripts/measure.py`, valid tier:

| What | Result |
|---|---|
| Process start to `/healthz` answering | **2.5 s**. Of that, the naming process takes 2.1 s from spawn to ready: 1.8 s of engine start (jar hashes a few tens of ms, `import orthonym` about 0.3 s, JVM start plus OPSIN's automaton and the first name about 1.2 s) |
| `POST /name`, the 12 Figure 2 compounds, HTTP round trip | min 46 ms, **median 76 ms**, max 602 ms (the first structure in this process that needs the lexicon: the table is built once, since there is no cache) |
| `POST /names`, the 12 plus 8 others (taxol and a saponin among them) | 1.3 s for 20, about 65 ms per structure |
| Structures cut off at the 10 s soft limit | vancomycin, a PEG-50 chain and a PAMAM dendrimer: 10.0 s each, `timeout`. Orthonym's own state was restored every time (`state_not_restored: []`), so the process was kept, and the next name came back at once |
| Soak: 600 names, 20 per request, 23 different structures in rotation | **66 ms per name** |
| Memory, web process | 46 MB, 47 MB after the soak (Orthonym and RDKit are never imported in it) |
| Memory, naming process (Python, RDKit, Orthonym and the JVM) | 289 MB after start, 342 MB after the first heavy structures, **350 MB flat from name 100 to name 600** (peak equal), so no leak in this run |

The `valid` tier costs more than the default tier did (42 ms per name in an earlier run, median 44 ms): when the PIN path declines, the general engine runs as well. The first name in a fresh JVM costs about 1.2 s; the service pays it at startup, so no request does. `python scripts/measure.py [--soak N]` prints these.

## Figure 2 of the golden paper

`pytest -m slow -s tests/test_integration.py -k figure_2` prints this table. SMILES are rows 1 to 12 of `golden/labels.csv` (the rows numbered exactly `1`..`12`; the `(Fig 3)` rows are different compounds). The last column is a check that is **not** Orthonym's own: the test runs the OPSIN command line separately (another process, another JVM) on each shown name and compares the InChIKey of the structure OPSIN reads with the one of the SMILES sent.

| # | SMILES | name | tier | verified | OPSIN read-back |
|---|---|---|---|---|---|
| 1 | `C=CC(C)(C)[C@@H]1Nc2ccccc2C(=O)[C@H]1CN1C(=O)CC[C@@H]1OC` | (2R,3S)-3-{[(2S)-2-methoxy-5-oxopyrrolidin-1-yl]methyl}-2-(2-methylbut-3-en-2-yl)-4-oxo-1,2,3,4-tetrahydroquinoline | systematic_verified | opsin | same InChIKey |
| 2 | `C=CC(C)(C)c1[nH]c2ccccc2c1CCN1C(=O)CCC1O` | 3-[2-(2-hydroxy-5-oxopyrrolidin-1-yl)ethyl]-2-(2-methylbut-3-en-2-yl)-1H-indole | systematic_verified | opsin | same InChIKey |
| 3 | `CCC(O)CC/C=C\C/C=C\C(O)CCCCC(=O)O` | (7Z,10Z)-6,14-dihydroxyhexadeca-7,10-dienoic acid | pin_verified | opsin | same InChIKey |
| 4 | `OC[C@H](O)C(c1c[nH]c2ccccc12)c1c[nH]c2ccccc12` | (2R)-3,3-bis(1H-indol-3-yl)propane-1,2-diol | systematic_verified | opsin | same InChIKey |
| 5 | `C[C@H]1CCC/C=C/C(=O)c2c(O)cc(O)cc2CC(=O)O1` | (5S,9E)-13,15-dihydroxy-5-methyl-3,11-dioxo-4-oxabicyclo[10.4.0]hexadeca-1(16),9,12,14-tetraene | systematic_verified | opsin | same InChIKey |
| 6 | `C[C@H]1CCCC(O)CC(=O)c2c(O)cc(O)cc2CC(=O)O1` | (5S)-9,13,15-trihydroxy-5-methyl-3,11-dioxo-4-oxabicyclo[10.4.0]hexadeca-1(16),12,14-triene | systematic_verified | opsin | same InChIKey |
| 7 | `C[C@H]1CCCCCC(=O)c2c(O)cc(O)cc2CC(=O)O1` | (5S)-13,15-dihydroxy-5-methyl-3,11-dioxo-4-oxabicyclo[10.4.0]hexadeca-1(16),12,14-triene | systematic_verified | opsin | same InChIKey |
| 8 | `C[C@H]1CCC[C@@H]2CC(=O)c3c(cc(O)cc3O2)CC(=O)O1` | (4S,8R)-13-hydroxy-4-methyl-4,5,6,7,8,9-hexahydro-2H-8,11-epoxy-3-benzoxacyclododecine-2,10(1H)-dione | pin_verified | opsin | same InChIKey |
| 9 | `CC[C@H](C)c1cnc(CC(C)C)c(=O)[nH]1` | 6-[(2S)-butan-2-yl]-3-(2-methylpropyl)pyrazin-2(1H)-one | pin_verified | opsin | same InChIKey |
| 10 | `CCC(C)c1cnc(C(C)CC)c(=O)[nH]1` | 3,6-di(butan-2-yl)pyrazin-2(1H)-one | pin_verified | opsin | same InChIKey |
| 11 | `O=c1c(-c2ccc(O)cc2)coc2cc(O)ccc12` | 7-hydroxy-3-(4-hydroxyphenyl)-4H-1-benzopyran-4-one | pin_verified | opsin | same InChIKey |
| 12 | `O=c1c(-c2ccc(O)cc2)coc2cc(O)cc(O)c12` | 5,7-dihydroxy-3-(4-hydroxyphenyl)-4H-1-benzopyran-4-one | pin_verified | opsin | same InChIKey |

**All twelve are named, each fully read back by OPSIN.** Six (3, 8 to 12) are PIN-path names; the other six (1, 2, 4 to 7), which Orthonym's default tier declines, are `systematic_verified`: correct systematic names that are **not** the preferred names (compounds 5 to 7 are von Baeyer names of the macrocycle, not the "benzoxacyclododecine" form). The test pins the split and the tiers, so an Orthonym bump that changes them fails there and is looked at.

## Tests

```bash
uv run pytest                          # fast tests: fake namer / fake engine, no JVM, no network (about 30 s)
JAVA_HOME=~/.local/opt/jre uv run pytest -m slow -s   # real Orthonym and JVM, the jars must be on disk (about 25 s)
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Fast tests:

- `test_results.py`: the policy against **literal** expected sets: every allowed (tier, verified) pair is shipped with both labels; `unverified`, `None`, an empty string, an unknown word or a non-string are withheld as `not_round_tripped` in every allowed tier; `pin_unverified` with `unverified` is withheld; the three real carve-out rows; `best_effort` and unknown tiers are withheld as `tier_not_accepted` whatever `verified` says; declines.
- `test_results.py` also pins that every field of a result is an exact `str` when the row holds `str` subclasses (in the shipped, declined and withheld paths and in `as_dict`).
- `test_logs.py`: the redaction filter (logger and level kept, everything else gone, other loggers untouched) and `log_failure` (type and frames, not the message).
- `test_settings.py`: environment parsing (only exactly `1` opts in to downloads).
- `test_app.py`: with a fake namer: response shape, every 422 path and its one shape, 413 (also chunked), batch order and limits (20 ok, 21 refused), per-item `invalid_smiles`, 503 for an unavailable engine, 500 with an `error_id` and the service staying up, no secrets in the log record of a 500, 503 with `Retry-After` when saturated and when a waiter's 15 s are up, a waiter that hung up is not named, readiness (cache, failure, busy; declines and non-`opsin` names do **not** refresh it), the startup selftest, no SMILES in the logs, production limits.
- `test_supervisor.py`: a **real child process** running a fake engine: results in order from another process, the same process for every call, a hung call killed at the hard deadline and the process replaced in the background, a hang in the middle of a batch costing one entry, the batch budget, a slow replacement waited for only as long as the budget (and not abandoned), a replacement that fails to start (`engine_unavailable`, 503 when nothing could be tried, not retried in a loop), an engine exception (`engine_error`, same process), a crash (batch carries on) and two crashes in a row, a process that asks to be replaced, a cancelled call, startup failures (message, no word, hang), `stop()` during a background replacement, the reaper tolerating `process.close()` failing, the parent-death signal (checked, and: kill -9 the parent while the child is stuck, the child dies), Orthonym's logs redacted, the pipe carrying JSON in both directions (a pickle from the other end is refused and never loaded), a `str` subclass from a module only the child imports arriving as a plain `str` with that module absent from the parent, a reply with a non-string field refused (`engine_error`, process replaced), the scratch directory (created, the same across replacements, removed by `stop()`), relative paths made absolute, and no replacement started after `stop()` (checked with a spy on the spawn, since a late child could otherwise fail for an unrelated reason and hide the bug).
- `test_engine.py`: the allow-list of `ORTHONYM_*` variables (each dangerous one refused, values never echoed, empty values too, the five allowed ones pass), jar checks (missing, wrong checksum, unreadable with the `chmod` hint, one of two, download opt-in, nothing downloaded otherwise), the jar policy, `Orthonym` built with **exactly** `general_fallback=True`, an unreadable SMILES declined by RDKit before Orthonym is called, the policy applied to what the namer returns, the soft limit with a fake that spins in Python (a timeout result, usable afterwards, state left behind asks for a new process and only its keys are logged, refused off the main thread), and `java` refused as a subprocess (as a `FileNotFoundError`) while others still run; the JVM-options variables refused; the lexicon cache switched off by the service, before Orthonym is imported, and not switchable on by an operator.

Slow tests (`test_integration.py`): caffeine, ethanol and aspirin with exactly the names in Orthonym's README; the 12 Figure 2 compounds in one batch (each named, fully OPSIN-read-back, with the tier pinned, and checked independently by the OPSIN CLI; the table above); no tier wider than `valid` (three structures that only `best-effort` names are declined); the reviewer's three carve-out cases through the service (not shipped under their carve-out names) and through a default-tier engine (all three withheld as `not_round_tripped`); a `pin_unverified` name with a full read-back is served (sphingosine); invalid SMILES (422 for `/name`, per-item in a batch); declines for other reasons (`[Na+]`); one process and one JVM across requests; a real Orthonym timeout (a dendrimer with a 1 s limit comes back as `timeout` in under 10 s, with no process killed and the same pid after); engine start and naming with the network unavailable; the web process never imports `orthonym` or `rdkit` after naming the silyl ethers and stays under 80 MB; no cache pickle is read, written or changed (three variants); the jars on disk equal the hashes in this README; `python -m decimer_chem` as a program (JSON on every log line, no structure names in the logs, clean exit on SIGTERM).

Checked by breaking the code (each plausible bug applied alone, the named tests run, then restored):

| Bug applied | Caught by |
|---|---|
| the `verified` check dropped from the policy | `test_results.py::test_a_name_whose_check_is_not_an_allowed_one_is_withheld_as_not_round_tripped` |
| `unverified` added to the round-trip list, or `best_effort` to the tier list | `test_results.py::test_the_allow_lists_are_exactly_the_decided_ones` |
| Orthonym built for the default tier (no flags) | `test_engine.py::test_the_engine_is_built_for_the_valid_tier_and_no_wider_one`; with the real engine `test_integration.py::test_figure_2_...` (six compounds come back declined) |
| Orthonym built for `best-effort` (or `complete`) | the same fast test; with the real engine `test_integration.py::test_no_tier_wider_than_valid_is_used` (the diol, benzene-iron and the cyclodextrin come back named) |
| any `ORTHONYM_*` variable tolerated, or `ORTHONYM_ALLOW_REDUCED` added to the allowed ones | `test_engine.py::test_every_orthonym_setting_but_the_allowed_ones_is_refused`, `test_the_allowed_orthonym_settings_are_exactly_these_five` |
| a `java` subprocess allowed | `test_engine.py::test_a_java_subprocess_is_refused_in_the_naming_process` |
| the RDKit check before Orthonym removed | `test_engine.py::test_an_unreadable_smiles_is_declined_before_orthonym_is_called` |
| Orthonym's soft time limit skipped | `test_engine.py::test_orthonym_running_past_the_soft_limit_is_a_timeout_result` |
| leaked state logged by value, not by key; no new process after a dirty timeout | `test_engine.py::test_a_timeout_that_left_state_behind_asks_for_a_new_process_and_logs_only_the_keys` |
| the unreadable-jar hint removed; a missing jar tolerated; Orthonym's download not switched off | `test_engine.py::test_a_jar_the_service_cannot_read_gets_a_permissions_hint`, `test_missing_jars_are_a_runtime_error_...`, `test_the_jar_policy_points_orthonym_at_our_directory_and_forbids_downloads` |
| readiness refreshed by any name (or any request) | `test_app.py::test_steady_declines_do_not_keep_readyz_green_when_the_selftest_would_fail` |
| the queue wait not limited; a client that left still named | `test_app.py::test_a_request_that_waits_too_long_for_the_slot_is_a_503_with_retry_after`, `test_work_is_skipped_for_a_client_that_left_while_waiting_for_the_slot` |
| the 503 gate always lets requests in | `test_app.py::test_503_with_retry_after_when_saturated_...` |
| a 422 detail without the `body` prefix | `test_app.py::test_every_422_has_the_same_detail_shape` |
| no `error_id` in the 500; the selftest not checking the name; the body cap not applied to a chunked body | `test_app.py::test_an_unexpected_error_is_a_500_with_an_error_id_...`, `test_startup_fails_when_caffeine_gets_another_name`, `test_a_chunked_body_over_the_limit_is_413_too` |
| the exception message in the logged failure fields | `test_logs.py::test_failure_fields_have_the_exception_type_and_the_frames_but_not_the_message` |
| the redaction filter not installed in the naming process, or Orthonym's INFO logs left on | `test_supervisor.py::test_orthonyms_per_molecule_info_logs_are_redacted_in_the_service_logs` |
| the hard deadline removed | `test_supervisor.py::test_a_call_that_ignores_the_soft_limit_is_killed_at_the_hard_deadline` |
| the wait for a replacement not bounded by the request budget | `test_supervisor.py::test_a_replacement_that_is_slow_to_start_is_waited_for_only_as_long_as_the_budget` |
| a failed replacement fails the whole batch | `test_supervisor.py::test_a_replacement_that_fails_to_start_marks_the_rest_engine_unavailable` |
| no replacement in the background after a kill | `test_supervisor.py::test_the_process_is_replaced_in_the_background_after_a_kill` |
| a process that asks to be replaced is kept | `test_supervisor.py::test_a_process_that_asks_to_be_replaced_is_replaced_after_its_answer_is_delivered` |
| an engine exception not contained per structure | `test_supervisor.py::test_an_error_inside_the_engine_costs_that_structure_only` |
| the parent-death signal's result unchecked; the signal not set | `test_supervisor.py::test_parent_death_signal_failure_is_logged_not_ignored`, `test_the_naming_process_dies_with_its_parent_even_when_stuck` |
| the kill waited for on the event loop | `test_supervisor.py::test_killing_a_process_does_not_block_the_event_loop` |
| batch results out of order; the process restarted per request; a cancelled call keeping its process | `test_supervisor.py::test_names_come_back_in_order_from_a_separate_process`, `test_the_same_process_serves_every_call`, `test_a_call_cancelled_mid_flight_...` |

| the policy no longer coerces fields to exact `str`; `as_dict` no longer does | `test_results.py::test_every_field_of_a_result_is_a_plain_str_even_when_the_row_holds_subclasses`, `test_as_dict_is_plain_strs_whatever_the_result_holds` |
| the child sends pickle; the parent loads pickle; the parent accepts any fields | `test_supervisor.py::test_both_directions_of_the_pipe_carry_json_not_pickle`, `test_a_pickle_from_the_other_end_is_refused_and_never_loaded`, `test_the_parent_accepts_only_exactly_four_string_or_null_fields` |
| Orthonym's lexicon cache left on, or switched off only after Orthonym is imported | `test_engine.py::test_the_lexicon_pickle_cache_is_switched_off_by_the_service_itself`, `test_start_switches_the_caches_off_before_anything_of_orthonym_is_imported`; with the real service `test_integration.py::test_the_service_neither_reads_nor_writes_a_cache_pickle` |
| the JVM-options variables tolerated | `test_engine.py::test_the_variables_that_inject_jvm_options_are_refused` |
| no scratch directory (or not removed at stop, or relative paths left relative) | `test_supervisor.py::test_the_naming_process_runs_in_a_scratch_directory_that_stop_removes`, `test_relative_path_settings_become_absolute_...` |
| a replacement started after `stop()` | `test_supervisor.py::test_no_replacement_starts_once_the_namer_is_stopped` |
| the `java` refusal raised as a `RuntimeError` | `test_engine.py::test_a_java_subprocess_is_refused_in_the_naming_process` |

Four tests were first found not to catch their bug, and now assert the invariant directly: the cancelled-call test passed by luck of a race, the INFO-logs test was hidden behind the redaction filter, the stop test passed because the late replacement happened to fail on its own (its scratch directory was already gone), and the cache-pickle test named only structures that never consult the lexicon (it passed with the cache on). The one thing a mutation cannot show on the real engine is the coercion of `str` subclasses, because the pinned Orthonym does not produce one for any structure I tried; the fast tests cover each layer with a subclass of their own.

Every wait that depends on the code under test is bounded, so a bug fails its test instead of hanging it.

## Things to know about Orthonym

- `name_tiered` raises `ValueError` for a SMILES RDKit cannot read, but an **empty string is not an error**: it is a decline (`UNNAMEABLE`). The HTTP layer rejects empty input before it gets there, and the engine checks every SMILES with `Chem.MolFromSmiles` first; a `ValueError` out of Orthonym itself for a SMILES RDKit read is therefore an `engine_error`, not an "invalid SMILES".
- Orthonym's `Orthonym()` constructor raises `JarUnavailable` (a `RuntimeError`) if a jar is missing; the service checks first so the message is ours.
- RDKit writes "non-ring atom marked aromatic" and "Can't kekulize mol" lines to stderr while Orthonym probes candidate names; the naming process turns RDKit's log streams off.
- Orthonym's JVM bridge falls back to a `java` subprocess per call if JPype or the JVM is unavailable ("fallback, never failure"); the service treats that as a startup error and refuses the subprocess (see Run).
- Orthonym's names are not always plain `str`: it has `str` subclasses that carry objects (`PrefixName`, `CascadeRefusal`, `AlphaKey`). Nothing of that crosses the pipe (see the process model).
- Orthonym's lexicon cache is a pickle written at run time; it is off (see "What the service writes to disk"). The morpheme table is rebuilt in each naming process the first time a structure needs it.
- `ORTHONYM_GC_TUNE=on` (Orthonym's batch tuning) is one of the refused variables; it was not tried.
