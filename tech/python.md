# Python

## 1. What it is

Python is a programming language. You write instructions in plain text files that end in `.py`, and a program called the **interpreter** (the command `python`) reads those files and carries out the instructions one by one. This repository uses Python 3.13 on the developer's machine and in the container image (the project accepts 3.12 or newer, which is what `requires-python = ">=3.12"` in `pyproject.toml` says).

A few words used throughout this file:

- A **module** is one `.py` file. A **package** is a folder of modules that has an `__init__.py` file in it. Here `zero_downtime` is a package, and `zero_downtime/domain/profile.py` is a module inside it.
- A **function** is a named piece of code that you can run by writing its name followed by brackets. A **class** is a recipe for making objects: it says what data an object holds and what it can do. A **method** is a function that belongs to a class.
- A **type hint** is a note such as `value: int` that says what kind of value a name holds. Python does not enforce hints when it runs; a separate tool (mypy, described in `mypy.md`) reads them and reports mistakes.
- An **exception** is a signal that something went wrong. Code can `raise` one, and other code can `catch` it with `try` and `except`. If nobody catches it the program stops and prints where it happened.

## 2. Why this repository uses it

The lesson needs two programs: a web service with realistic latency and start-up delay to roll, scale and drain, and a load generator that counts every failed request while the cluster changes underneath it. Python keeps both small enough to read, and the same language writes the tests that run the four operations and read the generator's verdict. Both programs are laid out in layers (domain, application, infrastructure, interfaces) so you can see how a real service is organised, even though each is small.

## 3. Where it appears

| File or folder | What it does with Python |
| --- | --- |
| `src/zero_downtime/` | The web service: the request profile, the use cases, settings, logging, clocks and the start-up code. |
| `src/loadgen/domain/`, `application/` | The load generator's rules (outcomes, pacing, statistics) and its runner (see `loadgen.md`). |
| `src/loadgen/infrastructure/` | Settings, logging, the real clock and thread pool. |
| `tests/` | The tests (`pytest.md`). |
| `pyproject.toml` | Declares both packages and the tools that check them (`toml.md`). |

## 4. How it works underneath

When you run `python something.py`, the interpreter does these things in order. It starts a **process** (a running program with its own memory). It reads the file, turns it into an internal form, and runs the top-level statements from the first line to the last. A statement such as `import json` finds the module named `json`, runs it once, and gives you its contents under that name. A `def` or `class` statement does not run its body; it creates the function or class and stores it under its name. The body runs later, when somebody calls it.

Names live in **scopes**. A name defined at the top of a module is visible in that whole module; a name defined inside a function exists only while that function runs. Values live in memory and are cleaned up automatically when nothing refers to them any more.

Python finds modules by searching a list of folders (`sys.path`). In this repository the package is installed into a **virtual environment**, a private copy of Python's libraries kept in the `.venv/` folder, so that the versions this project needs do not clash with anything else on the machine. `make install` creates it from the lock file.

Output goes to two numbered channels the operating system gives every process: **standard output** (for results) and **standard error** (for diagnostics). This app writes its log lines to standard error, which is the channel Kubernetes shows when you run `kubectl logs`.

Environment variables (named settings that a process inherits from whoever started it) are read with `os.environ`. A container platform passes configuration to a program this way, which is why `settings.py` reads them.

## 5. Walkthrough of this repository's use

Every file of the two packages that is not a web, HTTP-client or start-up detail is explained here: the web service first, then the load generator's domain, application and infrastructure code. The routes, the server and the generator's pacer, runner and clients have their own notes.

### `src/zero_downtime/__init__.py`

```text
"""A small HTTP service with realistic startup and request latency, for rolling, scaling and draining."""

__version__ = "0.1.0"
```

The docstring (text in triple quotes at the top of a file, kept by Python as the module's documentation) says what the package is: a small HTTP service with realistic start-up and request latency, for rolling, scaling and draining. `__version__ = "0.1.0"` holds the version number. The file's presence makes the folder a package.

### `src/zero_downtime/domain/__init__.py`

```text
"""Domain: rules and values with no I/O. Depends on nothing outside the standard library."""
```

A one-line docstring stating the layer's rule: the domain depends on nothing outside the standard library.

### `src/zero_downtime/domain/errors.py`

```text
"""Named errors for the service."""


class ServiceError(Exception):
    """Base class of every error this service raises on purpose."""


class InvalidProfileError(ServiceError):
    """A request profile has impossible bounds."""
```

A base class `ServiceError(Exception)` for every error the service raises on purpose, and one subclass, `InvalidProfileError`, for a request profile with impossible bounds. A class with only a docstring has no other behaviour; its name is its meaning.

### `src/zero_downtime/domain/profile.py`

```text
"""How a request behaves: how long it takes and how much of that time is spent computing."""

from __future__ import annotations

from dataclasses import dataclass

from zero_downtime.domain.errors import InvalidProfileError


@dataclass(frozen=True, slots=True)
class RequestProfile:
    """Latency is drawn between the bounds; cpu_ms of it is real computation, the rest is waiting (I/O).

    The computation is what makes CPU utilization, and therefore the autoscaler, respond to load.
    """

    min_ms: float = 50.0
    max_ms: float = 200.0
    cpu_ms: float = 2.0
```

`@dataclass(frozen=True, slots=True)` turns the class into a record: Python writes `__init__`, `__repr__` and equality for you; `frozen` forbids changing fields after creation; `slots` stores fields compactly. `RequestProfile` describes how a request behaves: latency is drawn between `min_ms` (50) and `max_ms` (200) and `cpu_ms` (2) of it is real computation, the rest waiting (like waiting for a database). The docstring explains why: computation is what makes CPU use, and so the autoscaler, respond to load.

```text
    def __post_init__(self) -> None:
        """Reject bounds that cannot describe a request."""
        if self.min_ms < 0 or self.max_ms < self.min_ms:
            raise InvalidProfileError(f"need 0 <= min_ms <= max_ms, got {self.min_ms} and {self.max_ms}")
        if not 0 <= self.cpu_ms <= self.min_ms:
            raise InvalidProfileError(f"cpu_ms must be between 0 and min_ms, got {self.cpu_ms}")

    def latency_ms(self, fraction: float) -> float:
        """Map a number in [0, 1] (from an injected random source) onto the latency range."""
        return self.min_ms + (self.max_ms - self.min_ms) * fraction

    def split(self, latency_ms: float) -> tuple[float, float]:
        """Split a latency into (computing ms, waiting ms)."""
        return self.cpu_ms, latency_ms - self.cpu_ms
```

`__post_init__` runs right after creation and rejects impossible bounds: `min_ms` negative or `max_ms` below `min_ms`, and `cpu_ms` outside 0 to `min_ms`. `latency_ms(fraction)` maps a number in [0, 1] onto the range (`min + (max - min) * fraction`; with 0.5 the middle). `split(latency_ms)` returns the pair (computing milliseconds, waiting milliseconds).

### `src/zero_downtime/application/__init__.py`

```text
"""Application: the use cases and the ports they need. Depends on the domain only."""
```

A one-line docstring: this layer holds the use cases and the ports and depends on the domain only.

### `src/zero_downtime/application/ports.py`

```text
"""What the use cases need from outside the domain."""

from __future__ import annotations

from typing import Protocol


class Randomness(Protocol):
    """A source of numbers in [0, 1); injected so tests are deterministic."""

    def fraction(self) -> float:
        """Return the next number in [0, 1)."""
        ...


class Waiter(Protocol):
    """Wait without computing (a sleep)."""

    def wait(self, milliseconds: float) -> None:
        """Block for the given time."""
        ...
```

A **port** is an interface the application needs from the outside. `Protocol` (from `typing`) defines a type by the methods it has; any object with these methods fits. `Randomness.fraction()` returns the next number in [0, 1); `Waiter.wait(milliseconds)` blocks without computing (a sleep). The body `...` means "no code here, this is only the shape". Injecting them makes tests deterministic.

```text
class Burner(Protocol):
    """Compute without waiting (a busy loop)."""

    def burn(self, milliseconds: float) -> None:
        """Keep one CPU busy for the given time."""
        ...
```

`Burner.burn(milliseconds)` keeps one CPU busy for the given time (a busy loop).

### `src/zero_downtime/application/use_cases.py`

```text
"""The service's behavior: do the work of one request, and warm up once before accepting traffic."""

from __future__ import annotations

from dataclasses import dataclass

from zero_downtime.application.ports import Burner, Randomness, Waiter
from zero_downtime.domain.profile import RequestProfile


@dataclass(frozen=True, slots=True)
class HandledRequest:
    """What one request cost, returned so callers and tests can see it."""

    latency_ms: float
    cpu_ms: float


class HandleRequest:
    """Spend a random latency inside the profile: compute for part of it, wait for the rest."""
```

`HandledRequest` is a frozen record of what one request cost: `latency_ms` and `cpu_ms`. `HandleRequest` begins: it spends a random latency inside the profile, computing for part of it and waiting for the rest.

```text
    def __init__(self, profile: RequestProfile, randomness: Randomness, burner: Burner, waiter: Waiter) -> None:
        """Bind the profile and the three injected capabilities."""
        self._profile = profile
        self._randomness = randomness
        self._burner = burner
        self._waiter = waiter

    def __call__(self) -> HandledRequest:
        """Do one request's work."""
        latency = self._profile.latency_ms(self._randomness.fraction())
        computing, waiting = self._profile.split(latency)
        self._burner.burn(computing)
        self._waiter.wait(waiting)
        return HandledRequest(latency_ms=latency, cpu_ms=computing)


class WarmUp:
    """The startup delay: the service is not ready, and must not be listening, until it has run."""
```

The constructor stores the profile and the three injected capabilities. Calling the object (`__call__`, so an instance is used like a function) draws a latency with `profile.latency_ms(randomness.fraction())`, splits it into computing and waiting, burns the CPU time, waits the rest and returns the record. `WarmUp` begins: the start-up delay, during which the service is not ready and must not be listening.

```text
    def __init__(self, waiter: Waiter, delay_ms: float) -> None:
        """Bind the waiter and the delay."""
        self._waiter = waiter
        self._delay_ms = delay_ms

    def __call__(self) -> float:
        """Wait out the delay and return how long it was, in milliseconds."""
        self._waiter.wait(self._delay_ms)
        return self._delay_ms
```

Its constructor stores a waiter and a delay; calling it waits that long and returns the delay in milliseconds.

### `src/zero_downtime/infrastructure/__init__.py`

```text
"""Infrastructure: adapters for the ports, environment settings and logging."""
```

A one-line docstring naming this layer's contents (settings, logging, clocks).

### `src/zero_downtime/infrastructure/settings.py`

```text
"""Configuration from the environment (twelve-factor), validated once at startup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from zero_downtime.domain.errors import InvalidProfileError
from zero_downtime.domain.profile import RequestProfile

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


class ConfigurationError(Exception):
    """An environment variable is missing or malformed. The message names the variable."""


@dataclass(frozen=True, slots=True)
class Settings:
    """What the service reads from its environment."""
```

The docstring names the "twelve-factor" idea (configuration lives in the environment). `Mapping` is the type of a read-only dictionary. `LOG_LEVELS` is a `frozenset` (immutable set) of accepted level names. `ConfigurationError` is raised when a variable is wrong and always names it. `Settings` is a frozen record of everything the service reads.

```text
    startup_delay_seconds: float = 1.0
    profile: RequestProfile = field(default_factory=RequestProfile)
    log_level: str = "INFO"
```

The fields: `startup_delay_seconds=1.0` (the warm-up before listening), `profile` (a `RequestProfile`, created fresh by `field(default_factory=RequestProfile)` for each `Settings`) and `log_level="INFO"`.

```text
    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """Build settings from a mapping such as os.environ, applying defaults and validating each value."""
        log_level = env.get("LOG_LEVEL", "INFO").upper()
        if log_level not in LOG_LEVELS:
            raise ConfigurationError(f"LOG_LEVEL must be one of {sorted(LOG_LEVELS)}, got {log_level!r}")
        defaults = RequestProfile()
        try:
            profile = RequestProfile(
                min_ms=_number(env, "LATENCY_MIN_MS", defaults.min_ms, 0, 60_000),
                max_ms=_number(env, "LATENCY_MAX_MS", defaults.max_ms, 0, 60_000),
                cpu_ms=_number(env, "CPU_WORK_MS", defaults.cpu_ms, 0, 60_000),
            )
        except InvalidProfileError as error:
            raise ConfigurationError(f"LATENCY_MIN_MS, LATENCY_MAX_MS or CPU_WORK_MS: {error}") from error
        return cls(
            startup_delay_seconds=_number(env, "STARTUP_DELAY_SECONDS", 1.0, 0, 600),
            profile=profile,
            log_level=log_level,
        )
```

`from_env` is a class method taking any mapping such as `os.environ`. It upper-cases and checks `LOG_LEVEL`, then builds a `RequestProfile` from `LATENCY_MIN_MS`, `LATENCY_MAX_MS` and `CPU_WORK_MS` (each read by `_number` with a default and limits from 0 to 60,000), turning an `InvalidProfileError` into a `ConfigurationError` that names the three variables, and finally returns the settings with `STARTUP_DELAY_SECONDS` (0 to 600).

```text
def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from None
    if not low <= value <= high:
        raise ConfigurationError(f"{name} must be between {low} and {high}, got {value}")
    return value
```

`_number` returns the default when the variable is absent; otherwise converts to a float (a failure becomes a `ConfigurationError` naming the variable, with `from None` hiding the noisy original) and checks it lies between `low` and `high`.

### `src/zero_downtime/infrastructure/logging.py`

```text
"""Structured logging: one JSON object per line on standard error, never a secret."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_RESERVED = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a record as {"ts", "level", "logger", "msg", ...extra fields}."""
```

The docstring says: one JSON object per line on standard error, never a secret. `_RESERVED` is the set of attribute names every log record already has (found by building an empty record with `logging.makeLogRecord({})`) plus `message` and `asctime`; any other attribute is an "extra" field a caller added. `JsonFormatter` subclasses the standard `logging.Formatter`.

```text
    def format(self, record: logging.LogRecord) -> str:
        """Return the record as one line of JSON; extra keyword fields become top-level keys."""
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({key: value for key, value in record.__dict__.items() if key not in _RESERVED})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    """Send every log record to standard error as JSON at the given level."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
```

`format` builds a dictionary with `ts` (creation time in UTC, ISO text, millisecond precision), `level`, `logger` and `msg` (message with arguments filled in), adds the extra fields as top-level keys and, for a record carrying an exception, the formatted traceback as `exc`. `json.dumps(entry, default=str)` produces one JSON line (values JSON does not know become text). `configure_logging(level)` makes a handler writing to standard error with that formatter, replaces the root logger's handlers with it and sets the level.

### `src/zero_downtime/infrastructure/runtime.py`

```text
"""Real clocks and random numbers behind the ports."""

from __future__ import annotations

import random
import time
from collections.abc import Callable


class SystemRandomness:
    """Uniform random numbers from the standard library's generator."""

    def __init__(self, seed: int | None = None) -> None:
        """Seed the generator; leave seed unset in production."""
        self._random = random.Random(seed)

    def fraction(self) -> float:
        """Return the next number in [0, 1)."""
        return self._random.random()
```

The real implementations of the ports. `SystemRandomness` wraps `random.Random(seed)` (leave the seed unset in production); `fraction()` returns `random()`, a number in [0, 1).

```text
class SleepWaiter:
    """Wait by sleeping, which uses no CPU."""

    def wait(self, milliseconds: float) -> None:
        """Sleep for the given time."""
        if milliseconds > 0:
            time.sleep(milliseconds / 1000)


class SpinBurner:
    """Burn CPU by hashing in a loop until a deadline on a monotonic clock."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        """Take the clock as a parameter so a test can drive it without burning real time."""
        self._clock = clock

    def burn(self, milliseconds: float) -> None:
        """Compute for the given time."""
        deadline = self._clock() + milliseconds / 1000
        value = 0
        while self._clock() < deadline:
            value = (value * 31 + 7) % 1_000_003
```

`SleepWaiter.wait` calls `time.sleep` when the time is positive (sleeping uses no CPU). `SpinBurner` burns CPU: its constructor takes a clock function (default `time.perf_counter`, a high-resolution timer) so a test can drive it without real time; `burn` computes a deadline and loops `value = (value * 31 + 7) % 1_000_003` until the clock passes it. The arithmetic is meaningless; it exists only to keep one CPU core busy.

### `src/loadgen/__init__.py`

```text
"""A fixed-rate load generator that counts every failed request by class and keeps latency percentiles."""

__version__ = "0.1.0"
```

The docstring: a fixed-rate load generator that counts every failed request by class and keeps latency percentiles. `__version__ = "0.1.0"`.

### `src/loadgen/domain/__init__.py`

```text
"""Domain: rules and values with no I/O. Depends on nothing outside the standard library."""
```

A one-line docstring: the load generator's domain (outcomes, pacing, statistics) with no outside dependencies.

### `src/loadgen/domain/errors.py`

```text
"""Named errors for the load generator."""


class LoadgenError(Exception):
    """Base class of every error this program raises on purpose."""


class InvalidRateError(LoadgenError):
    """A request rate outside the supported range."""
```

`LoadgenError` is the base class of every error the program raises on purpose; `InvalidRateError` is a request rate outside the supported range.

### `src/loadgen/domain/outcome.py`

```text
"""What can happen to one request, and the measured sample the counters are built from."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    """The class of one finished request. Everything except OK is an error and is counted on its own."""

    OK = "ok"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    HTTP_5XX = "http_5xx"
    HTTP_OTHER = "http_other"

    @property
    def is_error(self) -> bool:
        """True for every outcome except OK."""
        return self is not Outcome.OK
```

`Outcome` is a `StrEnum` (an enumeration whose members are also strings): `OK`, `CONNECTION`, `TIMEOUT`, `HTTP_5XX` and `HTTP_OTHER`, with the text values `"ok"`, `"connection"` and so on, which become the JSON keys. The property `is_error` is true for everything except `OK`.

```text
@dataclass(frozen=True, slots=True)
class Sample:
    """One request's outcome and how long it took, in seconds."""

    outcome: Outcome
    latency_seconds: float
```

`Sample` is a frozen record of one finished request: its outcome and how long it took in seconds.

### `src/loadgen/application/__init__.py`

```text
"""Application: the use cases and the ports they need. Depends on the domain only."""
```

A one-line docstring: the runner and the ports it needs.

### `src/loadgen/application/ports.py`

```text
"""What the runner needs from outside the domain."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from loadgen.domain.outcome import Sample


class Requester(Protocol):
    """Sends one request to the target and reports how it went. Never raises."""

    def request(self) -> Sample:
        """Send one request."""
        ...


class Clock(Protocol):
    """A monotonic clock and a sleep, injected so the runner is testable without real time."""
```

`Requester.request()` sends one request and returns a `Sample`; its docstring says it never raises (failures are samples). `Clock` is introduced: a monotonic clock plus a sleep, injected so the runner is testable without real time.

```text
    def now(self) -> float:
        """Seconds on a monotonic clock."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block for the given time."""
        ...


class Executor(Protocol):
    """Runs a function on another thread; a thread pool in production, an immediate call in tests."""

    def submit(self, function: Callable[[], None]) -> object:
        """Schedule the function."""
        ...

    def shutdown(self) -> None:
        """Wait for scheduled functions and release threads."""
        ...
```

`Clock.now()` returns seconds on a monotonic clock (one that never jumps backward) and `Clock.sleep(seconds)` blocks. `Executor.submit(function)` runs a function on another thread (a thread pool in production, an immediate call in tests) and `Executor.shutdown()` waits for scheduled work and releases the threads.

### `src/loadgen/infrastructure/__init__.py`

```text
"""Infrastructure: adapters for the ports, environment settings and logging."""
```

A one-line docstring naming the layer: settings, logging, the real clock and thread pool, the HTTP client.

### `src/loadgen/infrastructure/settings.py`

```text
"""Configuration from the environment, validated once at startup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from loadgen.domain.errors import InvalidRateError
from loadgen.domain.pacer import Pacer

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


class ConfigurationError(Exception):
    """An environment variable is missing or malformed. The message names the variable."""


@dataclass(frozen=True, slots=True)
class Settings:
    """What the load generator reads from its environment."""
```

Imports `urlsplit` (splits a URL into parts), the rate error and the `Pacer` (used only to validate the rate). Same pattern as the service's settings: `LOG_LEVELS`, `ConfigurationError`, a frozen `Settings` record.

```text
    target_url: str
    rate_per_second: float = 100.0
    timeout_seconds: float = 2.0
    max_in_flight: int = 128
    control_port: int = 9100
    log_level: str = "INFO"
```

Fields: `target_url` (required), `rate_per_second=100.0`, `timeout_seconds=2.0` (a request slower than this is a `timeout` error), `max_in_flight=128` (how many requests may be outstanding), `control_port=9100` and `log_level`.

```text
    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """Build settings from a mapping such as os.environ; TARGET_URL is required."""
        target = env.get("TARGET_URL", "").strip()
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            raise ConfigurationError(f"TARGET_URL must be an http:// URL with a host, got {target!r}")
        log_level = env.get("LOG_LEVEL", "INFO").upper()
        if log_level not in LOG_LEVELS:
            raise ConfigurationError(f"LOG_LEVEL must be one of {sorted(LOG_LEVELS)}, got {log_level!r}")
        rate = _number(env, "RATE_PER_SECOND", 100.0)
        try:
            Pacer(rate, 0.0)
        except InvalidRateError as error:
            raise ConfigurationError(f"RATE_PER_SECOND: {error}") from error
        return cls(
            target_url=target,
            rate_per_second=rate,
            timeout_seconds=_bounded(_number(env, "TIMEOUT_SECONDS", 2.0), "TIMEOUT_SECONDS", 0.1, 60),
            max_in_flight=int(_bounded(_number(env, "MAX_IN_FLIGHT", 128), "MAX_IN_FLIGHT", 1, 10_000)),
            control_port=int(_bounded(_number(env, "CONTROL_PORT", 9100), "CONTROL_PORT", 1, 65_535)),
            log_level=log_level,
        )
```

`from_env` requires `TARGET_URL` to be an `http://` URL with a host, checks the level, reads `RATE_PER_SECOND` and validates it by building a throwaway `Pacer` (an invalid rate becomes a `ConfigurationError`), and returns the settings with bounded numbers: timeout 0.1 to 60 s, in-flight 1 to 10,000, port 1 to 65,535.

```text
def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from None


def _bounded(value: float, name: str, low: float, high: float) -> float:
    if not low <= value <= high:
        raise ConfigurationError(f"{name} must be between {low} and {high}, got {value}")
    return value
```

`_number` reads a float with a default and a clear error; `_bounded` checks a value lies between two limits and names the variable otherwise.

### `src/loadgen/infrastructure/logging.py`

```text
"""Structured logging: one JSON object per line on standard error, never a secret."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_RESERVED = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a record as {"ts", "level", "logger", "msg", ...extra fields}."""
```

Identical in purpose to the service's logging module: `_RESERVED` and the `JsonFormatter` class begin.

```text
    def format(self, record: logging.LogRecord) -> str:
        """Return the record as one line of JSON; extra keyword fields become top-level keys."""
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({key: value for key, value in record.__dict__.items() if key not in _RESERVED})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    """Send every log record to standard error as JSON at the given level."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
```

`format` and `configure_logging` as in the service: one JSON line per record on standard error, extras as top-level keys, exceptions in `exc`.

### `src/loadgen/infrastructure/runtime.py`

```text
"""Real clock and a thread pool behind the runner's ports."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor


class SystemClock:
    """The monotonic clock and time.sleep."""

    def now(self) -> float:
        """Seconds on the monotonic clock."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Block for the given time."""
        time.sleep(seconds)
```

`SystemClock` is the real clock: `now()` returns `time.monotonic()` and `sleep` calls `time.sleep`.

```text
class PoolExecutor:
    """A fixed thread pool."""

    def __init__(self, workers: int) -> None:
        """Create the pool."""
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sender")

    def submit(self, function: Callable[[], None]) -> Future[None]:
        """Schedule the function on a pool thread."""
        return self._pool.submit(function)

    def shutdown(self) -> None:
        """Wait for scheduled work and release the threads."""
        self._pool.shutdown(wait=True)
```

`PoolExecutor` wraps a `ThreadPoolExecutor` (Python's fixed pool of worker threads) with `max_workers` and a thread-name prefix `sender`; `submit` schedules a function on a pool thread and returns a `Future` (a handle to the pending result); `shutdown` waits for scheduled work (`wait=True`) and releases the threads.

### `src/zero_downtime/interfaces/__init__.py`

```text
"""Interfaces: transports and composition roots. Nothing depends on this layer."""
```

A one-line docstring stating the layer's rule: the interfaces layer (HTTP and the composition root) is the outermost one, so nothing else imports from it. The file makes the folder a package.

### `src/loadgen/interfaces/__init__.py`

```text
"""Interfaces: transports and composition roots. Nothing depends on this layer."""
```

A one-line docstring stating the same rule for the load generator: nothing depends on the interfaces layer (the control API and the program). The file makes the folder a package.


## 6. Try it yourself

All commands run from the repository folder after `make install`.

```bash
.venv/bin/python -c "from zero_downtime.domain.profile import RequestProfile; p = RequestProfile(); print(p, p.latency_ms(0.5), p.split(125.0))"
```

Prints `RequestProfile(min_ms=50.0, max_ms=200.0, cpu_ms=2.0) 125.0 (2.0, 123.0)`: the middle of the latency range, split into 2 ms of computing and 123 ms of waiting.

```bash
.venv/bin/python -c "from zero_downtime.domain.profile import RequestProfile; RequestProfile(min_ms=10, max_ms=5)" 2>&1 | tail -1
```

Ends with `InvalidProfileError: need 0 <= min_ms <= max_ms, got 10 and 5`.

```bash
.venv/bin/python -c "
from zero_downtime.infrastructure.settings import Settings
print(Settings.from_env({'STARTUP_DELAY_SECONDS': '3', 'CPU_WORK_MS': '5'}))"
```

Prints a `Settings(...)` line with `startup_delay_seconds=3.0` and `cpu_ms=5.0`.

```bash
.venv/bin/python -m pytest tests/unit -q
```

Runs the unit tests and prints a line such as `NN passed`.

## 7. Mistakes people make

- **Changing a value that should not change.** `@dataclass(frozen=True)` makes the profile, settings and snapshots read-only.
- **Measuring with the real clock in tests.** Time, randomness and sleeping are injected through small interfaces (`Randomness`, `Waiter`, `Burner`, `Clock`), so tests are fast and exact.
- **Sharing mutable counters between threads without a lock.** `StatsWindow` and `LoadRunner` guard their state with `threading.Lock`.
- **Swallowing errors.** The code catches only the named errors it expects and turns each into another named error or an outcome.
- **Putting settings in the code.** Every setting comes from the environment and is checked once at start-up.

## 8. Check yourself

1. What is the difference between a module and a package? *A module is one `.py` file; a package is a folder of modules with an `__init__.py`.*
2. Why does `HandleRequest` receive a `Randomness`, a `Burner` and a `Waiter`? *So tests can replace the real random numbers, busy loop and sleep with fakes.*
3. Why is part of each request real computation? *CPU utilization is what the autoscaler measures; waiting alone would never raise it.*
4. What does `from None` do in `raise ConfigurationError(...) from None`? *It hides the original `ValueError` from the traceback because the new message says everything.*
5. Why does the load generator count `skipped`? *If it could not send a request on time, its verdict would be invalid; the count makes that visible.*

## 9. Further reading

- Python 3.13 documentation: https://docs.python.org/3.13/
- The tutorial: https://docs.python.org/3.13/tutorial/index.html
- `dataclasses`: https://docs.python.org/3.13/library/dataclasses.html
- `typing.Protocol`: https://docs.python.org/3.13/library/typing.html#typing.Protocol
- `logging`: https://docs.python.org/3.13/library/logging.html
