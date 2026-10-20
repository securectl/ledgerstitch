# SPDX-License-Identifier: AGPL-3.0-only
"""AI error base class shared by the core and add-ons (add-ons subclass it)."""
from __future__ import annotations


class AIError(Exception):
    """User-presentable AI failure (message never contains descriptors)."""
