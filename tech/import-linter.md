# import-linter

## 1. What it is

import-linter is a Python tool that checks **which modules are allowed to import which**. You write rules ("contracts") in the configuration; it reads the source, builds a graph of who imports whom, and fails if a rule is broken. The command is `lint-imports`. This repository uses it to enforce its layered design: the code is split into four layers, and an inner layer must never depend on an outer one.

## 2. Why this repository uses it

A layered design only stays layered if something stops a shortcut from creeping in at 5 p.m. on a Friday. Here the rules are, for each of the two programs: the web layer may use the infrastructure layer, which may use the use-case layer, which may use the domain layer, but never the other way. The domain and application layers must know nothing about Flask or gunicorn. And the web service and the load generator share no code at all. If someone writes `import flask` in a domain module, `make lint` fails.

## 3. Where it appears

| File | Role |
| --- | --- |
| `pyproject.toml` | The `[tool.importlinter]` table and two contracts. |
| `Makefile` | Target `lint` runs `lint-imports`. |
| `src/zero_downtime/`, `src/loadgen/` | The packages it inspects. |

## 4. How it works underneath

import-linter uses a library called Grimp to read every `.py` file in the root package, find each `import` statement and make a directed graph (an arrow from the importing module to the imported one). A **layers** contract lists packages from highest to lowest; it fails if a lower package imports a higher one, directly or through a chain. A **forbidden** contract lists source modules and modules that they must not import, directly or indirectly. With `include_external_packages = true` the graph also contains third-party packages, which is what allows the third contract to name `flask` and `gunicorn`.

## 5. Walkthrough of this repository's use

### `pyproject.toml`

```toml
[[tool.importlinter.contracts]]
name = "Service layers: interfaces -> infrastructure -> application -> domain, never upward"
type = "layers"
layers = [
    "zero_downtime.interfaces",
    "zero_downtime.infrastructure",
    "zero_downtime.application",
    "zero_downtime.domain",
]

[[tool.importlinter.contracts]]
name = "Load generator layers: interfaces -> infrastructure -> application -> domain, never upward"
type = "layers"
layers = [
    "loadgen.interfaces",
    "loadgen.infrastructure",
    "loadgen.application",
    "loadgen.domain",
]
```

The first `[[tool.importlinter.contracts]]` has a `name`, `type = "layers"` and the list `layers`, ordered from outermost (`zero_downtime.interfaces`, the web and start-up code) through `infrastructure` and `application` to `domain` (the pure rules). Imports may only point downward. The second contract is the same rule for the load generator's four layers.

```toml
[[tool.importlinter.contracts]]
name = "Domain and application know nothing about Flask"
type = "forbidden"
source_modules = ["zero_downtime.domain", "zero_downtime.application", "loadgen.domain", "loadgen.application"]
forbidden_modules = ["flask", "gunicorn"]

[[tool.importlinter.contracts]]
name = "The service and the load generator share no code"
type = "independence"
modules = ["zero_downtime", "loadgen"]
```

The third contract, `type = "forbidden"`, says the domain and application layers of both programs (`source_modules`) must not import `flask` or `gunicorn` (`forbidden_modules`), directly or through another module. The fourth, `type = "independence"`, says the two packages `zero_downtime` and `loadgen` share no code at all: neither may import the other, which is why the load generator image needs nothing but its own folder.


## 6. Try it yourself

```bash
.venv/bin/lint-imports
```

Prints the four contracts with `KEPT` and `Contracts: 4 kept, 0 broken.`

```bash
echo "import flask" >> src/zero_downtime/domain/profile.py && .venv/bin/lint-imports; git checkout src/zero_downtime/domain/profile.py
```

The third contract reports `BROKEN` and shows the import chain; the `git checkout` undoes the experiment.

## 7. Mistakes people make

- **Forgetting indirect imports.** A forbidden import through a middle module still counts; the tool reports the whole chain.
- **Listing layers in the wrong order.** The list goes from outermost to innermost.
- **Leaving `include_external_packages` off** and wondering why a rule about `flask` never fires.

## 8. Check yourself

1. Which layer may import which? *Each layer may import only the ones below it in the list.*
2. Why can the domain not import `flask`? *The third contract forbids it for the domain and application packages.*
3. Is a chain `application -> infrastructure -> flask` caught? *Yes, forbidden contracts check indirect imports.*
4. What command runs the check? *`lint-imports`, via `make lint`.*
5. What does the root package setting do? *It tells the tool which package to analyse.*

## 9. Further reading

- import-linter documentation: https://import-linter.readthedocs.io/en/stable/
- Contract types: https://import-linter.readthedocs.io/en/stable/contract_types/
- Grimp: https://grimp.readthedocs.io/en/stable/
