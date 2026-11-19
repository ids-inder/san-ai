"""Clinic attendance system.

Pulls punch data from biometric-machine aggregators (eTimeOffice and CSV
exports today, other adapters later), stores it idempotently, computes
attendance against shift rules, and serves a dashboard.

Self-contained: it shares no state, config or database with the sanai
package that lives beside it in this repo.
"""
__all__ = ["__version__"]
__version__ = "0.1.0"
