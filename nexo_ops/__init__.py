"""NEXO autonomous support operations on native Hermes.

Thin layer between the reviewed ``nexo_support_mcp`` ledger/adapters and native Hermes
facilities (Kanban tasks, script-only cron, MCP, gateway plugin). Kanban owns run state;
the ledger owns business state and evidence.
"""

__version__ = "0.1.0"
