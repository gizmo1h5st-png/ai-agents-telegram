# DEPRECATED / LEGACY FILE
# This file belongs to the old single-bot architecture.
# The current system uses the multi-bot engine in app/multibot/engine.py
# 
# DO NOT import this file in production code.
# It is kept only for reference / backward compatibility during transition.

from aiogram import Router
import logging

logger = logging.getLogger(__name__)
router = Router()

# All handlers are disabled.
# The real logic lives in the multi-bot mode (6 separate bots).

@router.message()
async def legacy_disabled(message):
    logger.warning("Legacy single-bot handler called — this mode is deprecated.")
    # Do nothing
    pass
