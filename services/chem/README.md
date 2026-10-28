# decimer-chem

IUPAC names for the structures decimer.ai finds, from [Orthonym](https://github.com/Steinbeck-Lab/Orthonym) (MIT), at its **`valid` tier, labelled**. It takes SMILES as JSON and returns a name that Orthonym built and OPSIN checked, together with how it was built (`tier`) and how it was checked (`verified`), or no name. It never guesses, and it never ships Orthonym's `best_effort` tier.

It also carries the **CDK fallback**: `POST /cdk` turns a SMILES that RDKit rejects into a V2000 molblock with CDK 2.8's own 2D layout, as the earlier Laravel version of decimer.ai does for every structure, or declines. CDK runs in the same JVM as Orthonym, in the same supervised process. See "CDK fallback" below.

Unlike `seg`, `ocsr` and `cls` it has no image upload and no TensorFlow, so it does not use the `../common` skeleton (`decimer_service.create_app` is built around an image upload). It follows the same conventions in its own small app (`healthz`, `readyz` with a real selftest, a bounded queue that answers 503 with `Retry-After`, anything else unexpected as 500 with an `error_id`) and **reuses `decimer_service.logs`** (JSON logs), `decimer_service.Gate` (the 503 queue) and `decimer_service.server.serve` (the `python -m` entry point) through the editable path dependency on `../common`, which cost one line in `pyproject.toml`.

Python 3.12 (the API's version; Orthonym needs 3.10+), a Java 21 runtime, and Orthonym pinned to git commit `110ed8153b365973ad90c518a3ea96fbebb13c8b` (version 1.0.8).

## Run

```bash
cd services/chem
uv sync                                  # see "Installing without downloads"
JAVA_HOME=/path/to/jre uv run python scripts/fetch_jars.py        # development only: put the three jars on disk once
JAVA_HOME=/path/to/jre uv run python -m decimer_chem              # listens on 0.0.0.0:8004
JAVA_HOME=/path/to/jre PORT=9000 uv run python -m decimer_chem    # another port
```

Startup takes about **3 s** (2.5 s before CDK) from process start to `/healthz` answering on an 8-core machine (details under "Measurements"): the naming process starts, checks the three jars against their SHA-256, starts the JVM, names caffeine once to warm OPSIN up, opens CDK in its own class loader and lays caffeine out once to warm it up, and the service then runs the selftest (names and CDK) through the whole stack. If the jars are missing or unreadable, Java is missing, an unlisted `ORTHONYM_*` variable or a JVM-options variable is set, or the selftest fails, **startup fails at once** with a message that names the cause (as `seg` does for missing weights).

| Variable | Default | Effect |
|---|---|---|
| `PORT` | `8004` | Listening port. |
| `JAVA_HOME` | unset | Where JPype finds the JVM (`$JAVA_HOME/lib/server/libjvm.so`). Without it JPype falls back to the `java` on `PATH`. Set it in the image. |
| `CHEM_JARS_DIR` | unset: Orthonym's own jar directory (`$ORTHONYM_JAR_DIR`, else `$ORTHONYM_CACHE_DIR/jars`, else `$XDG_CACHE_HOME/orthonym/jars`, else `~/.cache/orthonym/jars`) | Where `opsin-cli-2.9.0-jar-with-dependencies.jar`, `centres-cli-1.2.1.jar` and `cdk-2.8.jar` are. The image sets this to the directory it baked them into. |
| `CHEM_ALLOW_JAR_DOWNLOAD` | unset | Exactly `1` lets Orthonym download a *missing* OPSIN or centres jar into the jar directory (development only; a service without network cannot). Anything else, including `true`, is off. **It never covers `cdk-2.8.jar`**: the service does not download that one at all. A jar that is present with the wrong checksum is refused either way. |
| `CHEM_NAME_TIMEOUT_S` | `10` | Soft limit for one structure named; see "Time limits". |
| `CHEM_CDK_TIMEOUT_S` | `10` | Soft limit for one structure laid out by CDK; see "CDK fallback". |
| `CHEM_BATCH_TIMEOUT_S` | `60` | Budget for one request (`/names`, `/name` or `/cdk`). |
| `ORTHONYM_JVM_XMX` | `512m` (Orthonym's default) | JVM heap cap. The whole naming process stayed under 380 MB in the soak. |
| `ORTHONYM_JAR_DIR`, `ORTHONYM_NO_DOWNLOAD`, `ORTHONYM_CACHE_DIR`, `ORTHONYM_SKIP_JAR_PREFETCH` | unset | The only other `ORTHONYM_*` variables allowed (see below). The service sets the first two itself in the naming process. |

**Every other `ORTHONYM_*` variable makes the service refuse to start**, and the error names them (not their values). Orthonym reads about thirty; several switch checking off or change what is named: `ORTHONYM_ALLOW_REDUCED` names without the jars (no round trip at all), `ORTHONYM_OPSIN_JAR` and `ORTHONYM_CENTRES_JAR` use a jar "as given, without the checksum check", `ORTHONYM_DISABLE_OPSIN_VALIDITY_GATE` and `ORTHONYM_SELF_CONSISTENCY_GATE` turn checks off, `ORTHONYM_DISABLE_JPYPE` runs a `java` process per call. The allow-list is `ALLOWED_ORTHONYM_ENV` in `engine.py`; the names were checked against the pinned source (`JAR_DIR`, `NO_DOWNLOAD` and `CACHE_DIR` are read by `orthonym.jars`, `JVM_XMX` by `orthonym.jvm_bridge`; `SKIP_JAR_PREFETCH` only by the install-time build hook, so it cannot change a name at run time and is allowed so that an image that exports it still starts).

`JAVA_TOOL_OPTIONS`, `_JAVA_OPTIONS` and `JDK_JAVA_OPTIONS` are refused too (they inject JVM options: memory, agents, where crash reports go). Orthonym's lexicon cache is switched off by the service itself, so `ORTHONYM_LEXICON_CACHE` is not allowed either (see "What the service writes to disk").

It also refuses to start if the in-process JVM did not come up, even though Orthonym would fall back to a `java` subprocess per call and still name correctly, slowly: a misconfigured `JAVA_HOME` should be a deploy error, not a latency regression. And inside the naming process `subprocess` **refuses to start `java`** (fail closed) with a `FileNotFoundError`, which is what a missing `java` raises and what Orthonym's fallbacks are written to catch: a `java` process started by Orthonym's fallback would not be covered by the parent-death signal below and could outlive the service.

Production limits (`decimer_chem.app.LIMITS`): one request names (or lays out) at a time (one naming process, one JVM; `/cdk` and `/names` share the slot) and **4 more may wait**, each for at most **15 s**; after that, or when more arrive, the answer is 503 with `Retry-After: 5`. The worker sends one `/names` batch per job and runs at most two jobs at once, so one request waits at a time; the rest is headroom for single `/name` calls. A waiting request whose client hung up meanwhile is dropped without naming (answer 499, nobody reads it). Request bodies up to 256 KiB (413 above that, also for chunked bodies without a `Content-Length`); a cached readiness pass for 30 s.

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

The aim is that every shown name has passed Orthonym's OPSIN round trip. That is exactly true of `verified: "opsin"` (all 12 Figure 2 names below), a little weaker for the next four, and not true of `identity`; the UI hover can say which.

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

## CDK fallback: `POST /cdk`

**What.** For a SMILES that RDKit rejects (the API's RDKit check fails: a valence error, an aromatic ring that RDKit cannot kekulise, ...), CDK 2.8 may still read it, and the earlier version has always shown CDK's reading. `POST /cdk` returns the V2000 molblock that CDK writes for it, with CDK's own 2D coordinates, or declines. The worker half (`api/src/decimer_api/worker/pipeline.py`, `_cdk`; not this service's concern) sends the SMILES that RDKit refused in **one batch per job** (at most 20 per request, before it asks for the names), keeps a molblock only if the API's own molfile gate and RDKit will read it, and marks the result `validBy: "cdk"` (`valid` stays false, RDKit's word). Declines are silent there; a timeout, a failed call or a wrong number of answers is one `cdk_unavailable` warning per job, and a timeout of this call also skips the names for that job (the service is taken to be hung).

**Why.** On the golden paper RDKit rejects 1 SMILES per run and CDK 2.8 rejects the same ones, so the data alone would not justify CDK. It is kept for compatibility: the earlier version sends every SMILES through CDK 2.8, whose parser is "much more tolerant" than RDKit's and Indigo's, so users are used to CDK's reading; it is the fallback here, on by default.

**Same pipeline as the earlier version** (its `app/Python/check_smiles_validity.py`): `SmilesParser(SilentChemObjectBuilder)` parses, `StructureDiagramGenerator` (`setMolecule`, `generateCoordinates`) lays out, `MDLV2000Writer` writes the molblock. There any exception meant "invalid"; here a parse failure is `invalid_smiles` and a failure of the layout or the writer is `layout_failed`, both declines. The molblock is CDK's text unchanged (its second line carries a timestamp, `  CDK     10232605302D`, so two calls a minute apart differ in that line only; RDKit and every molblock reader ignore it).

**The jar.** `cdk-2.8.jar`, the CDK 2.8 bundle, 47,174,875 bytes, SHA-256 `5dff6e365ecc21020e44aa31861b36a9cce34c51ed0b8713b8d25275552239b4`: the exact file the earlier version ships (`DECIMER_Web/app/Python/cdk-2.8.jar`). Official source: the `cdk-2.8` release of github.com/cdk/cdk (published 2022-09-14), asset `cdk-2.8.jar`, https://github.com/cdk/cdk/releases/download/cdk-2.8/cdk-2.8.jar. A fresh download over https has the hash above and the size the GitHub API reports for the asset, which equals the earlier version's file (GitHub publishes no digest for the asset, so that file is the second witness). It is listed in `deploy/weights/SHA256SUMS` (`chem/cdk-2.8.jar`), fetched by `deploy/weights/fetch.sh chem` (https only, also across redirects, verified before anything is installed), baked into the image next to the Orthonym jars (`/opt/orthonym/jars`, root-owned, read-only for the service user, checked at build time with `verify.sh --strict`), and **checked again by the service before it builds its engine** (`check_cdk_jar`: missing, unreadable or any other SHA-256 is a `RuntimeError` that names the path, and the service does not start). It is never downloaded at run time, whatever `CHEM_ALLOW_JAR_DOWNLOAD` says. As with the Orthonym jars, the file is hashed and then opened by the JVM a moment later; the read-only, root-owned directory is what keeps that gap shut.

**One JVM, CDK in a class loader of its own (not on the class path).** The JVM is Orthonym's: `orthonym.jvm_bridge` starts it with exactly the OPSIN and centres jars on its class path (`jpype.startJVM(..., classpath=[opsin, centres])`) and refuses a JVM that someone else started, so the service cannot start one with CDK on the class path. Adding the jar afterwards through JPype's `addClassPath` would work (JPype 1.7.1 loads it into its own loader even when the JVM is running), **but it would not be CDK 2.8 that parses**: the centres jar bundles 771 classes of its own CDK, `SmilesParser` among them (a different build from the one in `cdk-2.8.jar`: another MD5), and the OPSIN jar bundles log4j. On one class path whichever jar comes first wins, so appended CDK would be shadowed by centres' parser, and CDK put in front would swap the CDK that centres' CIP labelling runs on. Measured with this JVM and these jars: with `addClassPath` after the start, `SmilesParser`, `MDLV2000Writer` and `CDK` are loaded from `centres-cli-1.2.1.jar` and `CDK.getVersion()` says **2.9** (the CDK that centres bundles); on a class path `[opsin, centres, cdk]` the same; only with cdk-2.8.jar first do they come from 2.8. So `cdk-2.8.jar` is opened by a `java.net.URLClassLoader` whose parent is the platform loader (the JDK and the jar, nothing else), exactly what the earlier version had (a JVM started with `-Djava.class.path=cdk-2.8.jar`), and OPSIN and centres are untouched (the 12 Figure 2 names are still named and read back in the same run). `CdkLayout.start` proves the isolation at every start: the `SmilesParser` it binds must be loaded from the jar file whose hash was checked (`cdk_loaded_from` in the "naming process ready" log line), and `CDK.getVersion()` must say `2.8`. The class loader is created once, when the naming process starts, after Orthonym's warm-up has started the JVM; it is fixed from then on and nothing is loaded per request.

**Two things JPype does that cost memory, and what the service does about them.** Both were found by a soak (16,000 structures through the naming process grew it from 300 MB to 536 MB with no plateau, while the Java heap after a collection stayed at 38 MB, so it was native memory and Python objects, not CDK). (1) JPype attaches every Python thread that calls Java and does not release the attachment when the thread ends: a thread per call, which was the first design, leaked about 8 KB per structure. Hence the one worker thread. (2) JPype keeps about 1 KB and three Python objects (a traceback, its frame and a function made from the Java stack) of every Java exception it turns into a Python one, for ever: not freed by a Python or a Java collection, 50,000 declines cost 50 MB. A decline is the normal outcome of a fallback, so the parse is called through a `java.lang.invoke` method handle (`MethodHandles.catchException`) that catches the parser's `Exception`s in Java and returns null: the SMILES is declined and no Python exception exists. A Java `Error` (out of memory, stack overflow) is not caught, reaches Python, and has the process replaced. A failure of the layout or the writer is still an ordinary Java exception (and costs its 1 KB), which has not been seen on any SMILES the parser accepted. Measured after the fix: the naming process stays at 363 MB from the 4,000th to the 16,000th structure, and 50,000 declines add no Python object (a slow test checks both: objects, threads and memory over 6,000 structures).

**The pipe.** Same pipe and same JSON-only rule as names, one message each way per structure: `["cdk", rid, smiles, soft_limit_s]` and back `["cdk_result", rid, {"molfile": str|null, "reason": str|null}, recycle]`. The parent accepts a reply only if the tag is `cdk_result`, the id is the request's, the payload is exactly those two keys holding plain strings or nulls that encode as UTF-8, **exactly one of them is set**, and a molblock has the shape of a V2000 file (`V2000` in the counts line, `M  END` last) and is at most **256 KiB** (`MAX_MOLFILE_BYTES`; the pipe carries 1 MiB at most, and a 300-atom molblock is about 28 KB (a full V2000 file of 999 atoms about 92 KB)). Anything else is that structure's `engine_error` and the process is replaced, as for names. The engine also refuses to hand on a block whose counts line disagrees with the molecule CDK wrote it from.

**Limits and deadlines (bounded like names).**

- **SMILES**: 1 to 2000 characters, printable ASCII, no whitespace inside, as for names (422 otherwise; the naming process checks the length again).
- **Atoms and bonds: 300 each, because DECIMER cannot write more.** The OCSR model decodes at most 302 tokens (`assets/max_length.pkl` holds 302, read from the pickle opcodes; `services/ocsr/README.md` measures the cut-off; the last step is the end token). Every atom of a SMILES costs at least one token and every ring closure two, so a connected structure of n atoms and r rings needs n + 2r tokens and has n - 1 + r bonds: at most 301 atoms and 300 bonds. The service takes 300 and 300 and declines more (`too_large`), **after the parse and before the layout**. Past that CDK's layout is slow and the one process is busy for all of it: measured: a chain of 300 takes 0.7 s, `C(C)(C)` x100 (300 atoms, which DECIMER cannot write: that is 700 tokens) 1.5 s, `C(C)(C)` x285 (855 atoms) **19 s**, a chain of 999 8 s and a chain of 2000 44 s; the decline costs milliseconds. (The cap also keeps well clear of the V2000 limit of 999 atoms and 999 bonds: CDK's writer writes `1000999` into the counts line for 1000 atoms and raises nothing.) A 20-structure batch of the 855-atom case used to hold the process for the whole 60 s budget, replace the JVM four times and make a parallel `/names` wait 62 s.
- **Soft limit** `CHEM_CDK_TIMEOUT_S` (10 s) or what is left of the request budget, whichever is smaller. A call into the JVM does not return to Python until Java is done, so the signal-based limit that Orthonym uses for names cannot interrupt it, and a Java thread cannot be stopped (`Thread.stop` is gone in Java 21). The conversions therefore run in **one long-lived worker thread** (a daemon, started once; its context class loader is CDK's) that the process waits for with a timeout. A conversion that is not finished in time is a `timeout` result, **and the process is replaced** after the answer is delivered (its runaway Java thread would keep a core busy for ever otherwise; the supervisor's existing `recycle_requested` path). A Java `Error` (out of memory, stack overflow) is an `engine_error` and a replacement too; a Java `Exception` is a decline. **After the first `timeout` in a `/cdk` batch the rest of that batch is answered `timeout` without being sent to the engine** (a timeout costs a replacement of about 3.5 s, and a slow batch would otherwise cost one per structure; names are not treated this way, because Orthonym puts its state back after a timeout and the process lives on).
- **Hard deadline**: soft limit plus 5 s, then the parent kills the process with SIGKILL (a stuck wait), as for names. A request is bounded by `CHEM_BATCH_TIMEOUT_S` (60 s); structures after that are `timeout`.
- One structure at a time on the one pipe; a `/cdk` batch and a `/names` batch take turns (one slot, 4 may wait, 15 s, then 503 with `Retry-After: 5`).

**The endpoint** mirrors `/names`: body `{"smiles": ["...", ...]}`, 1 to 20 SMILES (the same rules each, 256 KiB body cap, 413 / 422 / 499 / 503 / 500 with exactly the shapes above), and `200 {"results": [...]}` with one result per SMILES **in the same order**, each exactly these two keys:

```json
{"results": [
  {"molfile": "\n  CDK     10232605302D\n\n 11 11  0  0  0  0  0  0  0  0999 V2000\n ...M  END\n", "reason": null},
  {"molfile": null, "reason": "invalid_smiles"}
]}
```

| `reason` | When |
|---|---|
| `null` | `molfile` is the V2000 molblock. |
| `invalid_smiles` | CDK's parser cannot read the SMILES (an open ring, an aromatic atom outside a ring, an aromatic ring that cannot be kekulised, ...), or it has no atoms. |
| `layout_failed` | CDK read it, but the layout or the writer raised. |
| `too_large` | More than 300 atoms or 300 bonds (more than DECIMER can write), or a molblock over 256 KiB (a 300-atom molblock is about 28 KB). |
| `timeout` | Out of time (see above), or the request budget was spent before it was tried. |
| `engine_error` | The engine raised for this structure, or its process died or broke the protocol. Logged with the exception type and frames only. |
| `engine_unavailable` | No naming process could be started for this structure. |

Whatever goes wrong with one structure is that structure's entry, never the request's. Only when not one structure could be tried is the answer 503 (`{"detail": "naming engine unavailable"}`, `Retry-After: 5`). A SMILES CDK cannot read is **never a 422** (it passed the request rules): it is that entry's `invalid_smiles`.

**What the molblock is.** CDK's reading of the SMILES, with hydrogens implicit, the 2D coordinates from the structure diagram generator (flat, bond length 1.5), aromatic rings kekulised (alternating double bonds), charges as `M  CHG`, and a stereocentre as a wedge bond (`C[C@H](N)C(=O)O` has one). A structure that breaks a valence rule is written as drawn (a carbon with five neighbours is five bonds), which is the point: the UI shows what was recognised. The worker, not this service, decides what to do with it.

**Readiness.** The startup selftest also has CDK lay caffeine out and requires a molblock of exactly 14 atoms and 15 bonds (C8H10N4O2: 14 heavy atoms, 13 + 2 rings); the service does not start otherwise. `/readyz` keeps **two** passes, one for names (a name OPSIN read back in full, or the selftest) and one for CDK (a molblock, or the selftest), each reused for 30 s: good names never keep readiness green while CDK is broken, and the other way round. Declines and timeouts refresh neither.

**Logs.** `cdk`: structures, laid_out, declined, timeouts, ms. Never a SMILES or a molblock. CDK's own log4j is not wired to anything (it has no configuration here), so it writes nothing.

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
- **The pipe carries JSON, never pickle**, in both directions. Orthonym's names can be `str` subclasses (`PrefixName` carries a derivation object; others exist): pickling one makes the parent import its class and with it Orthonym and RDKit (a web process of 42 MB became 104 MB), and a pickle read from a pipe is code. Three layers keep plain strings on the wire: the policy (`result_from_row`) and `NameResult.as_dict` coerce every field to an exact `str`, and the parent accepts a reply only if it is exactly four fields that are plain strings or nulls that can be written as UTF-8 (a lone surrogate such as `"\ud800"` is valid JSON and would make the HTTP layer fail). Anything else is that structure's `engine_error` and the process is replaced, never a failed request that loses the names already made: a reply that is not a JSON array that starts with a word (a number, an object, `null`), nesting deep enough to overflow the parser, bytes that are not JSON, and a message over 1 MiB, which is refused without being read (`recv_bytes(maxlength=1 << 20)`; the same limit applies to the parent's requests). With the pinned Orthonym no real name could be made to come out as a subclass (the silyl ether `C[Si](C)(C)O[Si](C)(C)C` is a plain `str` at the `valid` tier), so the fast tests prove each layer with a subclass of their own, and a slow test checks on the real engine that the web process never has `orthonym` or `rdkit` in `sys.modules`.
- **The child runs in a scratch directory** (`decimer-chem-*` under the temporary directory, created by the parent, removed by `stop()` and by a `start()` that fails, the same for every replacement; a parent that is killed with SIGKILL cannot clean up, so its directory stays in the temporary directory), so that a JVM crash report (`hs_err_pid*.log`, which holds memory contents) lands there and not in the app directory. Relative path settings (`CHEM_JARS_DIR`, `ORTHONYM_JAR_DIR`, `ORTHONYM_CACHE_DIR`, `XDG_CACHE_HOME`, `JAVA_HOME`) are made absolute first.
- On Linux the child asks the kernel to **SIGKILL it when its parent dies** (`prctl(PR_SET_PDEATHSIG)`, the result checked), so a `kill -9` or OOM kill of the service cannot leave a stuck JVM behind (and `java` itself cannot be started from the child, see above).
- **The JVM starts once**, when a child starts, never per request.
- **CDK runs in this same process and on the same pipe**, one structure at a time like the names, with the same hard deadline and failure rules. Its soft limit works differently (a thread, not SIGALRM) and a timeout always replaces the process: see "CDK fallback".

**A timeout is a result, not an error: 200 with `name: null`, `tier: null`, `reason: "timeout"`, not a 504.** In a batch a slow structure costs its own entry, not the 19 good names next to it. Once the request budget (`CHEM_BATCH_TIMEOUT_S`, 60 s) is spent, the structures not yet tried are declined as `timeout` as well. The caller must therefore set its HTTP timeout **above the 15 s queue wait plus `CHEM_BATCH_TIMEOUT_S` plus about 10 s** (a hard kill and the next structure), say 90 s. The UI treats a timeout like any other decline ("no verified name"); a later edit asks again.

## Selftest and `/readyz`

Two selftests, one for each half of the service (the CDK one is described under "CDK fallback": a molblock of 14 atoms and 15 bonds for caffeine; `/readyz` keeps a separate pass for each). The names one:

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

## Java and jars

Java is only in this service. The jars are not downloaded at runtime: the service checks all three at `CHEM_JARS_DIR` (OPSIN and centres against Orthonym's pinned SHA-256, `cdk-2.8.jar` against its own pin in `decimer_chem.cdk`) **before it builds the engine**, and a missing one is a `RuntimeError` that names the path (and says how to supply it), unless `CHEM_ALLOW_JAR_DOWNLOAD=1`. A jar the service cannot read (Orthonym writes them with mode `0600`) is a `RuntimeError` that says so and suggests `chmod 0644` or `chown`. It also switches Orthonym's own first-use download off (`ORTHONYM_NO_DOWNLOAD=1`) in the naming process. A slow test starts the engine and names a structure with dead proxy variables and every socket connection and download function replaced by one that raises.

| Jar | Version | Size | SHA-256 |
|---|---|---|---|
| `opsin-cli-2.9.0-jar-with-dependencies.jar` ([release](https://github.com/dan2097/opsin/releases/download/2.9.0/opsin-cli-2.9.0-jar-with-dependencies.jar)) | OPSIN 2.9.0 | 14,335,652 B | `c2e29326c281f87b59a05d934d8589adac6e9d17b95b984931b3e739111b360f` |
| `centres-cli-1.2.1.jar` ([release](https://github.com/SiMolecule/centres/releases/download/1.2.1/centres.jar), published as `centres.jar`) | centres 1.2.1 | 2,291,742 B | `6c5d5cafa9393ed7b667a2340222348cd15a9bbdd0d1b92518229f26cc5e87fb` |
| `cdk-2.8.jar` ([release](https://github.com/cdk/cdk/releases/download/cdk-2.8/cdk-2.8.jar), the `cdk-2.8` release of github.com/cdk/cdk) | CDK 2.8 bundle (the file the earlier version ships) | 47,174,875 B | `5dff6e365ecc21020e44aa31861b36a9cce34c51ed0b8713b8d25275552239b4` |

The first two are the checksums Orthonym pins in `orthonym/jars.py` (it verified the downloads); the CDK hash is that of the earlier version's file and of a fresh https download of the release asset (see "CDK fallback"). A slow test checks that the files on disk match the ones written here, and `deploy/weights/SHA256SUMS` lists all three. `scripts/fetch_jars.py` puts them on disk for development (OPSIN and centres through Orthonym's downloader, CDK over https from the release, refused unless its hash is the pinned one) into `~/.cache/orthonym/jars` or `CHEM_JARS_DIR`; `deploy/weights/fetch.sh chem` fetches the same three into the image build context. The tests run with `CHEM_JARS_DIR=<repo>/deploy/weights/chem`.

The JRE used in development: **Eclipse Temurin 21.0.13+9 JRE, x64 Linux** (`OpenJDK21U-jre_x64_linux_hotspot_21.0.13_9.tar.gz`, 52,181,297 B), SHA-256 `a535a1df9c6284d9618872984e026ae0352d1f7e9e0605425c0e6114f47351dd`, which matches both the Adoptium API (`api.adoptium.net/v3/assets/latest/21/hotspot?image_type=jre&os=linux&architecture=x64`) and the `.sha256.txt` published next to the file in https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.13%2B9. Point `JAVA_HOME` at the unpacked directory. An image can use a JRE base layer (Temurin 21 JRE) instead; Orthonym needs Java 11+.

### Installing without downloads

**Orthonym's build hook downloads the jars at install time unless told not to.** `orthonym` is installed from git, and its `hatch_build.py` fetches both jars into the user cache when the wheel is built (`uv sync` does that; seen: 16 MB fetched during a sync). `pyproject.toml` therefore sets `[tool.uv.extra-build-variables] orthonym = { ORTHONYM_SKIP_JAR_PREFETCH = "1" }` (checked: without it the jars land in the cache during `uv sync`, with it nothing is written), so a plain `uv sync` is safe. A build that does not go through uv's project settings (`pip install` of the lock file, for instance) must export `ORTHONYM_SKIP_JAR_PREFETCH=1` itself, or it downloads in a build step you did not mean to give network access.

Other things to know when building the image:

- The jars are written with mode `0600` (Orthonym creates them through `mkstemp`): `chmod 0644` them or `chown` them to the service user when copying them into the image (`fetch.sh` and the Dockerfile do; `cdk-2.8.jar` is baked in the same way). The service only reads them. (Orthonym also opens `<jar dir>/.lock` for appending, but only when it downloads.)
- `JAVA_HOME` must point at the JRE (JPype looks for `lib/server/libjvm.so`).
- Do not set any other `ORTHONYM_*` variable in the image (see "Run").
- Memory: see below. A container limit of **1 GB** is comfortable (two processes: about 50 MB for the web process and about 350 MB for the naming process, flat over the soak, CDK included (it adds a few MB: 296 MB after start with CDK loaded); Orthonym's JVM heap cap of 512 MB was not reached). A replacement process starts while the old one is still being released, so the peak can briefly hold two.
- No `/tmp/hsperfdata_*` files: Orthonym starts its JVM with `-XX:-UsePerfData`, so killing the child leaks nothing.
- The image can be a normal Python 3.12 image plus the JRE; RDKit, lxml and JPype ship wheels (rdkit 2026.3.5, lxml 6.1.3, jpype1 1.7.1, all pinned by Orthonym).

## Measurements

8 cores, no other load, Temurin 21.0.13, `scripts/measure.py`, valid tier:

| What | Result |
|---|---|
| Process start to `/healthz` answering | **2.5 s**. Of that, the naming process takes 2.1 s from spawn to ready: 1.8 s of engine start (jar hashes a few tens of ms, `import orthonym` about 0.3 s, JVM start plus OPSIN's automaton and the first name about 1.2 s) |
| `POST /name`, the 12 Figure 2 compounds, HTTP round trip | min 46 ms, **median 76 ms**, max 602 ms (the first structure in this process that needs the lexicon: the table is built once, since there is no cache) |
| `POST /names`, the 12 plus 8 others (taxol and a saponin among them) | 1.3 s for 20, about 65 ms per structure |
| Structures cut off at the 10 s soft limit | vancomycin, a PEG-50 chain and a PAMAM dendrimer: 10.0 s each, `timeout`. Orthonym's own state was restored every time (`state_not_restored: []`), so the process was kept, and the next name came back at once |
| Soak: 600 names, 20 per request, 23 different structures in rotation | **66 ms per name** |
| CDK in the engine start | **+0.3 s** (0.26 to 0.34 s in three runs: open the jar, bind the classes, lay caffeine out once), measured at load average 10 on an 8-core machine, so the 2.5 s above became about 3 s |
| `POST /cdk`, the 12 Figure 2 compounds, HTTP round trip | min 10 ms, **median 16 ms**, max 28 ms |
| `POST /cdk`, 20 structures | 108 ms (about 5 ms per structure) |
| `POST /cdk`, the slowest 300-atom shapes tried | chain of 300: 0.7 s; `C(C)(C)` x100: 1.5 s (a shape DECIMER cannot write); both inside the 10 s soft limit; the other 300-atom shapes tried (benzene chains, cages, a ring of 300, ring-closure webs) took 0.01 to 0.8 s |
| `POST /cdk`, over the cap: 855 atoms (`C(C)(C)` x285, 19 s to lay out), a chain of 999 (8 s) or of 2000 (44 s) | `too_large` in milliseconds (checked after the parse, before the layout); 20 of the 855-atom structures in one request, beside a `/name`, answer in under 10 s with no process replaced (it was 60 s and four JVMs) |
| Memory, naming process, CDK soak | 296 MB after start; 363 MB flat from the 4,000th to the 16,000th structure (a mix of laid out and declined, in-process engine); 373 MB flat from the 30,000th to the 50,000th decline. The first thousands of structures grow it by about 70 MB (the JVM commits its heap up to the 512 MB cap) and then it stops |
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
uv run pytest                          # fast tests: fake namer / fake engine, no JVM, no network (438 tests, about 1.5 min)
JAVA_HOME=/path/to/jre CHEM_JARS_DIR=<repo>/deploy/weights/chem uv run pytest -m slow -s   # real Orthonym, CDK and JVM, the three jars must be on disk (57 tests, about 2 min)
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Fast tests:

- `test_results.py`: the policy against **literal** expected sets: every allowed (tier, verified) pair is shipped with both labels; `unverified`, `None`, an empty string, an unknown word or a non-string are withheld as `not_round_tripped` in every allowed tier; `pin_unverified` with `unverified` is withheld; the three real carve-out rows; `best_effort` and unknown tiers are withheld as `tier_not_accepted` whatever `verified` says; declines.
- `test_results.py` also pins that every field of a result is an exact `str` when the row holds `str` subclasses (in the shipped, declined and withheld paths and in `as_dict`).
- `test_logs.py`: the redaction filter (logger and level kept, everything else gone, other loggers untouched) and `log_failure` (type and frames, not the message).
- `test_settings.py`: environment parsing (only exactly `1` opts in to downloads; `CHEM_CDK_TIMEOUT_S` like the other timeouts).
- `test_app.py`: with a fake namer: response shape, every 422 path and its one shape, 413 (also chunked), batch order and limits (20 ok, 21 refused), per-item `invalid_smiles`, 503 for an unavailable engine, 500 with an `error_id` and the service staying up, no secrets in the log record of a 500, 503 with `Retry-After` when saturated and when a waiter's 15 s are up, a waiter that hung up is not named, readiness (cache, failure, busy; declines and non-`opsin` names do **not** refresh it), the startup selftest, no SMILES in the logs, production limits.
- `test_supervisor.py`: a **real child process** running a fake engine: results in order from another process, the same process for every call, a hung call killed at the hard deadline and the process replaced in the background, a hang in the middle of a batch costing one entry, the batch budget, a slow replacement waited for only as long as the budget (and not abandoned), a replacement that fails to start (`engine_unavailable`, 503 when nothing could be tried, not retried in a loop), an engine exception (`engine_error`, same process), a crash (batch carries on) and two crashes in a row, a process that asks to be replaced, a cancelled call, startup failures (message, no word, hang), `stop()` during a background replacement, the reaper tolerating `process.close()` failing, the parent-death signal (checked, and: kill -9 the parent while the child is stuck, the child dies), Orthonym's logs redacted, the pipe carrying JSON in both directions (a pickle from the other end is refused and never loaded), a `str` subclass from a module only the child imports arriving as a plain `str` with that module absent from the parent, a reply with a non-string field refused (`engine_error`, process replaced), the scratch directory (created, the same across replacements, removed by `stop()`), relative paths made absolute, a failed start leaving no scratch directory (four ways to fail), the malformed replies above (a scripted "process" answers one request properly and the next with each kind of garbage: the names around it are kept and the replacement serves the rest) and the same for a real 2 MiB reply and a lone surrogate from a real child, and no replacement started after `stop()` (checked with a spy on the spawn, since a late child could otherwise fail for an unrelated reason and hide the bug).
- `test_engine.py`: the allow-list of `ORTHONYM_*` variables (each dangerous one refused, values never echoed, empty values too, the five allowed ones pass), jar checks (missing, wrong checksum, unreadable with the `chmod` hint, one of two, download opt-in, nothing downloaded otherwise), the jar policy, `Orthonym` built with **exactly** `general_fallback=True`, an unreadable SMILES declined by RDKit before Orthonym is called, the policy applied to what the namer returns, the soft limit with a fake that spins in Python (a timeout result, usable afterwards, state left behind asks for a new process and only its keys are logged, refused off the main thread), and `java` refused as a subprocess (as a `FileNotFoundError`) while others still run; the JVM-options variables refused; the lexicon cache switched off by the service, before Orthonym is imported, and not switchable on by an operator.
- CDK, fast (no JVM): `test_results.py` (`CdkResult` is two plain fields, the reason words and limits written out literally, `looks_like_molfile` on eleven inputs); `test_cdk.py` (the thread machinery of `CdkLayout` with the conversion replaced: a result from a thread of its own with CDK's context class loader, 200 calls on that one thread (a thread per call leaks in JPype), a failure to set the loader is every call's error, a SMILES of 2001 characters declined without asking CDK, a conversion past the soft limit is a `timeout` that sets `needs_new_process` and refuses the next call, an exception reaches the caller, not started is an error, `counts_of`); `test_engine.py` (the pin written out and cross-checked against `SHA256SUMS`, `fetch.sh` and the Dockerfile; a missing, wrong, unreadable and right jar; `start()` refuses a missing or wrong CDK jar before any engine is built, with and without `CHEM_ALLOW_JAR_DOWNLOAD`; the engine passes a result through, asks for a new process when a Java thread is left running, and refuses `cdk` before `start`); `test_supervisor.py` (the same process serves names and CDK, CDK's own soft limit, a hang killed at the hard deadline, a hang in the middle of a batch, the batch budget, an engine error, a crash, two crashes in a row, no process at all, a process that asks to be replaced, a cancelled call, names and CDK taking turns; the wire: exactly two fields with exactly one set, a V2000-shaped molblock of at most 256 KiB counted in UTF-8 bytes, seven bad replies from a real process and nine scripted ones, a reply of the wrong tag in either direction); `test_app.py` (`/cdk`: shape and order, declines in place, 20 ok and 21 refused, 2000 and 2001 characters, ten bad bodies, not JSON, 413 also chunked, 503 and 500 shapes, the slot shared with `/names`, counts-only logs without a SMILES or a molblock; the selftest: six wrong answers from CDK stop the start, both selftests run again when the pass is old, `/readyz` 503 when only CDK or only the names broke, and neither half's successes keep the other green).

Slow tests (`test_integration.py`): caffeine, ethanol and aspirin with exactly the names in Orthonym's README; the 12 Figure 2 compounds in one batch (each named, fully OPSIN-read-back, with the tier pinned, and checked independently by the OPSIN CLI; the table above); no tier wider than `valid` (three structures that only `best-effort` names are declined); the three carve-out cases through the service (not shipped under their carve-out names) and through a default-tier engine (all three withheld as `not_round_tripped`); a `pin_unverified` name with a full read-back is served (sphingosine); invalid SMILES (422 for `/name`, per-item in a batch); declines for other reasons (`[Na+]`); one process and one JVM across requests; a real Orthonym timeout (a dendrimer with a 1 s limit comes back as `timeout` in under 10 s, with no process killed and the same pid after); engine start and naming with the network unavailable; the web process never imports `orthonym` or `rdkit` after naming the silyl ethers and stays under 80 MB; no cache pickle is read, written or changed (three variants); the jars on disk equal the hashes in this README; `python -m decimer_chem` as a program (JSON on every log line, no structure names in the logs, clean exit on SIGTERM).

Slow tests for CDK (`test_cdk_integration.py`, 32 tests, real JVM; RDKit is the independent oracle: it decides what it cannot read and reads back the molblocks): the jar on disk is 47,174,875 bytes with the pinned hash, which is in the README and in `SHA256SUMS`; the centres jar bundles a `SmilesParser` that differs from 2.8's (the reason CDK has a class loader of its own); the engine reports CDK 2.8 loaded from the checked jar; caffeine comes back as 14 atoms and 15 bonds and RDKit's reading of it has caffeine's canonical SMILES; **four SMILES that RDKit rejects and CDK accepts**, each pinned with hand-counted atoms, bonds, elements, bond orders (the ring kekulised) and the neighbours of the atom that breaks the valence rule, and a flat layout (z = 0, no coordinates all zero, no two atoms closer than 0.5, every bond 1.4 to 1.6 long): `CC(C)(C)(C)c1ccccc1`, `C[N](C)(C)(C)C`, `C1=CC=CC=C1=O`, `CS(=O)(=O)(=O)(=O)C`; six SMILES both parsers reject, declined as `invalid_smiles`; one batch with declines in place and in order; a salt (charges) and a stereocentre (one wedge); 300 atoms laid out and 301 declined as `too_large`; the 855-atom, 999-chain, 2000-chain and 999-fragment SMILES each declined in under 3 s, and 20 of the 855-atom case beside a `/name` answer in under 10 s with no process replaced; 2001 characters a 422; a 300-atom structure that takes 1.5 s with a 0.3 s soft limit is a `timeout`, the rest of its batch is a `timeout` too and never sent, the process is replaced once (no process killed, the old one gone, the next call works); the loader built with the system loader as parent (centres' CDK 2.9 visible) is refused at start, and each of the two start-up checks refuses on its own; a jar that is not the official one, one that differs by one byte, and a missing one are refused at start; engine start and layout with every Python network function denied; the web process never imports `jpype`, `orthonym` or `rdkit` for CDK; names and CDK from concurrent requests share one process and `/readyz` stays ready; and 6,000 structures (half of them declines) after a 4,000-structure warm-up add under 300 Python objects and no thread to the process (a thread per call, or a Java exception per decline, each fails it by a factor of 30 or more; the resident size is printed, not asserted, because it also steps with the JVM's heap commits). `test_integration.py` also checks the three jars on disk against the README and that the `python -m decimer_chem` logs carry neither the SMILES nor a molblock for `/cdk`.

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
| a reply that is not a tagged array accepted; deep nesting escaping as `RecursionError`; a reply of any size read; lone surrogates accepted | `test_supervisor.py::test_a_reply_that_makes_no_sense_costs_that_structure_only_and_the_process_is_replaced` (one case each), `test_exchange_raises_engine_failure_...`, `test_parse_result_refuses_text_that_is_not_utf8` |
| a failed `start()` leaving its scratch directory | `test_supervisor.py::test_a_failed_start_removes_the_scratch_directory` (and the session check in `conftest.py`) |
| a replacement started after `stop()` | `test_supervisor.py::test_no_replacement_starts_once_the_namer_is_stopped` |
| the `java` refusal raised as a `RuntimeError` | `test_engine.py::test_a_java_subprocess_is_refused_in_the_naming_process` |
| the CDK hash check removed | `test_engine.py::test_a_cdk_jar_with_another_checksum_is_refused_with_both_hashes`, `test_start_refuses_a_bad_cdk_jar_before_any_engine_is_built_even_with_the_download_opt_in`; with the real JVM `test_cdk_integration.py::test_a_cdk_jar_that_is_not_the_official_one_is_refused_before_the_jvm_starts`, `test_a_cdk_jar_that_differs_from_the_official_one_by_one_byte_is_refused` |
| the CDK pin changed in one place | `test_engine.py::test_the_cdk_jar_is_pinned_to_the_file_legacy_uses_and_the_pin_is_everywhere_it_must_be` |
| a SMILES CDK cannot read not mapped to a decline (the exception escapes, so it is an `engine_error`), or mapped to `layout_failed` | `test_cdk_integration.py::test_a_smiles_both_parsers_reject_is_declined`, `test_a_batch_comes_back_in_order_with_declines_in_place` |
| the parent accepting a reply with a molblock and a reason; a CDK timeout mapped to `engine_error`; the reply tag not checked | `test_supervisor.py::test_the_parent_accepts_only_a_molblock_or_a_reason`, `test_a_cdk_call_that_ignores_the_soft_limit_is_killed_at_the_hard_deadline`, `test_a_cdk_reply_that_makes_no_sense_costs_that_structure_only_and_the_process_changes` |
| the atom or bond cap raised back to 999 | `test_cdk_integration.py::test_300_atoms_are_laid_out_and_301_are_declined_before_the_layout`, `test_a_too_large_smiles_is_declined_fast_and_never_reaches_the_layout`, `test_twenty_slow_structures_neither_hold_the_process_nor_make_a_name_wait`; `test_cdk.py::test_the_limits_are_what_decimer_can_write` |
| no give-up after the first CDK timeout of a batch (or the give-up applied to names too) | `test_supervisor.py::test_after_the_first_cdk_timeout_the_rest_of_the_batch_is_a_timeout_not_sent`, `test_a_cdk_hang_killed_at_the_hard_deadline_also_gives_up_the_rest_of_the_batch`, `test_a_names_timeout_does_not_give_up_its_batch`; with the real JVM `test_cdk_integration.py::test_a_conversion_that_takes_too_long_is_a_timeout_and_the_process_is_replaced` |
| the class loader's location check, or its version check, deleted (each alone) | `test_cdk_integration.py::test_start_refuses_a_shadowed_cdk_and_a_cdk_of_another_version` (builds the loader with the system loader as parent, where centres' CDK 2.9 is visible, and with the expected version changed, so each check fires on its own) |
| the parent not enforcing the 256 KiB molblock cap | `test_supervisor.py::test_the_size_cap_on_a_molblock_is_exact` |
| batch order reversed (in the supervisor, in the endpoint, or seen with the real JVM) | `test_supervisor.py::test_cdk_results_come_back_in_order_from_the_process_that_names`, `test_app.py::test_cdk_returns_exactly_two_keys_per_result_in_request_order`, `test_cdk_integration.py::test_a_batch_comes_back_in_order_with_declines_in_place` |
| no replacement of the process after a CDK timeout (its Java thread would run on) | `test_cdk.py::test_a_conversion_past_the_soft_limit_is_a_timeout_and_the_process_is_to_be_replaced`, `test_engine.py::test_a_cdk_call_that_left_a_java_thread_running_asks_for_a_new_process`; with the real JVM `test_cdk_integration.py::test_a_conversion_that_takes_too_long_is_a_timeout_and_the_process_is_replaced` |
| the soft limit not enforced (the wait without a timeout); the SMILES length not checked in the engine | `test_cdk.py::test_a_conversion_past_the_soft_limit_is_...` (it ends after its own 30 s bound, failing), `test_a_smiles_over_the_cap_is_declined_without_asking_cdk` |
| CDK given the names' soft limit | `test_supervisor.py::test_cdk_is_given_its_own_soft_limit_not_the_one_for_names` |
| CDK put on the class path / JPype's shared loader instead of a loader of its own (centres' bundled classes shadow it: `SmilesParser` from `centres-cli-1.2.1.jar`, `CDK.getVersion()` 2.9) | the start fails: the code-source check ("loaded from ... centres-cli-1.2.1.jar"), and with that check removed the version check ("is CDK 2.9"); every real-JVM test then errors, among them `test_cdk_integration.py::test_cdk_is_loaded_from_the_checked_jar_and_is_version_2_8` |
| a worker thread per call; declines raised as Java exceptions (JPype keeps each) | `test_cdk.py::test_every_conversion_runs_on_the_same_thread_so_the_jvm_attaches_it_once`, `test_cdk_integration.py::test_many_structures_neither_declines_nor_layouts_grow_the_process` (10,801 and 163,096 objects against a limit of 300) |
| the CDK selftest not run at start; its counts not checked | `test_app.py::test_startup_has_cdk_lay_caffeine_out_before_serving`, `test_startup_fails_when_cdk_cannot_lay_caffeine_out` |
| one readiness pass for names and CDK | `test_app.py::test_good_names_do_not_keep_readyz_green_while_cdk_is_broken` |

`tests/conftest.py` also fails the session if the tests leave a `decimer-chem-*` directory behind.

Four tests were first found not to catch their bug, and now assert the invariant directly: the cancelled-call test passed by luck of a race, the INFO-logs test was hidden behind the redaction filter, the stop test passed because the late replacement happened to fail on its own (its scratch directory was already gone), and the cache-pickle test named only structures that never consult the lexicon (it passed with the cache on). The one thing a mutation cannot show on the real engine is the coercion of `str` subclasses, because the pinned Orthonym does not produce one for any structure tried; the fast tests cover each layer with a subclass of their own.

Every wait that depends on the code under test is bounded, so a bug fails its test instead of hanging it.

## Things to know about Orthonym

- `name_tiered` raises `ValueError` for a SMILES RDKit cannot read, but an **empty string is not an error**: it is a decline (`UNNAMEABLE`). The HTTP layer rejects empty input before it gets there, and the engine checks every SMILES with `Chem.MolFromSmiles` first; a `ValueError` out of Orthonym itself for a SMILES RDKit read is therefore an `engine_error`, not an "invalid SMILES".
- Orthonym's `Orthonym()` constructor raises `JarUnavailable` (a `RuntimeError`) if a jar is missing; the service checks first so the message is ours.
- RDKit writes "non-ring atom marked aromatic" and "Can't kekulize mol" lines to stderr while Orthonym probes candidate names; the naming process turns RDKit's log streams off.
- Orthonym's JVM bridge falls back to a `java` subprocess per call if JPype or the JVM is unavailable ("fallback, never failure"); the service treats that as a startup error and refuses the subprocess (see Run).
- Orthonym's names are not always plain `str`: it has `str` subclasses that carry objects (`PrefixName`, `CascadeRefusal`, `AlphaKey`). Nothing of that crosses the pipe (see the process model).
- Orthonym's lexicon cache is a pickle written at run time; it is off (see "What the service writes to disk"). The morpheme table is rebuilt in each naming process the first time a structure needs it.
- `ORTHONYM_GC_TUNE=on` (Orthonym's batch tuning) is one of the refused variables; it was not tried.

## Things to know about CDK

- CDK 2.8's `SmilesParser` accepts what RDKit rejects on valence (a carbon with five neighbours, a nitrogen with five, a sulfur with ten bonds, a carbonyl on a ring carbon that already has its double bond), and still refuses an open ring or branch, an aromatic atom outside a ring, and an aromatic ring that cannot be kekulised (`c1cccc1`, `n1cccc1`). The slow tests pin four of the first kind and six of the second.
- CDK's `MDLV2000Writer` writes `1000999` into the counts line for a molecule with 1000 atoms and raises nothing. The service's cap (300 atoms and 300 bonds, from DECIMER's 302-token limit) is far below it; more is `too_large`.
- A call into the JVM cannot be interrupted from Python, so CDK's soft limit is a worker thread with a timeout and a timeout costs the process (see "CDK fallback").
- JPype leaks a little for every Python thread that calls Java and for every Java exception it converts (see "CDK fallback"); the service uses one thread and catches the parser's exceptions in Java. Orthonym's own use of JPype is unaffected (its JVM calls come from the main thread and do not throw in the normal case).
