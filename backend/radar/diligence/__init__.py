"""Technical diligence: what a company claims, what public evidence supports or contradicts it, what can be
tested, and what stays unknown.

Two halves, deliberately separate:

* **Collection** (``collect.py``) talks to the network. Everything it fetches is untrusted data: SSRF-guarded,
  robots-respecting, size-capped, time-budgeted, never executed, cloned or imported.
* **Analysis** (``analyze.py`` and the modules it calls) is pure and deterministic over the collected
  artifacts. Verification statuses come from the documented rules in ``rules.py`` — never from a model.

A run can be captured to a file (``capture.py``) and replayed offline with identical results. The output is
an evidence assessment, never an investment recommendation. Method: ``docs/DILIGENCE_METHOD.md``.
"""

from __future__ import annotations

METHOD_VERSION = "dil-2026.11-2"
