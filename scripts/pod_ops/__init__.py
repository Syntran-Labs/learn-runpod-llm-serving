"""Gated automation for RunPod pod lifecycle (create/terminate) and progress display.

Every action here that spends real money (create, terminate) requires an explicit
--yes flag AND an interactive typed confirmation. Nothing in this package runs
automatically; a human must invoke each script by hand.
"""
