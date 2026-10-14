"""Operator tools for the alignment component.

These are explicit, operator-run utilities (e.g. one-time model acquisition).
Nothing here runs at package import or server startup, and importing this
package pulls in no heavy dependency — each tool imports torch/torchaudio
lazily inside its own entry point.
"""
