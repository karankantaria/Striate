"""binviz — binary visualiser & triage tool."""

#: The distribution version, and the only place it is written down —
#: `pyproject.toml` reads it from here via `[tool.setuptools.dynamic]`.
#: It used to be declared here *and* in pyproject, and the two had already
#: drifted (0.0.1 against a tool reporting 0.0.3).
#:
#: Deliberately **not** unified with `cache.TOOL_VERSION`. That one is the
#: analysis-schema version and it feeds `params_fingerprint`, so changing it
#: invalidates every cached analysis on every install. A release that only
#: changes the UI must not throw away a 5 GiB cache.
__version__ = "0.0.3"
