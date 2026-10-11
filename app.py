"""invoice-to-sheet — Gradio app (Milestone 5). Placeholder until the UI is built; see CLAUDE.md §9."""

from __future__ import annotations

import gradio as gr

from invoice_to_sheet import __version__

with gr.Blocks(title="invoice-to-sheet") as demo:
    gr.Markdown(
        f"# invoice-to-sheet (v{__version__})\n\nInvoices and receipts → validated rows in a spreadsheet; doubtful ones go to a review queue.\n\n"
        "*The web UI arrives in Milestone 5. Until then: `python scripts/run_folder.py <folder>`.*"
    )

if __name__ == "__main__":
    demo.launch()
