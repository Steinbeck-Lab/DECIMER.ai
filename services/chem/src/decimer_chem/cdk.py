"""CDK 2.8 in the naming process's JVM: a SMILES to a V2000 molblock with CDK's own 2D layout.

The fallback for SMILES RDKit rejects. It does what the earlier Laravel version of decimer.ai does
(`app/Python/check_smiles_validity.py`): `SmilesParser(SilentChemObjectBuilder)`, then
`StructureDiagramGenerator` for the 2D coordinates, then `MDLV2000Writer`; a SMILES CDK cannot
read is a decline, not an error. Nothing here is imported by the HTTP process.

**CDK is loaded through a class loader of its own, not put on the JVM's class path.** The JVM is
started by Orthonym (`orthonym.jvm_bridge`), with exactly the OPSIN and centres jars on its class
path, and it refuses a JVM that someone else started. The centres jar bundles 771 classes of its
own CDK (`SmilesParser` among them, a different build from the one in cdk-2.8.jar), and the OPSIN
jar bundles log4j: a cdk-2.8.jar appended to that class path would be shadowed by them (the
`SmilesParser` that ran would not be 2.8's), and one put in front of them would change the CDK that
centres runs on. So `cdk-2.8.jar` is opened by a `URLClassLoader` whose parent is the platform
loader: it sees the JDK and the jar and nothing else, exactly as in the earlier version (a JVM
started with `-Djava.class.path=cdk-2.8.jar`), and OPSIN and centres are left as they were.
`start` proves it: the `SmilesParser` it binds must come from the jar file the hash was checked
on, and `CDK.getVersion()` must say 2.8.

**The soft deadline.** A call into the JVM does not return to Python until Java is done, so the
signal-based limit that Orthonym uses for names cannot interrupt it, and a Java thread cannot be
stopped (`Thread.stop` is gone in Java 21). The conversions therefore run in one Python thread, and
the caller waits for each with a timeout. A conversion that is not done in time is a `timeout`
result, and since the thread is still running in the JVM, the process must be replaced:
`needs_new_process` is set, the engine asks for a new one, and the supervisor kills this one after
the answer is delivered. If the wait itself never returns, the parent's hard deadline kills the
process as it does for names.

**One long-lived thread, not one per call.** JPype attaches every Python thread that calls Java to
the JVM and does not give the attachment back when the Python thread ends: a thread per call leaked
about 8 KB of native memory and three Python objects per structure (measured: the naming process
grew from 300 MB to 536 MB over 16,000 structures without reaching a plateau, while the Java heap
after a collection stayed at 38 MB). The worker thread is started once, is a daemon (so it never
holds the process up at exit) and attaches once.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .results import (
    INVALID_SMILES_REASON,
    LAYOUT_FAILED_REASON,
    MAX_MOLFILE_BYTES,
    MAX_SMILES_CHARS,
    TIMEOUT_REASON,
    TOO_LARGE_REASON,
    CdkResult,
    looks_like_molfile,
)

#: The jar the earlier version uses: the CDK 2.8 bundle from
#: https://github.com/cdk/cdk/releases/download/cdk-2.8/cdk-2.8.jar (release `cdk-2.8` of
#: github.com/cdk/cdk, 2022-09-14), 47,174,875 bytes. The hash is that of the earlier version's copy
#: (`app/Python/cdk-2.8.jar`) and of a fresh download; deploy/weights/SHA256SUMS lists the same.
CDK_JAR_NAME = "cdk-2.8.jar"
CDK_JAR_SHA256 = "5dff6e365ecc21020e44aa31861b36a9cce34c51ed0b8713b8d25275552239b4"
CDK_VERSION = "2.8"

#: What the layout is asked to do: at most 300 atoms and 300 bonds, which is more than DECIMER can
#: write. The OCSR model decodes at most 302 tokens (`assets/max_length.pkl` holds 302; the last
#: step is the end token), and every atom and every ring closure of a SMILES costs at least one
#: token: a connected structure of n atoms and r rings needs n + 2r tokens and has n - 1 + r bonds,
#: so at most 301 atoms and 300 bonds. The cap is 300 and 300. Past it CDK's layout is slow, not
#: useful: measured here, a chain of 300 takes 0.7 s, `C(C)(C)` x100 (300 atoms, which DECIMER
#: cannot write: 700 tokens) 1.5 s, and `C(C)(C)` x285 (855 atoms) 19 s, a chain of 2000 44 s, with
#: the one process busy for all of it. (A V2000 file itself holds 999 atoms and 999 bonds, three
#: digits each in the counts line; CDK's writer writes `1000999` for 1000 atoms and raises
#: nothing, so this cap also keeps clear of that.)
MAX_ATOMS = 300
MAX_BONDS = 300

_CDK = "org.openscience.cdk"
_TIMEOUT = CdkResult(None, TIMEOUT_REASON)


class _Job:
    """One conversion handed to the worker thread: the SMILES in, a result or an exception out."""

    def __init__(self, smiles: str) -> None:
        self.smiles = smiles
        self.done = threading.Event()
        self.outcome: CdkResult | BaseException | None = None


def counts_of(molfile: str) -> tuple[int, int] | None:
    """(atoms, bonds) from the counts line of a V2000 molblock, None if it cannot be read."""
    lines = molfile.splitlines()
    if len(lines) < 4:
        return None
    try:
        return int(lines[3][0:3]), int(lines[3][3:6])
    except ValueError:
        return None


class CdkLayout:
    """Converts one SMILES at a time to a molblock with CDK. `start` binds the classes once."""

    def __init__(self, jar: Path) -> None:
        self._jar = jar
        self._classes: dict[str, Any] = {}
        self._loader: Any = None
        #: True once a call ran out of time: its Java thread is still going and cannot be stopped,
        #: so this process must be replaced and must not be asked again.
        self.needs_new_process = False
        self._jobs: queue.SimpleQueue[_Job] = queue.SimpleQueue()
        self._worker: threading.Thread | None = None

    def start(self) -> dict[str, object]:
        """Open the jar in its own class loader and bind the classes; raise if it is not CDK 2.8.

        The caller has checked the jar's SHA-256. Needs the JVM, which Orthonym started."""
        import jpype
        from jpype import JClass

        if not jpype.isJVMStarted():
            raise RuntimeError("CDK needs the JVM that Orthonym starts, and it is not running")
        url = JClass("java.io.File")(str(self._jar)).toURI().toURL()
        self._loader = JClass("java.net.URLClassLoader")([url], self._parent_loader())

        def bind(name: str) -> Any:
            return JClass(f"{_CDK}.{name}", loader=self._loader)

        self._classes = {
            "builder": bind("silent.SilentChemObjectBuilder"),
            "parser": bind("smiles.SmilesParser"),
            "layout": bind("layout.StructureDiagramGenerator"),
            "writer": bind("io.MDLV2000Writer"),
            "string_writer": JClass("java.io.StringWriter"),
            "thread": JClass("java.lang.Thread"),
            "exception": JClass("java.lang.Exception"),
            "objects": jpype.JArray(jpype.JObject),
        }
        self._classes["parse"] = self._parse_or_null(bind("interfaces.IAtomContainer"))
        loaded_from = self._classes["parser"].class_.getProtectionDomain().getCodeSource()
        location = Path(unquote(urlparse(str(loaded_from.getLocation())).path)).resolve()
        if location != self._jar.resolve():
            raise RuntimeError(
                f"the CDK SmilesParser was loaded from {location}, not from {self._jar}: "
                "another jar is shadowing CDK"
            )
        version = str(bind("CDK").getVersion())
        if version != CDK_VERSION:
            raise RuntimeError(
                f"{self._jar} is CDK {version}, but the service is built for {CDK_VERSION}"
            )
        return {"cdk": version, "cdk_loaded_from": str(location)}

    @staticmethod
    def _parent_loader() -> Any:
        """The parent of CDK's class loader: the platform loader, which sees the JDK and nothing
        else. (A test overrides this with the system loader, where centres' CDK 2.9 is visible, to
        show that the two checks in `start` fire.)"""
        from jpype import JClass

        return JClass("java.lang.ClassLoader").getPlatformClassLoader()

    def _parse_or_null(self, container: Any) -> Any:
        """A method handle for `SmilesParser.parseSmiles` that returns null instead of throwing.

        A decline is what a fallback mostly produces, and JPype keeps about 1 KB of every Java
        exception it turns into a Python one for ever (a traceback, its frame and a function built
        from the Java stack: measured at 3 objects and about 1 KB per decline, not released by a
        Python or a Java collection; 50,000 declines cost 50 MB). The handle catches the parser's
        `Exception`s in Java, so a decline never becomes a Python exception. An `Error` (out of
        memory, stack overflow) is not caught and does reach Python."""
        import jpype
        from jpype import JClass

        handles = JClass("java.lang.invoke.MethodHandles")
        java_class = JClass("java.lang.Class")
        exception = self._classes["exception"].class_
        parse = handles.publicLookup().findVirtual(
            self._classes["parser"].class_,
            "parseSmiles",
            JClass("java.lang.invoke.MethodType").methodType(
                container.class_, JClass("java.lang.String").class_
            ),
        )
        on_exception = handles.dropArguments(
            handles.constant(container.class_, None), 0, jpype.JArray(java_class)([exception])
        )
        return handles.catchException(parse, exception, on_exception)

    def molfile(self, smiles: str, timeout_s: float) -> CdkResult:
        """The molblock for `smiles`, or a decline, within `timeout_s`.

        Declines: `invalid_smiles` (CDK cannot read it, or it has no atoms), `too_large` (over the
        atom and bond cap above, or over the size cap), `layout_failed` (the layout or the writer
        raised), `timeout`. Anything that is not a Java `Exception` (an `Error` such as running out
        of memory) and any bug here is raised, for the caller to make an engine error of it."""
        if not self._classes:
            raise RuntimeError("CDK was not started")
        if self.needs_new_process:
            raise RuntimeError("this process must be replaced before it is asked again")
        if len(smiles) > MAX_SMILES_CHARS:
            return CdkResult(None, INVALID_SMILES_REASON)
        if self._worker is None:  # started once, here or at `start`'s warm-up, never per call
            self._worker = threading.Thread(target=self._serve, name="chem-cdk", daemon=True)
            self._worker.start()
        job = _Job(smiles)
        self._jobs.put(job)
        if not job.done.wait(timeout_s):
            self.needs_new_process = True
            return _TIMEOUT
        result = job.outcome
        if isinstance(result, BaseException):
            raise result
        assert result is not None  # noqa: S101  # `done` is set after `outcome`
        return result

    def _serve(self) -> None:
        """The worker thread: one conversion at a time, for as long as the process lives."""
        try:
            # CDK's own log4j and any ServiceLoader look at the thread's context class loader:
            # point it at CDK's, not at the system loader that holds OPSIN's copies.
            self._classes["thread"].currentThread().setContextClassLoader(self._loader)
        except BaseException as exc:  # every job is then answered with it
            failed: BaseException | None = exc
        else:
            failed = None
        while True:
            job = self._jobs.get()
            try:
                if failed is not None:
                    raise failed
                job.outcome = self._convert(job.smiles)
            except BaseException as exc:  # handed to the waiting thread
                job.outcome = exc
            job.done.set()

    def _convert(self, smiles: str) -> CdkResult:
        import jpype

        c = self._classes
        try:
            parser = c["parser"](c["builder"].getInstance())
            molecule = c["parse"].invokeWithArguments(c["objects"]([parser, smiles]))
        except jpype.JException:
            # Java `Exception`s are caught inside the handle, so what reaches here is an `Error`
            # (or the handle itself failing): the JVM is in doubt, and that is not a verdict.
            self.needs_new_process = True
            raise
        if molecule is None:
            return CdkResult(None, INVALID_SMILES_REASON)  # as before: any exception is "invalid"
        if molecule.getAtomCount() == 0:
            return CdkResult(None, INVALID_SMILES_REASON)
        if molecule.getAtomCount() > MAX_ATOMS or molecule.getBondCount() > MAX_BONDS:
            return CdkResult(None, TOO_LARGE_REASON)  # before the layout: it can take many seconds
        try:
            layout = c["layout"]()
            layout.setMolecule(molecule)
            layout.generateCoordinates(molecule)
            laid_out = layout.getMolecule()
            atoms, bonds = int(laid_out.getAtomCount()), int(laid_out.getBondCount())
            if atoms > MAX_ATOMS or bonds > MAX_BONDS:
                return CdkResult(None, TOO_LARGE_REASON)
            sink = c["string_writer"]()
            writer = c["writer"](sink)
            writer.write(laid_out)
            writer.close()
            text = str(sink.toString())
        except jpype.JException as exc:
            self._expect_exception(exc)
            return CdkResult(None, LAYOUT_FAILED_REASON)
        if len(text.encode("utf-8")) > MAX_MOLFILE_BYTES:
            return CdkResult(None, TOO_LARGE_REASON)
        if not looks_like_molfile(text) or counts_of(text) != (atoms, bonds):
            # Never hand on a block that does not say what the molecule is.
            raise RuntimeError("CDK wrote a molblock that does not match its molecule")
        return CdkResult(text, None)

    def _expect_exception(self, exc: BaseException) -> None:
        """Re-raise a Java `Error` (out of memory, stack overflow): the JVM's state is in doubt, so
        that is an engine error and not a verdict on the SMILES. A Java `Exception` is a decline."""
        if not isinstance(exc, self._classes["exception"]):
            self.needs_new_process = True
            raise exc
